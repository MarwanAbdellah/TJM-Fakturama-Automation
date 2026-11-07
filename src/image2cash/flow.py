"""One continuous Order-first flow: image -> saved Order -> linked, paid Invoice."""

from __future__ import annotations

import logging
import sys
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
from image2cash.ui import stop
from image2cash.ui.core import UI, ManualReview, UserStop
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
    STOPPED = 5  # the user pressed Esc


TRIES = 2  # a batch books each order at most this often


def batch_step(code: int, tries: int) -> str:
    """What a batch does after a run ends: 'next' order, 'retry' this one, 'skip' it, or 'pause'.
    A failure or a review stop is tried again (the user's call: a stop can come from the UI);
    a rejection cannot change, because the order data is the same."""
    if code == ExitCode.OK:
        return "next"
    if code in (ExitCode.BUSY, ExitCode.STOPPED):
        return "pause"  # another run holds Fakturama, or the user pressed Esc: wait for Resume
    if code in (ExitCode.FAILED, ExitCode.MANUAL_REVIEW) and tries < TRIES:
        return "retry"
    return "skip"


LOCK = config.RUNS_DIR / ".lock"
log = logging.getLogger("image2cash")


class Busy(RuntimeError):
    """The run lock is held by another image2cash process."""


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
        raise Busy("another image2cash run is in progress") from None
    try:
        yield
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def run(
    image: Path, run_dir: Path | None = None, draft: OrderDraft | None = None, resume: bool = False, ai: bool = False
) -> int:
    run_dir = run_dir or config.RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    handler = _start_log(run_dir)
    try:
        return _run(image, run_dir, draft, resume, time.time(), ai)
    finally:
        log.removeHandler(handler)
        handler.close()


def _start_log(run_dir: Path) -> logging.Handler:
    """Everything (UI actions at DEBUG) to runs/<id>/run.log; step lines to the console."""
    log.setLevel(logging.DEBUG)
    if not any(type(h) is logging.StreamHandler for h in log.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(console)
    handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)-18s %(message)s"))
    log.addHandler(handler)
    return handler


def _run(image: Path, run_dir: Path, draft: OrderDraft | None, resume: bool, started: float, ai: bool = False) -> int:
    log.info("run %s (image=%s, from draft=%s, resume=%s)", run_dir.name, image, draft is not None, resume)
    draft = draft or extract(image, ai)
    (run_dir / "order.json").write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    problems = check_order(draft)
    if problems:
        log.error("source rejected before touching Fakturama: %s", "; ".join(problems))
        (run_dir / "rejected.txt").write_text("\n".join(problems), encoding="utf-8")
        return ExitCode.INVALID_SOURCE

    try:
        with single_run():
            return _drive(draft, run_dir, resume, started)
    except Busy as exc:  # only the lock; UI timeouts are RuntimeErrors too
        log.error("%s", exc)
        return ExitCode.BUSY
    except Exception:  # noqa: BLE001 - e.g. Fakturama not open: fails before the step log exists
        log.exception("could not start driving Fakturama")
        return ExitCode.FAILED


def _drive(draft: OrderDraft, run_dir: Path, resume: bool, started: float) -> int:
    stop.start()  # Esc stops the run at its next action
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
    except UserStop as exc:
        ctx.record("stopped", "STOPPED", reason=str(exc), shot=ctx.shot("stopped_by_user"))
        log.warning("STOPPED by you (Esc). Fakturama is left as-is. Created so far: %s", ctx.created)
        return ExitCode.STOPPED
    except ManualReview as exc:
        ctx.record("stopped", "REVIEW", reason=str(exc), shot=ctx.shot("manual_review"))
        log.warning("MANUAL REVIEW: %s. Fakturama is left as-is for a human. Created so far: %s", exc, ctx.created)
        return ExitCode.MANUAL_REVIEW
    except Exception as exc:  # noqa: BLE001 - record everything
        log.exception("step failed")
        ctx.record("stopped", "FAIL", reason=str(exc), trace=traceback.format_exc(), shot=ctx.shot("failure"))
        log.error("FAILED: %s. Created so far: %s", exc, ctx.created)
        return ExitCode.FAILED
    finally:
        ctx.flush()
        log.info("run log: %s", run_dir / "run.log")


def _summary(ctx: Ctx) -> None:
    o = ctx.order
    log.info("=== Image-to-Cash complete ===")
    log.info("Order   %s  Cust.Ref. %s  total %s %s", ctx.order_no, o.external_ref, o.gross_total, o.currency)
    p = o.payment
    log.info("Invoice %s  payment %s / %s %s", ctx.invoice_no, p.method, p.status.value, p.paid_date or "")
    if ctx.created:
        log.info("Created master data: %s", ", ".join(ctx.created))
    for w in ctx.warnings:
        log.warning("warning: %s", w)
