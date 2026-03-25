from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
import urllib.request
from argparse import Namespace
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from ledger.auth import auth_enabled, get_bearer_token, resolve_principal
from ledger.event_store import EventStore
from ledger.metrics import (
    build_event_throughput_snapshot,
    build_manual_review_backlog_snapshot,
    compute_stream_sizes,
    get_replay_progress,
)
from ledger.mcp_server import MCPRuntime, create_runtime
from ledger.registry.client import ApplicantRegistryClient
from ledger.schema.events import (
    ApplicationSubmitted,
    DocumentAdded,
    DocumentFormat,
    DocumentType,
    DocumentUploadRequested,
    DocumentUploaded,
    LoanPurpose,
    PackageCreated,
)
from scripts.run_pipeline import run_pipeline


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        backend = await _build_backend()
        app.state.backend = backend
        await backend.sync()
        try:
            yield
        finally:
            await backend.close()

    app = FastAPI(title="The Ledger API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*", "Authorization", "X-API-Key"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/health/ready")
    async def readiness(request: Request) -> dict[str, Any]:
        backend = getattr(request.app.state, "backend", None)
        if backend is None:
            return {"ok": False, "ready": False, "reason": "Backend not initialized"}
        return {
            "ok": True,
            "ready": True,
            "store": backend.store.__class__.__name__,
            "lag": backend.runtime.get_lag_snapshot(),
        }

    @app.get("/applications")
    async def applications(request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_applications())

    @app.get("/applications/{application_id}")
    async def application_detail(application_id: str, request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        row = await backend.get_application_detail(application_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return jsonable_encoder(row)

    @app.get("/documents/{document_path:path}")
    async def download_document(document_path: str, request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        docs_root = _documents_root().resolve()
        target = (docs_root / document_path).resolve()
        try:
            target.relative_to(docs_root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

        if not target.is_file():
            raise HTTPException(status_code=404, detail="Document not found")

        return FileResponse(path=target, filename=target.name)

    @app.get("/timeline")
    async def timeline(request: Request, application_id: str | None = None) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_timeline(application_id))

    @app.get("/review-queue")
    async def review_queue(request: Request) -> Any:
        _require_roles(request, {"reviewer", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_review_queue())

    @app.get("/review-queue/metrics")
    async def review_queue_metrics(request: Request) -> Any:
        _require_roles(request, {"reviewer", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_review_queue_metrics())

    @app.get("/compliance")
    async def compliance(request: Request) -> Any:
        _require_roles(request, {"compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_compliance_rows())

    @app.get("/agents")
    async def agents(request: Request) -> Any:
        _require_roles(request, {"analyst", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_agent_performance())

    @app.get("/projections/lag")
    async def projection_lag(request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        lag_snapshot = backend.runtime.get_lag_snapshot()
        return jsonable_encoder(
            {
                name: {
                    "positionsBehind": lag.get("positions_behind", 0),
                    "millis": lag.get("millis", 0),
                }
                for name, lag in lag_snapshot.items()
            }
        )

    @app.get("/replay/progress")
    async def replay_progress(request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "admin"})
        backend = _get_backend(request)
        return jsonable_encoder(await get_replay_progress(backend.store))

    @app.get("/metrics/events")
    async def event_throughput(request: Request, window_minutes: int = 60, bucket_minutes: int = 5) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        return jsonable_encoder(
            await build_event_throughput_snapshot(
                backend.store,
                window_minutes=window_minutes,
                bucket_minutes=bucket_minutes,
            )
        )

    @app.get("/metrics/streams")
    async def stream_sizes(request: Request) -> Any:
        _require_roles(request, {"viewer", "analyst", "reviewer", "compliance", "auditor", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await compute_stream_sizes(backend.store))

    @app.get("/agents/stuck-sessions")
    async def stuck_sessions(request: Request, timeout_ms: int = 600000) -> Any:
        _require_roles(request, {"analyst", "admin"})
        backend = _get_backend(request)
        await backend.sync()
        return jsonable_encoder(await backend.list_stuck_agent_sessions(timeout_ms))

    @app.post("/refresh")
    async def refresh(request: Request) -> Any:
        _require_roles(request, {"admin"})
        backend = _get_backend(request)
        return jsonable_encoder(await backend.sync())

    @app.post("/applications/intake")
    async def intake_application(
        request: Request,
        company_id: str = Form(...),
        application_id: str | None = Form(None),
        requested_amount: float | None = Form(None),
        loan_purpose: str = Form("working_capital"),
        application_proposal: UploadFile | None = File(None),
        income_statement: UploadFile | None = File(None),
        balance_sheet: UploadFile | None = File(None),
        bank_statements: UploadFile | None = File(None),
        application_proposal_url: str | None = Form(None),
        income_statement_url: str | None = Form(None),
        balance_sheet_url: str | None = Form(None),
        bank_statements_url: str | None = Form(None),
    ) -> Any:
        _require_roles(request, {"analyst", "reviewer", "compliance", "admin"})
        backend = _get_backend(request)
        registry = backend.registry
        if registry is None:
            raise HTTPException(status_code=503, detail="Applicant registry is not available")

        company = await registry.get_company(company_id)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Company '{company_id}' not found")

        loan_stream = f"loan-{application_id or await _next_application_id(backend)}"
        application_id = loan_stream.replace("loan-", "", 1)
        existing_version = await backend.store.stream_version(loan_stream)
        if existing_version >= 0:
            raise HTTPException(status_code=409, detail=f"Application '{application_id}' already exists")

        docs_root = _documents_root()
        application_dir = docs_root / company_id / application_id
        application_dir.mkdir(parents=True, exist_ok=True)

        uploads: list[tuple[str, UploadFile | None, str | None, DocumentType, int | None]] = [
            ("application_proposal", application_proposal, application_proposal_url, DocumentType.APPLICATION_PROPOSAL, None),
            ("income_statement", income_statement, income_statement_url, DocumentType.INCOME_STATEMENT, 2024),
            ("balance_sheet", balance_sheet, balance_sheet_url, DocumentType.BALANCE_SHEET, 2024),
        ]
        if bank_statements is not None:
            uploads.append(("bank_statements", bank_statements, bank_statements_url, DocumentType.BANK_STATEMENTS, None))
        elif bank_statements_url:
            uploads.append(("bank_statements", None, bank_statements_url, DocumentType.BANK_STATEMENTS, None))

        document_records: list[dict[str, Any]] = []
        for slot_name, upload, source_url, document_type, fiscal_year in uploads:
            if upload is None and not source_url:
                raise HTTPException(status_code=400, detail=f"Missing file or URL for '{slot_name}'")
            record = await _persist_document_source(
                upload=upload,
                source_url=source_url,
                application_dir=application_dir,
                company_id=company_id,
                application_id=application_id,
                slot_name=slot_name,
                document_type=document_type,
                fiscal_year=fiscal_year,
            )
            document_records.append(record)

        now = _now()
        submission_amount = requested_amount if requested_amount is not None else 250_000.0
        loan_purpose_value = str(loan_purpose or LoanPurpose.WORKING_CAPITAL.value)
        try:
            purpose = LoanPurpose(loan_purpose_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid loan purpose '{loan_purpose_value}'") from exc

        loan_events = [
            ApplicationSubmitted(
                application_id=application_id,
                applicant_id=company_id,
                requested_amount_usd=submission_amount,
                loan_purpose=purpose,
                loan_term_months=36,
                submission_channel="web-upload",
                contact_email=f"{company_id.lower()}@synthetic.example",
                contact_name=str(company.name or company_id),
                submitted_at=now,
                application_reference=application_id,
            ).to_store_dict(),
            DocumentUploadRequested(
                application_id=application_id,
                required_document_types=[
                    DocumentType.APPLICATION_PROPOSAL,
                    DocumentType.INCOME_STATEMENT,
                    DocumentType.BALANCE_SHEET,
                ],
                deadline=now,
                requested_by="browser-upload",
            ).to_store_dict(),
        ]

        for record in document_records:
            loan_events.append(
                DocumentUploaded(
                    application_id=application_id,
                    document_id=record["document_id"],
                    document_type=DocumentType(record["document_type"]),
                    document_format=DocumentFormat(record["document_format"]),
                    filename=record["filename"],
                    file_path=record["file_path"],
                    file_size_bytes=record["file_size_bytes"],
                    file_hash=record["file_hash"],
                    fiscal_year=record["fiscal_year"],
                    uploaded_at=now,
                    uploaded_by="browser-upload",
                ).to_store_dict()
            )

        docpkg_events = [
            PackageCreated(
                package_id=application_id,
                application_id=application_id,
                required_documents=[
                    DocumentType.APPLICATION_PROPOSAL,
                    DocumentType.INCOME_STATEMENT,
                    DocumentType.BALANCE_SHEET,
                ],
                created_at=now,
            ).to_store_dict(),
        ]
        for record in document_records:
            docpkg_events.append(
                DocumentAdded(
                    package_id=application_id,
                    document_id=record["document_id"],
                    document_type=DocumentType(record["document_type"]),
                    document_format=DocumentFormat(record["document_format"]),
                    file_hash=record["file_hash"],
                    added_at=now,
                ).to_store_dict()
            )

        await backend.store.append(loan_stream, loan_events, expected_version=-1)
        await backend.store.append(f"docpkg-{application_id}", docpkg_events, expected_version=-1)

        db_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DB_URL")
        if not db_url:
            raise HTTPException(status_code=500, detail="DATABASE_URL or TEST_DB_URL is not configured")

        pipeline_args = Namespace(
            app=application_id,
            phase="all",
            db_url=db_url,
            llm="mock",
            model="deepseek-r1:8b",
        )
        pipeline_result = await run_pipeline(pipeline_args)
        if pipeline_result != 0:
            raise HTTPException(status_code=500, detail=f"Pipeline failed for application '{application_id}'")

        await backend.sync()
        detail = await backend.get_application_detail(application_id)
        return jsonable_encoder(
            {
                "ok": True,
                "application_id": application_id,
                "company_id": company_id,
                "detail": detail,
                "documents": document_records,
                "pipeline_result": pipeline_result,
            }
        )

    return app


@dataclass
class Backend:
    runtime: MCPRuntime
    registry: ApplicantRegistryClient | None

    async def sync(self) -> dict[str, Any]:
        return await self.runtime.sync_projections(max_rounds=32)

    async def close(self) -> None:
        await self.runtime.close()

    @property
    def store(self):
        return self.runtime.store

    async def list_applications(self) -> list[dict[str, Any]]:
        summaries = self.runtime.application_summary.all_rows()
        output: list[dict[str, Any]] = []
        for summary in summaries:
            detail = await self.get_application_detail(str(summary["application_id"]))
            if detail is not None:
                output.append(detail)
        return output

    async def get_application_detail(self, application_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def list_timeline(self, application_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_review_queue(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_review_queue_metrics(self) -> dict[str, Any]:
        raise NotImplementedError

    async def list_compliance_rows(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_agent_performance(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_stuck_agent_sessions(self, timeout_ms: int = 600000) -> list[dict[str, Any]]:
        raise NotImplementedError


async def _build_backend() -> Backend:
    runtime = create_runtime()
    await runtime.start()
    registry = None
    if isinstance(runtime.store, EventStore) and hasattr(runtime.store, "_require_pool"):
        try:
            registry = ApplicantRegistryClient(runtime.store._require_pool())
        except Exception:
            registry = None
    backend = Backend(runtime=runtime, registry=registry)
    _attach_backend_methods(backend)
    return backend


def _documents_root() -> Path:
    value = os.environ.get("DOCUMENTS_DIR", "documents")
    return Path(value).expanduser().resolve()


def _document_download_url(file_path: str) -> str | None:
    docs_root = _documents_root().resolve()
    target = Path(file_path).expanduser().resolve()
    try:
        relative_path = target.relative_to(docs_root)
    except ValueError:
        return None

    return "/documents/" + "/".join(relative_path.parts)


async def _next_application_id(backend: Backend) -> str:
    existing: set[str] = set()
    async for event in backend.store.load_all(from_position=0, event_types=["ApplicationSubmitted"]):
        stream_id = str(event.get("stream_id") or "")
        if stream_id.startswith("loan-"):
            existing.add(stream_id.replace("loan-", "", 1))

    index = 30
    while True:
        candidate = f"APEX-{index:04d}"
        if candidate not in existing:
            return candidate
        index += 1


def _infer_document_format(filename: str) -> DocumentFormat:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return DocumentFormat.PDF
    if suffix in {".xlsx", ".xls"}:
        return DocumentFormat.XLSX
    if suffix == ".csv":
        return DocumentFormat.CSV
    raise HTTPException(status_code=400, detail=f"Unsupported document format '{suffix or 'unknown'}'")


async def _persist_document_source(
    *,
    upload: UploadFile | None,
    source_url: str | None,
    application_dir: Path,
    company_id: str,
    application_id: str,
    slot_name: str,
    document_type: DocumentType,
    fiscal_year: int | None,
) -> dict[str, Any]:
    if upload is not None:
        filename = Path(upload.filename or f"{slot_name}.bin").name
        contents = await upload.read()
    else:
        filename, contents = await _fetch_remote_document(source_url or "", slot_name)

    document_format = _infer_document_format(filename)
    if not contents:
        raise HTTPException(status_code=400, detail=f"Uploaded file '{filename}' is empty")

    file_hash = hashlib.sha256(contents).hexdigest()
    safe_name = f"{slot_name}_{filename}"
    destination = application_dir / safe_name
    destination.write_bytes(contents)
    return {
        "document_id": f"doc-{hashlib.sha1(f'{application_id}:{slot_name}:{filename}'.encode('utf-8')).hexdigest()[:12]}",
        "document_type": document_type.value,
        "document_format": document_format.value,
        "filename": filename,
        "file_path": str(destination),
        "download_url": _document_download_url(str(destination)),
        "file_size_bytes": len(contents),
        "file_hash": file_hash,
        "fiscal_year": fiscal_year,
        "company_id": company_id,
    }


async def _fetch_remote_document(source_url: str, slot_name: str) -> tuple[str, bytes]:
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail=f"Unsupported URL scheme for '{slot_name}'")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"Invalid URL for '{slot_name}'")
    _reject_private_hostname(parsed.hostname)

    def _download() -> tuple[str, bytes]:
        request = urllib.request.Request(source_url, headers={"User-Agent": "the-ledger/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec: controlled by hostname validation
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            content_disposition = response.headers.get("Content-Disposition", "")
            filename = _filename_from_headers(content_disposition, parsed.path, slot_name, content_type)
            data = response.read(25 * 1024 * 1024 + 1)
            if len(data) > 25 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"Remote file for '{slot_name}' is too large")
            return filename, data

    try:
        return await asyncio.to_thread(_download)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to fetch remote document for '{slot_name}': {exc}") from exc


def _filename_from_headers(content_disposition: str, path: str, slot_name: str, content_type: str) -> str:
    if content_disposition:
        for part in content_disposition.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                candidate = part.split("=", 1)[1].strip().strip('"').strip("'")
                if candidate:
                    return Path(candidate).name

    path_name = Path(path).name
    if path_name:
        return path_name

    suffix_map = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "text/csv": ".csv",
    }
    suffix = suffix_map.get(content_type, ".bin")
    return f"{slot_name}{suffix}"


def _reject_private_hostname(hostname: str) -> None:
    lowered = hostname.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=400, detail="Remote URL must not target localhost")

    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        return

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status_code=400, detail="Remote URL resolves to a private or local address")


def _get_backend(request: Request) -> Backend:
    backend = getattr(request.app.state, "backend", None)
    if backend is None:
        raise RuntimeError("Backend has not been initialized")
    return backend


def _principal_for_request(request: Request):
    token = get_bearer_token(request.headers)
    principal = resolve_principal(token)
    if auth_enabled() and principal is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API credentials")
    return principal


def _require_roles(request: Request, allowed_roles: set[str]):
    principal = _principal_for_request(request)
    if principal is None:
        return None
    if principal.role == "admin":
        return principal
    if principal.role not in allowed_roles:
        raise HTTPException(status_code=403, detail=f"Role '{principal.role}' is not allowed to access this resource")
    return principal


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _now()


def _iso(value: Any) -> str:
    return _to_dt(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(value)


def _money(value: Any) -> str:
    if value is None or value == "":
        return "Pending"
    try:
        return f"${_to_float(value):,.0f}"
    except Exception:
        return str(value)


def _event_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _last_event(events: list[dict[str, Any]], event_type: str | list[str]) -> dict[str, Any]:
    targets = [_event_key(event_type)] if isinstance(event_type, str) else [_event_key(item) for item in event_type]
    for event in reversed(events):
        if _event_key(event.get("event_type")) in targets:
            return event
    return {}


def _domain_from_stream(stream_id: str) -> str:
    if stream_id.startswith("loan-"):
        return "loan"
    if stream_id.startswith("credit-"):
        return "credit"
    if stream_id.startswith("fraud-"):
        return "fraud"
    if stream_id.startswith("compliance-"):
        return "compliance"
    if stream_id.startswith("agent-"):
        return "agent session"
    return "loan"


def _pretty_status(state: Any) -> str:
    mapping = {
        "FINAL_APPROVED": "Approved",
        "APPROVED": "Approved",
        "FINAL_DECLINED": "Declined",
        "DECLINED": "Declined",
        "DECLINED_COMPLIANCE": "Declined",
        "PENDING_HUMAN_REVIEW": "Human Review",
        "PENDING_DECISION": "In Progress",
    }
    return mapping.get(str(state), "In Progress")


def _pretty_risk_tier(risk_tier: str) -> str:
    mapping = {"LOW": "Low", "MEDIUM": "Moderate", "HIGH": "High"}
    return mapping.get(str(risk_tier).upper(), str(risk_tier).title())


def _rebuild_compliance_state(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    for event in events:
        payload = event.get("payload") or {}
        etype = str(event.get("event_type"))
        application_id = str(payload.get("application_id") or "")
        if not application_id:
            continue

        if state is None:
            state = {
                "application_id": application_id,
                "session_id": None,
                "regulation_set_version": None,
                "rules_to_evaluate": [],
                "passed_rules": [],
                "failed_rules": [],
                "noted_rules": [],
                "overall_verdict": None,
                "has_hard_block": False,
                "last_updated_at": None,
            }

        if etype == "ComplianceCheckInitiated":
            state["session_id"] = payload.get("session_id")
            state["regulation_set_version"] = payload.get("regulation_set_version")
            state["rules_to_evaluate"] = list(payload.get("rules_to_evaluate") or [])
        elif etype == "ComplianceRulePassed":
            state["passed_rules"].append(
                {
                    "rule_id": payload.get("rule_id"),
                    "rule_name": payload.get("rule_name"),
                    "rule_version": payload.get("rule_version"),
                    "evidence_hash": payload.get("evidence_hash"),
                }
            )
        elif etype == "ComplianceRuleFailed":
            failed_rule = {
                "rule_id": payload.get("rule_id"),
                "rule_name": payload.get("rule_name"),
                "rule_version": payload.get("rule_version"),
                "failure_reason": payload.get("failure_reason"),
                "is_hard_block": bool(payload.get("is_hard_block", False)),
            }
            state["failed_rules"].append(failed_rule)
            if failed_rule["is_hard_block"]:
                state["has_hard_block"] = True
        elif etype == "ComplianceRuleNoted":
            state["noted_rules"].append(
                {
                    "rule_id": payload.get("rule_id"),
                    "rule_name": payload.get("rule_name"),
                    "note_type": payload.get("note_type"),
                    "note_text": payload.get("note_text"),
                }
            )
        elif etype == "ComplianceCheckCompleted":
            state["overall_verdict"] = payload.get("overall_verdict")
            state["has_hard_block"] = bool(payload.get("has_hard_block", state["has_hard_block"]))

        state["last_updated_at"] = event.get("recorded_at")

    return state


async def _compliance_state_with_fallback(self: Backend, application_id: str) -> dict[str, Any] | None:
    state = self.runtime.compliance_audit.get_current_compliance(application_id)
    if state is not None:
        return state

    events = await self.store.load_stream(f"compliance-{application_id}")
    return _rebuild_compliance_state(events)


def _loan_type_label(value: Any) -> str:
    mapping = {
        "working_capital": "Working Capital",
        "equipment_financing": "Equipment Finance",
        "real_estate": "Commercial Real Estate",
        "expansion": "Expansion",
        "refinancing": "Refinancing",
        "acquisition": "Acquisition",
        "bridge": "Bridge Loan",
    }
    if value is None:
        return "Working Capital"
    key = str(value)
    return mapping.get(key, key.replace("_", " ").title())


def _pretty_agent_name(agent_id: str) -> str:
    mapping = {
        "credit_analysis": "Credit Analyst",
        "fraud_detection": "Fraud Screen",
        "compliance": "Compliance Agent",
        "decision_orchestrator": "Decision Engine",
    }
    return mapping.get(agent_id, agent_id.replace("_", " ").title())


def _attach_backend_methods(backend: Backend) -> None:
    backend.get_application_detail = _backend_get_application_detail.__get__(backend, Backend)
    backend.list_timeline = _backend_list_timeline.__get__(backend, Backend)
    backend.list_review_queue = _backend_list_review_queue.__get__(backend, Backend)
    backend.list_review_queue_metrics = _backend_list_review_queue_metrics.__get__(backend, Backend)
    backend.list_compliance_rows = _backend_list_compliance_rows.__get__(backend, Backend)
    backend.list_agent_performance = _backend_list_agent_performance.__get__(backend, Backend)
    backend.list_stuck_agent_sessions = _backend_list_stuck_agent_sessions.__get__(backend, Backend)


async def _backend_get_application_detail(self: Backend, application_id: str) -> dict[str, Any] | None:
    loan_events = await self.store.load_stream(f"loan-{application_id}")
    summary = self.runtime.application_summary.get_application(application_id)
    if summary is None:
        summary = _fallback_application_summary(application_id, loan_events)

    credit_events = await self.store.load_stream(f"credit-{application_id}")
    fraud_events = await self.store.load_stream(f"fraud-{application_id}")
    compliance_state = await _compliance_state_with_fallback(self, application_id)
    manual_review = _manual_review_for(self.runtime, application_id)

    submitted = _last_event(loan_events, "ApplicationSubmitted")
    app_payload = submitted.get("payload", {}) if submitted else {}
    applicant_id = str(app_payload.get("applicant_id") or summary.get("applicant_id") or application_id)
    company = await _lookup_company(self.registry, applicant_id)
    company_name = company.get("name") if company else applicant_id
    industry = company.get("industry") if company else app_payload.get("industry", "Unknown")
    latest_financial = await _latest_financials(self.registry, applicant_id)
    compliance_flags = await _compliance_flags(self.registry, applicant_id)

    credit_event = _last_event(credit_events, ["CreditAnalysisCompleted", "CREDIT_ANALYSIS_COMPLETED"])
    fraud_event = _last_event(fraud_events, "FraudScreeningCompleted")
    decision_event = _last_event(loan_events, "DecisionGenerated")
    approved_event = _last_event(loan_events, "ApplicationApproved")
    declined_event = _last_event(loan_events, "ApplicationDeclined")
    review_completed = _last_event(loan_events, "HumanReviewCompleted")
    review_requested = _last_event(loan_events, "HumanReviewRequested") or manual_review or {}

    submitted_at = _to_dt(app_payload.get("submitted_at") or summary.get("last_event_at") or _now())
    decision_at = _to_dt(
        (decision_event or approved_event or declined_event or review_completed or review_requested or {}).get("recorded_at")
        or summary.get("final_decision_at")
        or summary.get("last_event_at")
        or submitted_at
    )
    decision_time_minutes = max(0, int((decision_at - submitted_at).total_seconds() / 60))

    analyses = _build_analyses(credit_event, fraud_event, compliance_state, review_requested)
    current_status = _pretty_status(summary.get("state"))
    final_recommendation, status = _derive_outcome(summary, decision_event, approved_event, declined_event, review_completed, review_requested)
    timeline = await _backend_list_timeline(self, application_id)
    documents = _build_documents(loan_events)
    pipeline = _build_pipeline(summary, loan_events, credit_event, fraud_event, compliance_state, decision_event, review_completed, review_requested)
    compliance_rows = _build_compliance_rows(compliance_state)

    return {
        "id": application_id,
        "companyId": applicant_id,
        "businessName": company_name,
        "loanType": _loan_type_label(app_payload.get("loan_purpose")),
        "requestedAmount": _to_float(app_payload.get("requested_amount_usd") or summary.get("requested_amount_usd")),
        "loanPurpose": str(app_payload.get("loan_purpose") or "working_capital"),
        "annualRevenue": _to_float(latest_financial.get("annualRevenue")) if latest_financial else _to_float(app_payload.get("requested_amount_usd") or 0),
        "yearsInBusiness": int(max(0, datetime.now(timezone.utc).year - int(company["founded_year"]))) if company and company.get("founded_year") else 0,
        "industry": industry,
        "collateralType": app_payload.get("collateral_type", "Not specified"),
        "riskTier": _extract_risk_tier(credit_event, summary),
        "status": status,
        "currentStatus": current_status,
        "finalRecommendation": final_recommendation,
        "confidenceScore": _derive_confidence(credit_event, decision_event, fraud_event),
        "lastUpdated": _iso(summary.get("final_decision_at") or summary.get("last_event_at") or submitted_at),
        "decisionTimeMinutes": decision_time_minutes,
        "assignedReviewer": (manual_review or {}).get("assigned_to") or summary.get("human_reviewer_id") or "Unassigned",
        "reviewReason": (manual_review or {}).get("reason") or summary.get("decision") or "",
        "reviewerNotes": (review_completed or {}).get("payload", {}).get("override_reason") if review_completed else None,
        "policyNotes": _policy_notes(analyses, compliance_state, review_requested),
        "documentCount": len(documents),
        "pipeline": pipeline,
        "facts": _build_fact_cards(latest_financial, summary, compliance_flags),
        "documents": documents,
        "analyses": analyses,
        "decisionReasons": _build_decision_reasons(analyses, compliance_state, manual_review, decision_event),
        "timeline": timeline,
        "complianceResults": compliance_rows,
        "extractedFacts": _build_extracted_facts(latest_financial, compliance_flags),
    }


def _fallback_application_summary(application_id: str, loan_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    submitted = _last_event(loan_events, "ApplicationSubmitted")
    if not submitted:
        return None

    submitted_payload = submitted.get("payload", {}) or {}
    last_event = loan_events[-1] if loan_events else submitted
    state = "SUBMITTED"
    approved_amount_usd = None
    decision = None
    human_reviewer_id = None
    final_decision_at = None
    for event in loan_events:
        etype = str(event.get("event_type"))
        payload = event.get("payload", {}) or {}
        if etype == "DecisionRequested":
            state = "PENDING_DECISION"
        elif etype == "HumanReviewRequested":
            state = "PENDING_HUMAN_REVIEW"
        elif etype == "DecisionGenerated":
            decision = payload.get("recommendation")
            state = "DECISION_GENERATED"
        elif etype == "HumanReviewCompleted":
            human_reviewer_id = payload.get("reviewer_id")
            state = "HUMAN_REVIEW_COMPLETE"
            final_decision_at = event.get("recorded_at")
        elif etype == "ApplicationApproved":
            approved_amount_usd = payload.get("approved_amount_usd")
            state = "FINAL_APPROVED"
            final_decision_at = event.get("recorded_at")
        elif etype == "ApplicationDeclined":
            state = "FINAL_DECLINED"
            final_decision_at = event.get("recorded_at")

    return {
        "application_id": application_id,
        "state": state,
        "applicant_id": submitted_payload.get("applicant_id"),
        "requested_amount_usd": submitted_payload.get("requested_amount_usd"),
        "approved_amount_usd": approved_amount_usd,
        "risk_tier": None,
        "fraud_score": None,
        "compliance_status": None,
        "decision": decision,
        "agent_sessions_completed": [],
        "last_event_type": last_event.get("event_type"),
        "last_event_at": last_event.get("recorded_at"),
        "human_reviewer_id": human_reviewer_id,
        "final_decision_at": final_decision_at,
    }


async def _backend_list_timeline(self: Backend, application_id: str | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in self.store.load_all(from_position=0, application_id=application_id):
        payload = event.get("payload") or {}
        events.append(
            {
                "eventName": event.get("event_type"),
                "timestamp": _iso(event.get("recorded_at")),
                "streamId": event.get("stream_id"),
                "version": int(event.get("stream_position", 0)),
                "domain": _domain_from_stream(str(event.get("stream_id", ""))),
                "payloadPreview": _payload_preview(payload),
                "sessionId": payload.get("session_id") or payload.get("orchestrator_session_id") or payload.get("reviewer_id"),
            }
        )
    events.sort(key=lambda row: row["timestamp"], reverse=True)
    return events


async def _backend_list_review_queue(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary_by_app = {row["application_id"]: row for row in self.runtime.application_summary.all_rows()}
    for review in self.runtime.manual_reviews.all_rows():
        app_id = str(review.get("application_id"))
        detail = await _backend_get_application_detail(self, app_id)
        if detail is None:
            continue
        rows.append(
            {
                "applicationId": app_id,
                "businessName": detail["businessName"],
                "reason": review.get("reason") or detail.get("reviewReason") or "",
                "confidence": detail.get("confidenceScore", 0),
                "assignedReviewer": review.get("assigned_to") or detail.get("assignedReviewer") or "Unassigned",
                "lastUpdated": _iso(review.get("requested_at") or summary_by_app.get(app_id, {}).get("last_event_at") or _now()),
            }
        )
    rows.sort(key=lambda row: row["lastUpdated"], reverse=True)
    return rows


async def _backend_list_review_queue_metrics(self: Backend) -> dict[str, Any]:
    return build_manual_review_backlog_snapshot(self.runtime.manual_reviews.all_rows())


async def _backend_list_compliance_rows(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    states = list(self.runtime.compliance_audit._current.values())
    if not states:
        seen: set[str] = set()
        async for event in self.store.load_all(from_position=0, event_types=["ComplianceCheckCompleted"]):
            app_id = str((event.get("payload") or {}).get("application_id") or "")
            if app_id:
                seen.add(app_id)
        for app_id in seen:
            state = await _compliance_state_with_fallback(self, app_id)
            if state is not None:
                states.append(state)

    for state in states:
        app_id = str(state["application_id"])
        detail = await _backend_get_application_detail(self, app_id)
        if detail is None:
            continue
        rows.append(
            {
                "applicationId": app_id,
                "businessName": detail["businessName"],
                "regulationVersion": state.get("regulation_set_version") or "2026.03",
                "passedRules": _map_rules(state.get("passed_rules", []), "passed", state.get("regulation_set_version")),
                "failedRules": _map_rules(state.get("failed_rules", []), "failed", state.get("regulation_set_version")),
                "notes": _compliance_notes(state, detail),
            }
        )
    return rows


async def _backend_list_agent_performance(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in self.runtime.agent_performance.all_rows():
        decisions = int(row.get("decisions_generated") or row.get("analyses_completed") or 0) or 1
        rows.append(
            {
                "agentName": _pretty_agent_name(str(row.get("agent_id", "unknown-agent"))),
                "modelVersion": str(row.get("model_version", "unknown-model")),
                "decisions": decisions,
                "overrides": round(decisions * _to_float(row.get("human_override_rate", 0.0))),
                "averageConfidence": _to_float(row.get("avg_confidence_score", 0.0)),
                "referralRate": _to_float(row.get("refer_rate", 0.0)),
                "approved": round(decisions * _to_float(row.get("approve_rate", 0.0))),
                "declined": round(decisions * _to_float(row.get("decline_rate", 0.0))),
                "humanReview": round(decisions * _to_float(row.get("refer_rate", 0.0))),
            }
        )
    return rows


async def _backend_list_stuck_agent_sessions(self: Backend, timeout_ms: int = 600000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in self.runtime.agent_session_failures.get_stuck_sessions(int(timeout_ms), now=_now()):
        rows.append(
            {
                "sessionId": row.get("session_id"),
                "applicationId": row.get("application_id"),
                "agentId": row.get("agent_id"),
                "agentType": row.get("agent_type"),
                "agentName": _pretty_agent_name(str(row.get("agent_id", "unknown-agent"))),
                "modelVersion": row.get("model_version"),
                "status": row.get("status"),
                "startedAt": _iso(row.get("started_at")),
                "lastEventAt": _iso(row.get("last_event_at")),
                "lastEventType": row.get("last_event_type"),
                "ageMs": int(row.get("age_ms", 0)),
                "timeoutMs": int(row.get("timeout_ms", timeout_ms)),
            }
        )
    return rows


async def _lookup_company(registry: ApplicantRegistryClient | None, company_id: str) -> dict[str, Any] | None:
    if registry is None:
        return None
    company = await registry.get_company(company_id)
    if company is None:
        return None
    return {
        "company_id": company.company_id,
        "name": company.name,
        "industry": company.industry,
        "founded_year": company.founded_year,
    }


async def _latest_financials(registry: ApplicantRegistryClient | None, company_id: str) -> dict[str, Any] | None:
    if registry is None:
        return None
    history = await registry.get_financial_history(company_id)
    if not history:
        return None
    latest = history[-1]
    return {
        "annualRevenue": latest.total_revenue,
        "ebitda": latest.ebitda,
        "debt": latest.long_term_debt,
        "cashFlow": getattr(latest, "operating_cash_flow", None),
    }


async def _compliance_flags(registry: ApplicantRegistryClient | None, company_id: str) -> list[dict[str, Any]]:
    if registry is None:
        return []
    flags = await registry.get_compliance_flags(company_id, active_only=True)
    return [{"flag_type": flag.flag_type, "severity": flag.severity, "note": flag.note} for flag in flags]


def _manual_review_for(runtime: MCPRuntime, application_id: str) -> dict[str, Any] | None:
    for row in runtime.manual_reviews.all_rows():
        if str(row.get("application_id")) == application_id:
            return row
    return None


def _derive_outcome(
    summary: dict[str, Any],
    decision_event: dict[str, Any],
    approved_event: dict[str, Any],
    declined_event: dict[str, Any],
    review_completed: dict[str, Any],
    review_requested: dict[str, Any],
) -> tuple[str, str]:
    if approved_event:
        return "Approve", "Approved"
    if declined_event:
        return "Decline", "Declined"
    if review_completed:
        payload = review_completed.get("payload", {})
        final = str(payload.get("final_decision", "REFER")).upper()
        if final == "APPROVE":
            return "Approve", "Approved"
        if final == "DECLINE":
            return "Decline", "Declined"
    if decision_event:
        recommendation = str(decision_event.get("payload", {}).get("recommendation", "REFER")).upper()
        if recommendation == "APPROVE":
            return "Approve", "Approved"
        if recommendation == "DECLINE":
            return "Decline", "Declined"
    if str(summary.get("state", "")).upper() == "PENDING_HUMAN_REVIEW" or review_requested:
        return "Human Review", "Human Review"
    return "Human Review", "In Progress"


async def _backend_get_application_detail(self: Backend, application_id: str) -> dict[str, Any] | None:
    loan_events = await self.store.load_stream(f"loan-{application_id}")
    summary = self.runtime.application_summary.get_application(application_id)
    if summary is None:
        summary = _fallback_application_summary(application_id, loan_events)

    credit_events = await self.store.load_stream(f"credit-{application_id}")
    fraud_events = await self.store.load_stream(f"fraud-{application_id}")
    compliance_state = await _compliance_state_with_fallback(self, application_id)
    manual_review = _manual_review_for(self.runtime, application_id)

    submitted = _last_event(loan_events, "ApplicationSubmitted")
    app_payload = submitted.get("payload", {}) if submitted else {}
    applicant_id = str(app_payload.get("applicant_id") or summary.get("applicant_id") or application_id)
    company = await _lookup_company(self.registry, applicant_id)
    company_name = company.get("name") if company else applicant_id
    industry = company.get("industry") if company else app_payload.get("industry", "Unknown")
    latest_financial = await _latest_financials(self.registry, applicant_id)
    compliance_flags = await _compliance_flags(self.registry, applicant_id)

    credit_event = _last_event(credit_events, ["CreditAnalysisCompleted", "CREDIT_ANALYSIS_COMPLETED"])
    fraud_event = _last_event(fraud_events, "FraudScreeningCompleted")
    decision_event = _last_event(loan_events, "DecisionGenerated")
    approved_event = _last_event(loan_events, "ApplicationApproved")
    declined_event = _last_event(loan_events, "ApplicationDeclined")
    review_completed = _last_event(loan_events, "HumanReviewCompleted")
    review_requested = _last_event(loan_events, "HumanReviewRequested") or manual_review or {}

    submitted_at = _to_dt(app_payload.get("submitted_at") or summary.get("last_event_at") or _now())
    decision_at = _to_dt(
        (decision_event or approved_event or declined_event or review_completed or review_requested or {}).get("recorded_at")
        or summary.get("final_decision_at")
        or summary.get("last_event_at")
        or submitted_at
    )
    decision_time_minutes = max(0, int((decision_at - submitted_at).total_seconds() / 60))

    analyses = _build_analyses(credit_event, fraud_event, compliance_state, review_requested)
    current_status = _pretty_status(summary.get("state"))
    final_recommendation, status = _derive_outcome(summary, decision_event, approved_event, declined_event, review_completed, review_requested)
    timeline = await _backend_list_timeline(self, application_id)
    documents = _build_documents(loan_events)
    pipeline = _build_pipeline(summary, loan_events, credit_event, fraud_event, compliance_state, decision_event, review_completed, review_requested)
    compliance_rows = _build_compliance_rows(compliance_state)

    return {
        "id": application_id,
        "companyId": applicant_id,
        "businessName": company_name,
        "loanType": _loan_type_label(app_payload.get("loan_purpose")),
        "requestedAmount": _to_float(app_payload.get("requested_amount_usd") or summary.get("requested_amount_usd")),
        "loanPurpose": str(app_payload.get("loan_purpose") or "working_capital"),
        "annualRevenue": _to_float(latest_financial.get("annualRevenue")) if latest_financial else _to_float(app_payload.get("requested_amount_usd") or 0),
        "yearsInBusiness": int(max(0, datetime.now(timezone.utc).year - int(company["founded_year"]))) if company and company.get("founded_year") else 0,
        "industry": industry,
        "collateralType": app_payload.get("collateral_type", "Not specified"),
        "riskTier": _pretty_risk_tier(_extract_risk_tier(credit_event, summary)),
        "status": status,
        "currentStatus": current_status,
        "finalRecommendation": final_recommendation,
        "confidenceScore": _derive_confidence(credit_event, decision_event, fraud_event),
        "lastUpdated": _iso(summary.get("final_decision_at") or summary.get("last_event_at") or submitted_at),
        "decisionTimeMinutes": decision_time_minutes,
        "assignedReviewer": (manual_review or {}).get("assigned_to") or summary.get("human_reviewer_id") or "Unassigned",
        "reviewReason": (manual_review or {}).get("reason") or summary.get("decision") or "",
        "reviewerNotes": (review_completed or {}).get("payload", {}).get("override_reason") if review_completed else None,
        "policyNotes": _policy_notes(analyses, compliance_state, review_requested),
        "documentCount": len(documents),
        "pipeline": pipeline,
        "facts": _build_fact_cards(latest_financial, summary, compliance_flags),
        "documents": documents,
        "analyses": analyses,
        "decisionReasons": _build_decision_reasons(analyses, compliance_state, manual_review, decision_event),
        "timeline": timeline,
        "complianceResults": compliance_rows,
        "extractedFacts": _build_extracted_facts(latest_financial, compliance_flags),
    }


async def _backend_list_timeline(self: Backend, application_id: str | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in self.store.load_all(from_position=0, application_id=application_id):
        payload = event.get("payload") or {}
        events.append(
            {
                "eventName": event.get("event_type"),
                "timestamp": _iso(event.get("recorded_at")),
                "streamId": event.get("stream_id"),
                "version": int(event.get("stream_position", 0)),
                "domain": _domain_from_stream(str(event.get("stream_id", ""))),
                "payloadPreview": _payload_preview(payload),
                "sessionId": payload.get("session_id") or payload.get("orchestrator_session_id") or payload.get("reviewer_id"),
            }
        )
    events.sort(key=lambda row: row["timestamp"], reverse=True)
    return events


async def _backend_list_review_queue(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary_by_app = {row["application_id"]: row for row in self.runtime.application_summary.all_rows()}
    for review in self.runtime.manual_reviews.all_rows():
        app_id = str(review.get("application_id"))
        detail = await _backend_get_application_detail(self, app_id)
        if detail is None:
            continue
        rows.append(
            {
                "applicationId": app_id,
                "businessName": detail["businessName"],
                "reason": review.get("reason") or detail.get("reviewReason") or "",
                "confidence": detail.get("confidenceScore", 0),
                "assignedReviewer": review.get("assigned_to") or detail.get("assignedReviewer") or "Unassigned",
                "lastUpdated": _iso(review.get("requested_at") or summary_by_app.get(app_id, {}).get("last_event_at") or _now()),
            }
        )
    rows.sort(key=lambda row: row["lastUpdated"], reverse=True)
    return rows


async def _backend_list_compliance_rows(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    states = list(self.runtime.compliance_audit._current.values())
    if not states:
        seen: set[str] = set()
        async for event in self.store.load_all(from_position=0, event_types=["ComplianceCheckCompleted"]):
            app_id = str((event.get("payload") or {}).get("application_id") or "")
            if app_id:
                seen.add(app_id)
        for app_id in seen:
            state = await _compliance_state_with_fallback(self, app_id)
            if state is not None:
                states.append(state)

    for state in states:
        app_id = str(state["application_id"])
        detail = await _backend_get_application_detail(self, app_id)
        if detail is None:
            continue
        rows.append(
            {
                "applicationId": app_id,
                "businessName": detail["businessName"],
                "regulationVersion": state.get("regulation_set_version") or "2026.03",
                "passedRules": _map_rules(state.get("passed_rules", []), "passed", state.get("regulation_set_version")),
                "failedRules": _map_rules(state.get("failed_rules", []), "failed", state.get("regulation_set_version")),
                "notes": _compliance_notes(state, detail),
            }
        )
    return rows


async def _backend_list_agent_performance(self: Backend) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in self.runtime.agent_performance.all_rows():
        decisions = int(row.get("decisions_generated") or row.get("analyses_completed") or 0) or 1
        rows.append(
            {
                "agentName": _pretty_agent_name(str(row.get("agent_id", "unknown-agent"))),
                "modelVersion": str(row.get("model_version", "unknown-model")),
                "decisions": decisions,
                "overrides": round(decisions * _to_float(row.get("human_override_rate", 0.0))),
                "averageConfidence": _to_float(row.get("avg_confidence_score", 0.0)),
                "referralRate": _to_float(row.get("refer_rate", 0.0)),
                "approved": round(decisions * _to_float(row.get("approve_rate", 0.0))),
                "declined": round(decisions * _to_float(row.get("decline_rate", 0.0))),
                "humanReview": round(decisions * _to_float(row.get("refer_rate", 0.0))),
            }
        )
    return rows


async def _lookup_company(registry: ApplicantRegistryClient | None, company_id: str) -> dict[str, Any] | None:
    if registry is None:
        return None
    company = await registry.get_company(company_id)
    if company is None:
        return None
    return {
        "company_id": company.company_id,
        "name": company.name,
        "industry": company.industry,
        "founded_year": company.founded_year,
    }


async def _latest_financials(registry: ApplicantRegistryClient | None, company_id: str) -> dict[str, Any] | None:
    if registry is None:
        return None
    history = await registry.get_financial_history(company_id)
    if not history:
        return None
    latest = history[-1]
    return {
        "annualRevenue": latest.total_revenue,
        "ebitda": latest.ebitda,
        "debt": latest.long_term_debt,
        "cashFlow": getattr(latest, "operating_cash_flow", None),
    }


async def _compliance_flags(registry: ApplicantRegistryClient | None, company_id: str) -> list[dict[str, Any]]:
    if registry is None:
        return []
    flags = await registry.get_compliance_flags(company_id, active_only=True)
    return [{"flag_type": flag.flag_type, "severity": flag.severity, "note": flag.note} for flag in flags]


def _manual_review_for(runtime: MCPRuntime, application_id: str) -> dict[str, Any] | None:
    for row in runtime.manual_reviews.all_rows():
        if str(row.get("application_id")) == application_id:
            return row
    return None


def _credit_payload_parts(event: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = event.get("payload", {}) if event else {}
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    return payload, decision


def _confidence_score(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"low", "l"}:
            return 60
        if normalized in {"medium", "med", "m"}:
            return 75
        if normalized in {"high", "h"}:
            return 90
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 1:
        return int(round(numeric * 100))
    return int(round(numeric))


def _derive_confidence(*events: dict[str, Any]) -> int:
    for event in events:
        payload, decision = _credit_payload_parts(event)
        if "credit_score" in payload:
            score = _confidence_score(payload.get("credit_score"))
            if score is not None:
                return score
        if "confidence" in payload:
            score = _confidence_score(payload.get("confidence"))
            if score is not None:
                return score
        if "confidence" in decision:
            score = _confidence_score(decision.get("confidence"))
            if score is not None:
                return score
    return 0


def _extract_risk_tier(*events: dict[str, Any]) -> str:
    for event in events:
        payload, decision = _credit_payload_parts(event)
        risk = decision.get("risk_tier")
        if risk:
            return str(risk)
        risk = payload.get("risk_tier")
        if risk:
            return str(risk)
        policy_outcome = str(payload.get("policy_outcome") or "").upper()
        if policy_outcome == "APPROVE":
            return "LOW"
        if policy_outcome == "MANUAL_REVIEW":
            return "MEDIUM"
        if policy_outcome == "REJECT":
            return "HIGH"
    summary = events[-1] if events else {}
    decision = summary.get("decision") if isinstance(summary, dict) else None
    if isinstance(decision, dict):
        return str(decision.get("risk_tier") or "LOW")
    return "LOW"


def _build_fact_cards(latest_financial: dict[str, Any] | None, summary: dict[str, Any], compliance_flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revenue = latest_financial.get("annualRevenue") if latest_financial else summary.get("requested_amount_usd")
    ebitda = latest_financial.get("ebitda") if latest_financial else None
    debt = latest_financial.get("debt") if latest_financial else None
    cash_flow = latest_financial.get("cashFlow") if latest_financial else None
    flags = [flag["note"] for flag in compliance_flags[:3]] if compliance_flags else []
    return [
        {"label": "Revenue", "value": _money(revenue), "tone": "good"},
        {"label": "EBITDA", "value": _money(ebitda), "tone": "good"},
        {"label": "Debt", "value": _money(debt), "tone": "neutral"},
        {"label": "Cash Flow", "value": _money(cash_flow), "tone": "good"},
        {"label": "Flags", "value": ", ".join(flags) if flags else "None material", "tone": "neutral"},
    ]


def _build_analyses(
    credit_event: dict[str, Any],
    fraud_event: dict[str, Any],
    compliance_state: dict[str, Any] | None,
    review_requested: dict[str, Any],
) -> dict[str, Any]:
    credit_payload, credit_decision = _credit_payload_parts(credit_event)
    credit_score = _confidence_score(credit_payload.get("credit_score"))
    if credit_score is None:
        credit_score = _confidence_score(credit_decision.get("confidence"))
    if credit_score is None:
        credit_score = _confidence_score(credit_payload.get("confidence"))
    if credit_score is None:
        credit_score = 0
    credit_verdict = credit_decision.get("risk_tier") or credit_payload.get("risk_tier")
    if not credit_verdict:
        policy_outcome = str(credit_payload.get("policy_outcome") or "").upper()
        credit_verdict = {"APPROVE": "LOW", "MANUAL_REVIEW": "MEDIUM", "REJECT": "HIGH"}.get(policy_outcome, "LOW")
    credit_notes = credit_decision.get("rationale") or credit_payload.get("explanation") or "No credit analysis yet."
    credit_reasons = credit_decision.get("key_concerns") or credit_payload.get("key_factors") or []
    fraud_payload = fraud_event.get("payload", {}) or {}
    compliance_state = compliance_state or {}
    return {
        "credit": {
            "score": credit_score,
            "verdict": _pretty_risk_tier(str(credit_verdict)),
            "notes": str(credit_notes),
            "reasons": [str(item) for item in credit_reasons],
        },
        "fraud": {
            "score": int(round(float(fraud_payload.get("fraud_score", 0.0)) * 100)),
            "verdict": str(fraud_payload.get("risk_level", "LOW")).title(),
            "notes": f"{int(fraud_payload.get('anomalies_found', 0))} anomalies detected" if fraud_payload else "No fraud screening yet.",
            "reasons": [str(fraud_payload.get("recommendation", "PROCEED"))] if fraud_payload else [],
        },
        "compliance": {
            "score": 100 if str(compliance_state.get("overall_verdict", "CLEAR")).upper() == "CLEAR" else 60,
            "verdict": str(compliance_state.get("overall_verdict", "CLEAR")).title(),
            "notes": "Compliance complete" if compliance_state else "No compliance record yet.",
            "reasons": [str(review_requested.get("reason", ""))] if review_requested else [],
        },
    }


def _build_documents(loan_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs = []
    for event in loan_events:
        if event.get("event_type") == "DocumentUploaded":
            payload = event.get("payload", {})
            document = {
                "name": str(payload.get("filename", payload.get("document_id", "document"))),
                "type": str(payload.get("document_type", "Document")).replace("_", " ").title(),
                "size": f"{round(int(payload.get('file_size_bytes', 0)) / 1024)} KB" if payload.get("file_size_bytes") else "Unknown",
                "status": "verified",
            }
            download_url = _document_download_url(str(payload.get("file_path") or ""))
            if download_url is not None:
                document["downloadUrl"] = download_url
            docs.append(document)
    if not docs:
        docs.append({"name": "application_proposal.pdf", "type": "Proposal", "size": "Unknown", "status": "uploaded"})
    return docs


def _build_pipeline(
    summary: dict[str, Any],
    loan_events: list[dict[str, Any]],
    credit_event: dict[str, Any],
    fraud_event: dict[str, Any],
    compliance_state: dict[str, Any] | None,
    decision_event: dict[str, Any],
    review_completed: dict[str, Any],
    review_requested: dict[str, Any],
) -> list[dict[str, Any]]:
    has_docs = any(event.get("event_type") == "DocumentUploaded" for event in loan_events)
    stages = [
        ("Documents", "Document Intake", "complete" if has_docs else "in-progress"),
        ("Credit", "Credit Analyst", "complete" if credit_event else "pending"),
        ("Fraud", "Fraud Screen", "complete" if fraud_event else "pending"),
        ("Compliance", "Compliance Agent", "complete" if compliance_state else "pending"),
        (
            "Decision",
            "Human Reviewer" if review_requested and not decision_event else "Decision Engine",
            "complete" if (decision_event or review_completed or str(summary.get("state", "")).startswith("FINAL")) else ("in-progress" if review_requested else "pending"),
        ),
    ]
    return [
        {
            "name": name,
            "state": state,
            "owner": owner,
            "completedAt": _iso((decision_event or review_completed or review_requested or {}).get("recorded_at")) if state == "complete" else None,
        }
        for name, owner, state in stages
    ]


def _build_compliance_rows(compliance_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not compliance_state:
        return []
    version = str(compliance_state.get("regulation_set_version") or "2026.03")
    rows = []
    for rule in compliance_state.get("passed_rules", []):
        rows.append(
            {
                "ruleId": str(rule.get("rule_id", "")),
                "label": str(rule.get("rule_name", rule.get("rule_id", "Passed rule"))),
                "note": str(rule.get("evidence_hash", "Rule passed")),
                "status": "passed",
                "regulationVersion": version,
            }
        )
    for rule in compliance_state.get("failed_rules", []):
        rows.append(
            {
                "ruleId": str(rule.get("rule_id", "")),
                "label": str(rule.get("rule_name", rule.get("rule_id", "Failed rule"))),
                "note": str(rule.get("failure_reason", "Rule failed")),
                "status": "failed",
                "regulationVersion": version,
            }
        )
    return rows


def _build_decision_reasons(
    analyses: dict[str, Any],
    compliance_state: dict[str, Any] | None,
    manual_review: dict[str, Any] | None,
    decision_event: dict[str, Any],
) -> list[str]:
    reasons = []
    for key in ("credit", "fraud", "compliance"):
        note = analyses.get(key, {}).get("notes")
        if note:
            reasons.append(str(note))
    if manual_review and manual_review.get("reason"):
        reasons.append(str(manual_review["reason"]))
    if decision_event and (decision_event.get("payload", {}) or {}).get("executive_summary"):
        reasons.append(str(decision_event["payload"]["executive_summary"]))
    return reasons[:4] or ["Awaiting backend decision data"]


def _build_extracted_facts(latest_financial: dict[str, Any] | None, compliance_flags: list[dict[str, Any]]) -> dict[str, Any]:
    revenue = latest_financial.get("annualRevenue") if latest_financial else None
    ebitda = latest_financial.get("ebitda") if latest_financial else None
    debt = latest_financial.get("debt") if latest_financial else None
    cash_flow = latest_financial.get("cashFlow") if latest_financial else None
    return {
        "revenue": _money(revenue),
        "ebitda": _money(ebitda),
        "debt": _money(debt),
        "cashFlow": _money(cash_flow),
        "flags": [flag["note"] for flag in compliance_flags] or ["No active compliance flags"],
    }


def _policy_notes(analyses: dict[str, Any], compliance_state: dict[str, Any] | None, review_requested: dict[str, Any]) -> str:
    notes = []
    if analyses.get("credit", {}).get("verdict"):
        notes.append(f"Credit: {analyses['credit']['verdict']}")
    if analyses.get("fraud", {}).get("verdict"):
        notes.append(f"Fraud: {analyses['fraud']['verdict']}")
    if compliance_state:
        notes.append(f"Compliance: {str(compliance_state.get('overall_verdict', 'CLEAR')).title()}")
    if review_requested and review_requested.get("reason"):
        notes.append(f"Manual review: {review_requested['reason']}")
    return " | ".join(notes) if notes else "No policy notes available."


def _compliance_notes(state: dict[str, Any], detail: dict[str, Any]) -> str:
    parts = []
    if state.get("overall_verdict"):
        parts.append(f"Verdict: {state['overall_verdict']}")
    if detail.get("policyNotes"):
        parts.append(str(detail["policyNotes"]))
    return " | ".join(parts) if parts else "No compliance notes"


def _map_rules(rules: list[dict[str, Any]], status: str, version: Any) -> list[dict[str, Any]]:
    return [
        {
            "ruleId": str(rule.get("rule_id", "")),
            "label": str(rule.get("rule_name", rule.get("rule_id", "Rule"))),
            "note": str(rule.get("failure_reason") or rule.get("evidence_hash") or rule.get("note_text") or "Rule processed"),
            "status": status,
            "regulationVersion": str(version or "2026.03"),
        }
        for rule in rules
    ]


def _payload_preview(payload: dict[str, Any]) -> str:
    keys = [
        "application_id",
        "requested_amount_usd",
        "recommendation",
        "overall_verdict",
        "final_decision",
        "reason",
        "confidence",
        "fraud_score",
        "session_id",
    ]
    preview = {key: payload[key] for key in keys if key in payload}
    return str(preview)


def _derive_outcome(
    summary: dict[str, Any],
    decision_event: dict[str, Any],
    approved_event: dict[str, Any],
    declined_event: dict[str, Any],
    review_completed: dict[str, Any],
    review_requested: dict[str, Any],
) -> tuple[str, str]:
    if approved_event:
        return "Approve", "Approved"
    if declined_event:
        return "Decline", "Declined"
    if review_completed:
        payload = review_completed.get("payload", {})
        final = str(payload.get("final_decision", "REFER")).upper()
        if final == "APPROVE":
            return "Approve", "Approved"
        if final == "DECLINE":
            return "Decline", "Declined"
    if decision_event:
        recommendation = str(decision_event.get("payload", {}).get("recommendation", "REFER")).upper()
        if recommendation == "APPROVE":
            return "Approve", "Approved"
        if recommendation == "DECLINE":
            return "Decline", "Declined"
    if str(summary.get("state", "")).upper() == "PENDING_HUMAN_REVIEW" or review_requested:
        return "Human Review", "Human Review"
    return "Human Review", "In Progress"
