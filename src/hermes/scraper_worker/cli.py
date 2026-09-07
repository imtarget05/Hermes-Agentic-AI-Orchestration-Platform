"""CLI entrypoint for the scraper worker: ``hermes-scraper``."""
from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        return _cmd_cleanup(sys.argv[2:])
    from . import main as _main
    _main()
    return 0


def _cmd_cleanup(args: list[str]) -> int:
    rag_path = ""
    ttl_days = 30
    i = 0
    while i < len(args):
        if args[i] == "--rag-path" and i + 1 < len(args):
            rag_path = args[i + 1]
            i += 2
        elif args[i] == "--ttl-days" and i + 1 < len(args):
            try:
                ttl_days = int(args[i + 1])
            except ValueError:
                print("--ttl-days must be an integer", file=sys.stderr)
                return 1
            i += 2
        else:
            i += 1

    if not rag_path:
        rag_path = os.environ.get("HERMES_RAG_INDEX", "")

    if not rag_path:
        print("--rag-path is required or HERMES_RAG_INDEX must be set", file=sys.stderr)
        return 1

    from ..scraper.db import ScrapedDocStore
    store = ScrapedDocStore(rag_path=rag_path, ttl_days=ttl_days)
    stale = store.evict_stale(ttl_days=ttl_days)
    print(f"Evicted {len(stale)} stale chunks")
    store.save(rag_path)
    return 0
