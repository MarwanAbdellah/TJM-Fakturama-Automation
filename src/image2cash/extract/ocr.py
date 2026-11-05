"""Local OCR (Indra) with boxes. Used for the source image and for UI grids."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Line:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def overlaps_x(self, other: Line | tuple[float, float], slack: float = 0) -> float:
        lo, hi = (other.x0, other.x1) if isinstance(other, Line) else other
        return max(0.0, min(self.x1, hi + slack) - max(self.x0, lo - slack))


@lru_cache(maxsize=1)
def _engine():
    import indra_ocr

    return indra_ocr.load(threads=4, dpi=200, mode="ocr")


def pdf_to_png(pdf: Path, png: Path, dpi: int = 200) -> int:
    """Render page 1 of ``pdf`` to ``png``; returns the PDF's page count (one order per image)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        doc[0].render(scale=dpi / 72).to_pil().convert("RGB").save(png)
        return len(doc)
    finally:
        doc.close()


def as_image(path: Path) -> Path:
    """PDFs become a PNG next to them (page 1); images pass through."""
    if path.suffix.lower() != ".pdf":
        return path
    png = path.with_suffix(".page1.png")
    if not png.exists() or png.stat().st_mtime < path.stat().st_mtime:
        pdf_to_png(path, png)
    return png


def ocr_image(path: str | Path) -> tuple[list[Line], tuple[int, int]]:
    """OCR one image (a PDF is rendered to PNG first). Returns lines in image pixels and the size."""
    from PIL import Image

    path = as_image(Path(path))
    with Image.open(path) as img:
        size = img.size
    for _, page in _engine().convert(str(path), max_pages=1):
        lines = [Line(l.text.strip(), float(l.conf), *(float(v) for v in l.box)) for l in page.lines]
        return lines, size
    return [], size


def ocr_pil(img, scratch: Path, scale: int = 2) -> list[Line]:
    """OCR a PIL image (a UI capture). Upscales small UI fonts first.

    Returned boxes are in the coordinates of the *original* capture.
    """
    from PIL import Image

    scratch.parent.mkdir(parents=True, exist_ok=True)
    big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    big.save(scratch)
    lines, _ = ocr_image(scratch)
    return [Line(l.text, l.conf, l.x0 / scale, l.y0 / scale, l.x1 / scale, l.y1 / scale) for l in lines]


def load(path: Path) -> list[Line]:
    return [Line(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
