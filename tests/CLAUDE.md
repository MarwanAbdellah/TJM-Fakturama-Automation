# tests/

These are unit tests only: they never start or drive Fakturama. Run them with
`uv run pytest -q`; they also run on pre-push.

| File | Covers |
|---|---|
| `test_extract.py` | The parser, on the recorded OCR of the assessment image (`fixtures/*.ocr.json`). Also field boxes, US/CA city lines, resolution independence, and that misread digits are caught. |
| `test_validate.py` | Formulas, `check_order`, `parse_amount` (including Persian digits). |
| `test_rules.py` | Business rules: `sku_eq`/`ocr_eq`, the line-position rule, currency guard, preflight idempotency, `close`, catalogue-price confirmation. |
| `test_ui_helpers.py` | Pure UI helpers: date parsing, grid separators, column naming, netstat parsing. |
| `test_flow.py` | Exit codes. Only a held lock is "busy"; a missing Fakturama is "failed". Also that the run log is written. |
| `test_vision.py` | The opt-in AI fallback against a mock HTTP transport: request shape, parsing, refusal, and that it runs only when asked and only when the parser fails. Skipped without the `llm` extra. |

A new business rule gets a small pure function and a parametrized case here.
