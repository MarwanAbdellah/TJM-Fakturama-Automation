# extract/

- `__init__.extract_draft(path)` is **the** entry point for the CLI and the app.
  It returns an `Extraction` with the image the boxes refer to, the draft or an
  error, and the field boxes. Don't call `ocr_image` and `parse` separately in
  new code.
- `ocr.py`: Indra OCR with boxes. `as_image` renders page 1 of a PDF to
  `<name>.page1.png` at 200 dpi.
- `layout.py`: a label-anchored parser for the "SALES ORDER INPUT" family.
  Values are found relative to their printed labels, and thresholds scale with
  the median text height.

## Gotchas

- Address lines:
  - `City, ST ZIP` and `City, PR A1A 1A1` (US/CA) are tried before `ZIP City`.
  - Fakturama has no state field, so the city becomes `Boston, MA`.
- E-mail: OCR inserts spaces into e-mail addresses, so they are stripped.
- OCR drops some table header captions: two-line headers come back as boxes
  without text.
- Unseen layouts (`samples/unseen/`) are rejected with `ExtractionError`
  offline, on purpose.
- `vision.py` is the AI fallback: `extract_draft(path, ai=True)` calls it only
  when the parser fails or `check_order` finds problems. It is **opt-in only**
  (`--ai`, the app toggle) because it sends the image to Anthropic. It needs
  `uv sync --extra llm` and a key. Its draft has no boxes and
  `engine="claude-vision"`; never let it skip `check_order` or human review.
  `tests/test_vision.py` covers it with a mock transport, no network.
