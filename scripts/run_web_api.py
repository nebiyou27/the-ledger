from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    host = os.environ.get("LEDGER_API_HOST", "127.0.0.1")
    port = int(os.environ.get("LEDGER_API_PORT", "8000"))
    uvicorn.run("ledger.api:create_app", factory=True, host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
