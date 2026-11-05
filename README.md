# Fakturama Image-to-Cash

This tool turns one order image into a saved, verified **Order** and a linked
**Invoice** in Fakturama 2.2.0, with the payment status applied. It drives
Fakturama's real GUI with **Microsoft UI Automation** and uses **local OCR**
(Indra) for the image and for Fakturama's custom-drawn grids. Every step is
verified through UI read-back and **read-only** queries on Fakturama's HSQLDB.

The tool runs from a **Streamlit app**:
1. Upload an image.
2. See what OCR read, with a box drawn around each field.
3. Correct anything that is wrong.
4. Preview what will be created and what will be selected.
5. Press **Proceed**.

It runs from a CLI too.

- Design: [`docs/design.md`](docs/design.md) (v1) and [`docs/design_v2.md`](docs/design_v2.md) (app, scrolling, USD/pharma data)
- Review record: [`docs/egm_review.md`](docs/egm_review.md), the Elephant-Goldfish reviews and what the live app revealed
- Diagrams: [`docs/diagrams/`](docs/diagrams), hand-drawn (architecture, flow, control grounding)
- Evidence: [`docs/screenshots/`](docs/screenshots), annotated, one per step

## Diagrams

**Architecture.** The app and the CLI drive the same flow. Fakturama is only written through its UI; its database is only read.

![Architecture](docs/diagrams/architecture.svg)

**Order flow.** This is the Order-first flow from the brief. Database counts decide between select, create and manual review.

![Order flow](docs/diagrams/flow.svg)

**Control grounding.** How each control is found, in order of preference. No coordinates are stored.

![Control grounding](docs/diagrams/grounding.svg)

## Results (live, test workspace, USD)

The samples are a fictional pharmaceutical distributor's orders (US and Canada,
billed in USD). They were rendered from the JSON specs in `samples/`. Tax
rates: 0 % for prescription items and 6.25 % for over-the-counter supplies.

| Image | What it exercises | Outcome |
|---|---|---|
| `order_RX-1001.png`, Boston pharmacy | New debtor; new product; existing payment and VATs; PAID by Credit Card. **Started from the Streamlit app's Proceed button.** | Order PO000006 and Invoice INV000007, paid, verified. One uninterrupted run. |
| `order_RX-1002.png`, Toronto distributor | Canadian postal code; different delivery warehouse; PAID by Bank Transfer | Order PO000007 and Invoice INV000008 ($633.76, paid), in one uninterrupted run of 109 s. |
| `order_RX-1003.png`, Chicago hospital | **8 lines** (the item table scrolls); new VAT 6.25 %; 8 new products; separate delivery address; UNPAID | Order PO000005 and Invoice INV000006 ($2,035.58). This one was built up over several calibration runs (see "found live" below) and finished with `--resume`. |
| `order_WEB-2026-0714-A17.png`, the assessment's own image (EUR) | Reference input | Booked earlier as PO000002 / INV000003 while the workspace still used its old currency. Under USD it now **stops for manual review**: "order is in EUR, Fakturama books in USD". This is the intended behaviour. |

Each run writes `runs/<id>/run.json` (every step, the values read back and the
DB rows) and a set of annotated screenshots.

## Setup

**Requirements:** Windows 10/11, Fakturama 2.2.0, Python 3.12, [uv](https://docs.astral.sh/uv/), and Node.js (only to regenerate the diagrams).

1. Use a **copy** of the Fakturama workspace; runs create real records. To book
   in USD, set `PREFERENCE_CURRENCY_LOCALE=en/US` in
   `<workspace>/.metadata/.plugins/org.eclipse.core.runtime/.settings/com.sebulli.fakturama.rcp.prefs`.
   Do this **while Fakturama is closed**, because it rewrites the file on exit.
   Then start Fakturama on that workspace:
   ```bash
   "D:/Fakturama/Fakturama2/Fakturama.exe" -data "C:/Users/<you>/.fakturama2_test"
   ```
2. Install the dependencies. The first OCR call downloads Indra's models.
   ```bash
   uv sync
   ```
3. Paths live in `src/image2cash/config.py`. Override them with the environment
   variables `FAKTURAMA_HOME`, `HSQL_URL` and `HSQL_JAR`.

## Run

**The app** (on the PC running Fakturama, at localhost:8501):
```bash
uv run streamlit run app/streamlit_app.py
```
After you press Proceed, don't touch the mouse or keyboard until the page shows the result.

**CLI:** OCR and validate an image only.
```bash
uv run image2cash extract samples/order_RX-1001.png
```
**CLI:** run the full flow.
```bash
uv run image2cash run samples/order_RX-1001.png
```
**CLI:** run a reviewed or edited draft.
```bash
uv run image2cash run --draft runs/<id>/draft.json
```
**CLI:** continue after the Order was saved.
```bash
uv run image2cash run <image> --resume
```
**CLI:** read-only DB view of the documents for a reference.
```bash
uv run image2cash verify RX-1001
```

**Tests and hooks:** 59 unit tests, plus ruff and hygiene hooks; pytest also runs on push.
```bash
uv run pytest -q
```
```bash
uv run pre-commit install --install-hooks -t pre-commit -t pre-push
```

**Tools:** render a synthetic order from a JSON spec.
```bash
uv run python tools/make_sample.py samples/order_RX-1003.json
```
Regenerate the hand-drawn diagrams:
```bash
cd tools/diagrams && npm install && node make_diagrams.mjs
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Done and verified |
| 1 | Automation error |
| 2 | Stopped for manual review (the UI is left as it is) |
| 3 | Source rejected; nothing was touched |
| 4 | Another run is in progress (one run at a time, enforced by an OS file lock) |

## How it works (short)

1. **Extract.**
   - Indra OCR gives text with boxes. A label-anchored parser maps them to an
     `OrderDraft` and records each field's box (the app draws these).
   - US and Canadian address lines (`Boston, MA 02116`, `Toronto, ON M5J 2S1`)
     and German ones (`10117 Berlin`) are both parsed.
   - Arithmetic checks reject misreads before the UI is touched: line totals,
     net, VAT rounded per rate, and gross.
2. **Ground.**
   - Controls are found by UIA name, or failing that from their label, or by
     the options a combo offers.
   - Fakturama's custom-drawn grids are read by OCR inside the rectangle UIA
     reports. Columns come from the drawn grid lines, named by position.
   - Clicks use cell centres computed in the same run.
3. **Decide.** Whether a debtor, product, VAT or payment method already exists
   is a read-only DB count:
   - 0 → create it;
   - 1 → select it in the Order's selector;
   - 2 or more → manual review.
4. **Verify.**
   - Every typed value is read back.
   - After each line, the running `Total Net` must match.
   - Before saving, the totals must equal the image.
   - After each Save, the DB row is checked.
   - At the end: the Invoice is linked to the Order; the paid flag, date, value
     and method are right; the Order is unchanged and still open.

## Fakturama behaviours found live, and how they are handled

| Behaviour | Handling |
|---|---|
| Save throws an NPE when the dynamically added "Delivery address" tab is active (`DocumentEditor.doSave`; the same error appears in the brief's figures) | Select the Invoice-address tab before saving any document |
| The "New product" link throws a ClassCastException (`CallEditor`) after a debtor address was picked in a selector | Use the toolbar's "Create a new product" button |
| Selectors auto-accept as soon as the filter leaves one row | Paste the whole search term at once |
| The Debtor editor loads its Payment list only when it opens | Ensure the payment method *before* opening New Debtor (a documented deviation from brief 2.10) |
| The item table gets its own scrollbar after about 7 rows; the scrollbar buttons ignore UIA Invoke | Maximize the editor area; scroll with the mouse wheel over the grid, and stop if the editor itself moved; the newest line must be the bottom row with `Pos.` = line number |
| Header captions on gray are faint, so OCR reads only some of them | Columns come from drawn grid lines and are named by a majority vote of the captions that were read |
| Segmented date fields | Find each segment by pressing Up and seeing what changed, type the digits, read back |
| Locale differences | Form fields parse in the UI locale; grid cells always take `.` |
| The e-mail validator rejects `.test` domains | Detected in the DB and reported as a warning (samples use `example.com`) |
| "Save Parts" ignores programmatic unchecking | Click the checkbox glyph and verify; cancel if that fails |

## What is skipped or simplified

- **The vision-LLM second opinion for extraction** is designed but not built.
  The parser handles this document family; other layouts would need it.
- **Line-level VAT override** is not automated. An existing product whose VAT
  differs from the image stops for manual review.
- **Order-level discount and shipping** from an image are not mapped. 0 % and
  free shipping are enforced (brief 4.2).
- **Selector dialogs do not scroll.** If a search fills the whole list, the run
  stops for review (`shortcut:` comment in `steps/selector.py`). Exact SKU and
  company searches rarely do.
- **No generic watcher for unexpected dialogs.** The known dialogs are
  handled; anything else times out with a screenshot.
- **Resume only covers "Order saved, Invoice not yet".** An earlier stop needs
  the unsaved Order discarded and a rerun; master data created before the stop
  is reused.
- **Speed:** a line takes about 15 s, because the item grid is re-read by OCR
  before every cell edit. This is deliberate: an Enter can move the table.
- **No cleanup or rollback** of a failed run's records.
- **The app is local only.** It has no authentication and serves one user, so
  don't expose it. The run moves the real mouse, so the desktop must stay
  unlocked and untouched.

## Written question: with 3 more hours

1. **Recovery.** Watch for dialogs between actions. Resume from any step using
   `run.json` and the DB state. Add a `--cleanup` that soft-deletes a run's
   records through the UI.
2. **LLM cross-check.** A schema-constrained vision extraction behind a flag,
   diffed field by field against OCR, so other layouts work too.
3. **Batch regression.** Render a few dozen synthetic orders covering ambiguous
   debtors, VAT conflicts and long orders. Run them nightly against a workspace
   restored from a zipped baseline.
4. **Speed.** Reuse a grid read until the header actually moves. Replace fixed
   sleeps with event waits.

## Repository layout

```
app/streamlit_app.py     upload → boxes → review → preview → proceed → live log → result
src/image2cash/
  extract/   ocr.py (Indra + boxes), layout.py (label-anchored parser, field boxes)
  ui/        core.py (locators, editors, screenshots), grid.py (OCR grids), datefield.py
  steps/     order.py, debtor.py, product.py, invoice.py, selector.py, context.py
  verify/    db.py (read-only HSQLDB)
  models.py  validate.py  flow.py (state machine, run lock)  cli.py  config.py
tests/       59 unit tests + recorded OCR fixture
tools/       make_sample.py (order renderer), diagrams/ (rough.js)
samples/     pharma orders (JSON spec + PNG) + the assessment image
docs/        design.md, design_v2.md, egm_review.md, PLAN_v2.md, diagrams/, screenshots/
```

## Tooling used

- **Elephant-Goldfish Model (EGM).** The designs were drafted with full
  context, then reviewed by zero-context "goldfish" subagents: twice for v1 by
  hand, and twice for v2 with the installed plugin's `egm-design` and
  `egm-implementation-readiness` reviewers. Findings and resolutions are in
  `docs/egm_review.md` and `docs/design_v2.md` §6.
- **Ponytail.** A whole-repo audit removed dead helpers and unused options,
  fixed silently dropped address lines and added tests for the rules that
  guard identity, currency and idempotency.
