"""One continuous Order-first flow: image -> saved Order -> linked, paid Invoice."""

from __future__ import annotations

import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from image2cash import config
from image2cash.extract import extract
from image2cash.models import OrderDraft
from image2cash.steps import debtor, invoice, order, product
from image2cash.steps.context import Ctx
from image2cash.ui.core import UI, AutomationError, ManualReview
from image2cash.validate import check_order

STEPS = [
    ("preflight", order.preflight),
    ("order header", order.open_order),
    ("debtor", debtor.select_or_create),
    ("products and lines", product.all_items),
    ("save order", order.complete_and_save),
    ("invoice and payment", invoice.create_and_pay),
]
RESUME = {
    "invoice": [("verify saved order", order.verify_saved_order), ("invoice and payment", invoice.create_and_pay)]
}


class ExitCode:
    OK = 0
    FAILED = 1
    MANUAL_REVIEW = 2
    INVALID_SOURCE = 3
    BUSY = 4


LOCK = config.RUNS_DIR / ".lock"


@contextmanager
def single_run():
    """One Fakturama run at a time, CLI or app. The OS releases the lock if the process dies."""
    import msvcrt

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        raise RuntimeError("another image2cash run is in progress") from None
    try:
        yield
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def busy() -> bool:
    try:
        with single_run():
            return False
    except RuntimeError:
        return True


def run(image: Path, run_dir: Path | None = None, draft: OrderDraft | None = None, resume: bool = False) -> int:
    run_dir = run_dir or config.RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    draft = draft or extract(image)
    (run_dir / "order.json").write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    problems = check_order(draft)
    if problems:
        print("Source rejected before touching Fakturama:")
        for p in problems:
            print("  -", p)
        (run_dir / "rejected.txt").write_text("\n".join(problems), encoding="utf-8")
        return ExitCode.INVALID_SOURCE

    try:
        with single_run():
            return _drive(draft, run_dir, resume, started)
    except RuntimeError as exc:
        print(exc)
        return ExitCode.BUSY


def _drive(draft: OrderDraft, run_dir: Path, resume: bool, started: float) -> int:
    ui = UI(evidence_dir=run_dir / "screenshots")
    ctx = Ctx(ui=ui, order=draft, run_dir=run_dir, resume=resume)
    ctx.record("extract", "ok", engine=draft.engine, min_conf=draft.min_confidence, items=len(draft.items))
    try:
        steps = list(STEPS)
        i = 0
        while i < len(steps):
            name, step = steps[i]
            ctx.record(name, "start")
            step(ctx)
            if ctx.resume_at:
                steps = steps[: i + 1] + RESUME[ctx.resume_at]
                ctx.resume_at = None
            i += 1
        ctx.record("done", "ok", order=ctx.order_no, invoice=ctx.invoice_no, seconds=round(time.time() - started))
        _summary(ctx)
        return ExitCode.OK
    except ManualReview as exc:
        ctx.record("stopped", "REVIEW", reason=str(exc), shot=ctx.shot("manual_review"))
        print(f"\nMANUAL REVIEW: {exc}\nThe Fakturama state is left as-is for a human. Created so far: {ctx.created}")
        return ExitCode.MANUAL_REVIEW
    except (AutomationError, Exception) as exc:  # noqa: BLE001 - record everything
        ctx.record("stopped", "FAIL", reason=str(exc), trace=traceback.format_exc(), shot=ctx.shot("failure"))
        print(f"\nFAILED: {exc}\nCreated so far: {ctx.created}")
        return ExitCode.FAILED
    finally:
        ctx.flush()
        print(f"run log: {run_dir / 'run.json'}")


def _summary(ctx: Ctx) -> None:
    print("\n=== Image-to-Cash complete ===")
    print(
        f"Order   {ctx.order_no}  Cust.Ref. {ctx.order.external_ref}"
        f"  total {ctx.order.gross_total} {ctx.order.currency}"
    )
    print(
        f"Invoice {ctx.invoice_no}  payment {ctx.order.payment.method}"
        f" / {ctx.order.payment.status.value} {ctx.order.payment.paid_date or ''}"
    )
    if ctx.created:
        print("Created master data: " + ", ".join(ctx.created))
    for w in ctx.warnings:
        print("warning:", w)
