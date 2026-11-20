#!/usr/bin/env python3
"""Delete one random up-to-100-300 document batch from every lab index."""

from opensearch_lab.bootstrap import ensure_service_user, ensure_virtualenv


if __name__ == "__main__":
    if not ensure_service_user(__file__):
        raise SystemExit(1)
    if not ensure_virtualenv(__file__):
        raise SystemExit(1)
    from opensearch_lab.cli import main
    raise SystemExit(main("delete-docs"))
