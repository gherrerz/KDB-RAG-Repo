"""Wrapper CLI para administrar el esquema PostgreSQL del repo."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Agrega src/ al path y delega al modulo administrativo real."""
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from coderag.storage.postgres_schema_admin import main as admin_main

    return admin_main()


if __name__ == "__main__":
    raise SystemExit(main())