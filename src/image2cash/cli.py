"""Command line: extract | run | verify."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="image2cash", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="OCR + parse an order image, print JSON and validation result")
    e.add_argument("image", type=Path)

    r = sub.add_parser("run", help="full Order-first flow against the running Fakturama")
    r.add_argument("image", type=Path, nargs="?")
    r.add_argument("--draft", type=Path, help="run a reviewed order.json instead of OCR'ing an image")
    r.add_argument("--run-dir", type=Path)
    r.add_argument("--resume", action="store_true", help="continue a stopped run whose Order is already saved")

    v = sub.add_parser("verify", help="read-only DB check of the documents for a Cust.Ref.")
    v.add_argument("cust_ref")

    a = p.parse_args(argv)
    if a.cmd == "extract":
        from image2cash.extract import extract
        from image2cash.validate import check_order

        draft = extract(a.image)
        print(draft.model_dump_json(indent=1))
        problems = check_order(draft)
        print("validation:", "OK" if not problems else problems)
        return 0 if not problems else 3
    if a.cmd == "run":
        from image2cash.flow import run
        from image2cash.models import OrderDraft

        if not (a.image or a.draft):
            p.error("run needs an image or --draft")
        draft = OrderDraft.model_validate_json(a.draft.read_text(encoding="utf-8")) if a.draft else None
        return run(a.image or a.draft, a.run_dir, draft=draft, resume=a.resume)
    if a.cmd == "verify":
        from image2cash.verify import db

        for d in db.documents_with_ref(a.cust_ref):
            print(d)
            for it in db.document_items(d["id"]):
                print("   ", it)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
