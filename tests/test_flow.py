"""flow.run exit codes: only a held lock is 'busy'; everything else is reported as what it is."""

from pathlib import Path

import pytest
import pywinauto.timings

from image2cash import flow
from image2cash.extract.layout import parse
from image2cash.extract.ocr import load

FIXTURE = Path(__file__).parent / "fixtures" / "order_WEB-2026-0714-A17.ocr.json"


def _draft():
    return parse(load(FIXTURE), source="fixture")


def test_fakturama_not_found_is_failed_not_busy(monkeypatch, tmp_path):
    def no_window(**_):
        raise pywinauto.timings.TimeoutError("no Fakturama window")  # a RuntimeError subclass

    monkeypatch.setattr(flow, "UI", no_window)
    assert flow.run(Path("x.png"), tmp_path, draft=_draft()) == flow.ExitCode.FAILED
    assert "no Fakturama window" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_held_lock_is_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "UI", lambda **_: (_ for _ in ()).throw(AssertionError("must not drive the UI")))
    with flow.single_run():
        assert flow.run(Path("x.png"), tmp_path, draft=_draft()) == flow.ExitCode.BUSY


def test_rejected_source_is_logged(tmp_path):
    draft = _draft()
    draft.gross_total = draft.gross_total + 1
    assert flow.run(Path("x.png"), tmp_path, draft=draft) == flow.ExitCode.INVALID_SOURCE
    assert "gross total" in (tmp_path / "run.log").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "code, tries, step",
    [
        (flow.ExitCode.OK, 1, "next"),
        (flow.ExitCode.FAILED, 1, "retry"),
        (flow.ExitCode.FAILED, 2, "skip"),  # tried twice: move on
        (flow.ExitCode.MANUAL_REVIEW, 1, "retry"),
        (flow.ExitCode.MANUAL_REVIEW, 2, "skip"),
        (flow.ExitCode.INVALID_SOURCE, 1, "skip"),
        (flow.ExitCode.BUSY, 1, "pause"),
        (None, 1, "skip"),  # crashed without an exit code we know
    ],
)
def test_batch_step(code, tries, step):
    assert flow.batch_step(code, tries) == step
