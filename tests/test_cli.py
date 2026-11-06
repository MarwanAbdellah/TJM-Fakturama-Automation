from pathlib import Path

import image2cash.extract as extract_mod
from image2cash import cli
from image2cash.extract import ExtractionError


def test_rejected_source_exits_3(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise ExtractionError("label 'CONTACT NAME' not found")

    monkeypatch.setattr(extract_mod, "extract", boom)
    assert cli.main(["extract", str(Path("x.png"))]) == 3
    assert "CONTACT NAME" in capsys.readouterr().err
