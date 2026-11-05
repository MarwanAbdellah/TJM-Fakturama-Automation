"""Image -> OrderDraft."""

from __future__ import annotations

from pathlib import Path

from image2cash.extract.layout import ExtractionError, parse
from image2cash.extract.ocr import ocr_image
from image2cash.models import OrderDraft

__all__ = ["extract", "ExtractionError"]


def extract(image: str | Path) -> OrderDraft:
    """Local OCR + label-anchored layout parsing. Deterministic and offline."""
    lines, _ = ocr_image(image)
    return parse(lines, source=str(image))
