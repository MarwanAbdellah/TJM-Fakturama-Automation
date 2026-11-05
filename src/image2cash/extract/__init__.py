"""Image or PDF -> OrderDraft. The one extraction entry point for the CLI and the app."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from image2cash.extract.layout import ExtractionError, parse
from image2cash.extract.ocr import as_image, ocr_image
from image2cash.models import OrderDraft

__all__ = ["Extraction", "ExtractionError", "extract", "extract_draft"]


@dataclass
class Extraction:
    image: Path  # the raster the boxes refer to (page 1 for a PDF)
    draft: OrderDraft | None
    boxes: dict = field(default_factory=dict)  # field key -> [[x0, y0, x1, y1], ...] in `image` pixels
    error: str | None = None
    engine: str = "indra-ocr+layout"


def extract_draft(path: str | Path, ai: bool = False) -> Extraction:
    """Local OCR + label-anchored layout parsing. Deterministic and offline; never raises
    for an unreadable layout (returns ``error`` instead).

    ``ai=True`` (explicit opt-in: it sends the image to an AI model) asks it to read the
    image when the parser fails or its draft does not pass ``check_order``."""
    image = as_image(Path(path))
    lines, _ = ocr_image(image)
    boxes: dict = {}
    try:
        local = Extraction(image, parse(lines, source=str(path), boxes=boxes), boxes)
    except ExtractionError as exc:
        local = Extraction(image, None, boxes, str(exc))
    if not ai or (local.draft is not None and not _problems(local.draft)):
        return local
    from image2cash.extract.vision import ENGINE, VisionError, vision_draft

    try:
        return Extraction(image, vision_draft(image), {}, None, ENGINE)
    except VisionError as exc:
        if local.draft is None:
            local.error = f"{local.error}; AI fallback: {exc}"
        return local


def _problems(draft: OrderDraft) -> list[str]:
    from image2cash.validate import check_order

    return check_order(draft)


def extract(image: str | Path, ai: bool = False) -> OrderDraft:
    """CLI path: the draft, or ExtractionError."""
    result = extract_draft(image, ai)
    if result.draft is None:
        raise ExtractionError(result.error)
    return result.draft
