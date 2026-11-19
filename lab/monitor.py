#!/usr/bin/env python3
"""Terminal dashboard: F4 toggles controlled document mutations."""
from opensearch_lab.bootstrap import ensure_service_user, ensure_virtualenv

if __name__ == "__main__":
    if not ensure_service_user(__file__):
        raise SystemExit(1)
    if not ensure_virtualenv(__file__):
        raise SystemExit(1)
    from opensearch_lab.cli import main
    raise SystemExit(main("dashboard"))
