# Fakturama Image-to-Cash

This tool turns an order image or PDF into a saved, verified **Order** and a linked
**Invoice** in Fakturama 2.2.0, with the payment status applied. It drives
Fakturama's real GUI with **Microsoft UI Automation** and uses **local OCR**
(Indra) for the image and for Fakturama's custom-drawn grids. Every step is
verified through UI read-back and **read-only** queries on Fakturama's HSQLDB.

The tool runs from a **Streamlit app**:
1. Upload one or more images or PDFs (each becomes a row in a queue).
2. See what OCR read, with a box drawn around each field.
3. Correct anything that is wrong.
4. Preview what will be created and what will be selected.
5. Press **Book this order**, or **Book all ready** to book the queue one
   order at a time.

It runs from a CLI too.

- Design: [`docs/design.md`](docs/design.md) (v1), [`docs/design_v2.md`](docs/design_v2.md) (app, scrolling, USD/pharma data) and [`docs/design_v3.md`](docs/design_v3.md) (speed, queue, PDF, catalogue prices, unseen layouts)
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

Each run writes these files to `runs/<id>/`:

| File | Contents |
|---|---|
| `run.json` | Every step, the values read back and the DB rows |
| `run.log` | Timestamped log of every step and every UI action: clicks, typed fields, combo choices, dialogs, saves, grid reads, timeouts |
| `output.log` | Raw console output, for crashes before logging starts |
| `screenshots/` | Annotated screenshots |

The app shows `run.log` live while a run is going, and expanded when a run
fails. It also keeps its own log in `runs/app.log`: extractions, each Proceed
or Resume with its command, and each exit code.

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
   variables `FAKTURAMA_HOME`, `FAKTURAMA_WORKDIR`, `HSQL_URL` and `HSQL_JAR`.
   - **Only the configured instance is ever driven.** That is the window titled
     `Fakturama - <FAKTURAMA_WORKDIR>`, and only if that same process owns the
     database port.
   - If another Fakturama is open (for example your live workspace), it owns
     port 9001. Runs then refuse to start, and the app disables Proceed and says
     why.
   - Close the other instance, then start the test one.

## Run

**The app** (on the PC running Fakturama, at localhost:8501):
```bash
uv run streamlit run app/streamlit_app.py
```
After you press a Book button, don't touch the mouse or keyboard until the page shows the result.

**CLI:** OCR and validate an image or PDF only.
```bash
uv run image2cash extract samples/order_RX-1001.png
```
**Opt-in AI fallback** for layouts the parser can't read. It sends the image
to Anthropic, so it is off unless you ask for it (`--ai` here, a toggle in the
app). It needs the extra and a key:
```bash
uv sync --extra llm
```
```bash
uv run image2cash extract samples/unseen/hospital_po.png --ai
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

**Tests and hooks:** 69 unit tests, plus ruff and hygiene hooks; pytest also runs on push.
```bash
uv run pytest -q
```
```bash
uv run pre-commit install --install-hooks -t pre-commit -t pre-push
```

**Tools:** render the four unseen-layout orders into `samples/unseen/`.
```bash
uv run python tools/make_unseen.py
```
Render a synthetic order from a JSON spec.
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

- **Unseen layouts need the AI fallback.** The local parser reads one
  document family. The four orders in `samples/unseen/` (a hospital PO, a
  rotated noisy fax, a two-column distributor form, a PDF letter) are all
  rejected offline (exit 3, "label 'CONTACT NAME' not found"), and nothing is
  touched. With `--ai` (or the app toggle) Claude reads them into the same
  `OrderDraft`; that draft has no field boxes, so the app asks you to check
  every field, and `check_order` plus the DB checks still apply. The fallback
  is unit-tested against a mock API only: no API key was available here.
- **Prices are never typed.** An existing product's catalogue net price must
  match the image to the cent, or the run stops for review. Vendors change
  prices in the product master, not on an order. Totals may differ by at most
  1 cent per line (gross-entered prices leave a net remainder); drift is a
  warning.
- **PDF input uses page 1 only**, rendered at 200 dpi.
- **The queue lives in the browser tab.** Reloading the page loses it. It
  continues after "done" or "rejected" and pauses after "failed", "manual
  review" or "busy" until you press Resume.
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
- **Speed:** the 8-line order took 264 s, mostly OCR re-reads of the item
  grid. Now a line reuses the read that proved it and only edits Qty and
  Discount when they differ from the default; a running-total check catches a
  wrong row. Expected: about one grid read per line instead of five. Not yet
  re-timed live.
- **No cleanup or rollback** of a failed run's records.
- **The app is local only.** It has no authentication and serves one user, so
  don't expose it. The run moves the real mouse, so the desktop must stay
  unlocked and untouched.

## Written question: with 3 more hours

1. **Recovery.** Watch for dialogs between actions. Resume from any step using
   `run.json` and the DB state. Add a `--cleanup` that soft-deletes a run's
   records through the UI.
2. **LLM cross-check.** The vision fallback exists; next, run it on every
   order and diff it field by field against OCR, flagging disagreements.
3. **Batch regression.** Render a few dozen synthetic orders covering ambiguous
   debtors, VAT conflicts and long orders. Run them nightly against a workspace
   restored from a zipped baseline.
4. **Speed.** Reuse a grid read until the header actually moves. Replace fixed
   sleeps with event waits.

## Repository layout

```
app/streamlit_app.py     upload → boxes → review → preview → proceed → live log → result
src/image2cash/
  extract/   ocr.py (Indra + boxes, PDF page 1), layout.py (label-anchored parser, field boxes),
             vision.py (opt-in Claude fallback)
  ui/        core.py (locators, editors, screenshots), grid.py (OCR grids), datefield.py
  steps/     order.py, debtor.py, product.py, invoice.py, selector.py, context.py
  verify/    db.py (read-only HSQLDB)
  models.py  validate.py  flow.py (state machine, run lock)  cli.py  config.py
tests/       69 unit tests + recorded OCR fixture
tools/       make_sample.py (order renderer), make_unseen.py (4 unseen layouts), diagrams/ (rough.js)
samples/     pharma orders (JSON spec + PNG), the assessment image, unseen/ (4 layouts + truth)
docs/        design.md, design_v2.md, design_v3.md, egm_review.md, PLAN_v2.md, diagrams/, screenshots/
```

## Tooling used

- **Elephant-Goldfish Model (EGM).** The designs were drafted with full
  context, then reviewed by zero-context "goldfish" subagents: twice for v1 by
  hand, and twice for v2 with the installed plugin's `egm-design` and
  `egm-implementation-readiness` reviewers. Findings and resolutions are in
  `docs/egm_review.md`, `docs/design_v2.md` §6 and `docs/design_v3.md` §6
  (design and zoom-out reviewers on v3).
- **CLAUDE.md in each source folder** records that folder's rules and the
  Fakturama quirks it works around, for the next agent or developer.
- **Ponytail.** A whole-repo audit removed dead helpers and unused options,
  fixed silently dropped address lines and added tests for the rules that
  guard identity, currency and idempotency.
