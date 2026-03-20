from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.demo_scenarios import build_narr05_demo_store
from ledger.regulatory import generate_regulatory_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NARR-05 human-override demo and write artifacts.")
    parser.add_argument(
        "--output-dir",
        default="artifacts",
        help="Directory where the regulatory package and narrative report should be written.",
    )
    return parser


async def run_demo(output_dir: Path) -> int:
    store, application_id = await build_narr05_demo_store()
    package_path = output_dir / "regulatory_package_NARR05.json"
    package = await generate_regulatory_package(store, application_id, output_path=package_path)

    report_path = output_dir / "narrative_test_results.txt"
    report_lines = [
        "NARR-05 demo smoke",
        "===================",
        f"Application: {application_id}",
        f"Package written: {package_path}",
        f"Final decision: {package['what_if']['actual']['final_decision']}",
        f"Decision recommendation: {package['underwriting']['decision_recommendation']}",
        f"Human override used: {package['underwriting']['override_used']}",
        f"Approved amount: ${package['underwriting']['approved_amount_usd']:,.0f}",
        f"Conditions count: {package['what_if']['underwriting']['conditions_count']}",
        "",
        "What-if result:",
        json.dumps(package["what_if"], indent=2),
        "",
        "Underwriting narrative:",
        package["what_if"]["narrative"],
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print(package["what_if"]["narrative"])
    print()
    print(f"Wrote {package_path}")
    print(f"Wrote {report_path}")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    raise SystemExit(asyncio.run(run_demo(output_dir)))


if __name__ == "__main__":
    main()
