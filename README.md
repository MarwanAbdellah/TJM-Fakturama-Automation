# Fakturama Image-to-Cash

This tool turns one order image into a saved, verified **Order** and a linked
**Invoice** in Fakturama 2.2.0. The payment status is applied too. It drives
Fakturama's real GUI with **Microsoft UI Automation**, uses **local OCR**
(Indra) for the image and for Fakturama's custom-drawn grids, and verifies
every step through UI read-back and **read-only** queries on Fakturama's
HSQLDB.

- Design: [`docs/design.md`](docs/design.md)
- Review record: [`docs/egm_review.md`](docs/egm_review.md), the
  Elephant-Goldfish review, its resolutions, and what the live app revealed.
- Evidence: [`docs/screenshots/`](docs/screenshots), annotated, one per step.

## Results (live, against the test workspace)

| Source image | Branches exercised | Outcome |
|---|---|---|
| `samples/order_WEB-2026-0714-A17.png` (the assessment image) | New debtor with a **different delivery address**; existing payment method; new VAT 19 %; 2 new products; 10 % line discount; **PAID** | Order **PO000002** (678.30) and Invoice **INV000003**. Paid 2026-07-18, value 678.30, Bank Transfer, linked to the Order. |
| `samples/order_SO-TEST-0002.png` (synthetic) | New debtor with billing = delivery; **new payment method** (SEPA direct debit, code 59); **new VAT 7 %**; one new and one existing product; mixed VAT; **UNPAID** | Order PO000003 (439.81) and Invoice INV000004, unpaid. |
| `samples/order_SO-TEST-0003.png` (synthetic) | Existing debtor and product picked from the selectors; one new product; decimal prices; 20 % discount; **PAID** by Credit Card (new payment, code 54) | **One uninterrupted run, 100 s**: Order PO000004 (273.46) and Invoice INV000005, paid 2026-09-23. |

Some of these results came from more than one run, and the history matters:

- **The assessment image.** It was built up over several calibration runs.
  During those runs I found a Fakturama bug: Save fails while the "Delivery
  address" tab is active. I saved that order by hand after selecting the
  Invoice-address tab, then fixed the code and finished the run with
  `--resume`.
- **SO-TEST-0002.** It ran in one go up to the saved Order, then stopped on a
  bug in my logging code, then finished with `--resume`.
- **SO-TEST-0003.** It ran with all fixes in place and needed no help.

On every run, each step was checked against the DB (see `runs/*/run.json`).

## Setup

**Requirements.** Windows 10/11, Fakturama 2.2.0, Python 3.12 and
[uv](https://docs.astral.sh/uv/).

1. Use a **copy** of the Fakturama workspace. The run creates real records.
   Start Fakturama on it, for example:
   ```bash
   "D:/Fakturama/Fakturama2/Fakturama.exe" -data "C:/Users/<you>/.fakturama2_test"
   ```
   The automation attaches to the running window. While Fakturama is open,
   HSQLDB listens on `localhost:9001`.
2. Install the dependencies:
   ```bash
   uv sync
   ```
   The first OCR call downloads Indra's models, about 12 files.
3. Paths are set in `src/image2cash/config.py`. Override them with
   environment variables: `FAKTURAMA_HOME`, `HSQL_URL`, `HSQL_JAR`.
   The JDBC driver and the JRE are taken from the Fakturama install.

## Run

```bash
uv run image2cash extract samples/order_WEB-2026-0714-A17.png
```
This only reads the image and validates it. It prints the JSON and the
validation result, and does not touch the UI.

```bash
uv run image2cash run samples/order_WEB-2026-0714-A17.png
```
This runs the full flow. Do not use the mouse or keyboard while it runs.

```bash
uv run image2cash run <image> --resume
```
This continues a stopped run whose Order is already saved. It verifies the
Order, then creates and pays the Invoice.

```bash
uv run image2cash verify WEB-2026-0714-A17
```
This prints a read-only DB view of the documents for that reference.

```bash
uv run pytest -q
```
This runs the 35 unit tests: extraction from a recorded OCR fixture,
validation, and the grid, date and selector helpers.

```bash
uv run python tools/make_sample.py samples/order_SO-TEST-0002.json
```
This renders a synthetic test order.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Done and verified |
| `1` | Automation error |
| `2` | Stopped for manual review; the UI is left as-is |
| `3` | Source image rejected by validation; nothing touched |

Each run writes `runs/<timestamp>/run.json`, `order.json` and annotated
`screenshots/`.

## How it works (short)

1. **Extract.** Indra OCR gives text with boxes. A label-anchored parser maps
   them to an `OrderDraft`; values are located relative to their printed
   labels, with no fixed coordinates. Arithmetic cross-checks (line totals,
   net, VAT per rate, gross) reject misreads before the UI is touched.
2. **Ground.** Controls are found by UIA name (toolbar tooltips, nav links,
   named edits and checkboxes). If there is no name, the control is found from
   its label (nearest field to the right or below; N-th icon beside a label).
   Combos are found by their options. Fakturama's custom-drawn NatTable grids
   are read by OCR inside the rectangle UIA reports. Columns come from the
   drawn grid lines, and clicks go to cell centres computed in the same run.
3. **Decide.** "Exact match" for debtor, product, VAT and payment method is a
   read-only DB count:
   - 0 → create;
   - 1 → select in the Order's selector;
   - 2 or more → manual review.

   The selector is still used as the brief requires; the DB only decides
   whether a match is exact.
4. **Verify.** Every typed value is read back. Lines are checked against the
   running `Total Net`, and the totals against the source. After each Save
   the DB row is checked. The final checks are:
   - the Invoice is linked to the Order;
   - paid flag, pay date, paid value and payment method are correct;
   - the Order is unchanged and still open.

## Fakturama behaviours handled (found during calibration)

- **Save NPE.** Saving a document while the dynamically added "Delivery
  address" tab is active throws a NullPointerException in
  `DocumentEditor.doSave`. The same "Failed to persist contents of part" error
  appears in the brief's figures. Fix: select the Invoice-address tab before
  saving.
- **Selector auto-accept.** The selectors auto-accept as soon as the filter
  leaves one row. Fix: search terms are pasted whole, never typed key by key.
- **Payment list not refreshed.** The Debtor editor loads its Payment list
  once, when it opens. Fix: the payment method is ensured *before* New Debtor
  opens. This is a documented deviation from the order in brief 2.10; the
  Order stays open either way.
- **Segmented dates.** Date fields only take keyboard input one segment at a
  time. WM_SETTEXT changes the display but not the model.
- **Locale.** This workspace's ps/AF locale shows Persian digits. Form fields
  need the `٫` decimal separator, while grid cells need `.`.
- **E-mail validator.** Fakturama's validator rejects `example.test` addresses
  and stores no e-mail. This is detected in the DB and reported as a warning.
- **Save Parts.** The "Save Parts" dialog ignores programmatic unchecking, so
  the checkbox glyph is clicked and the state verified.

## What is not done / simplified

- The vision-LLM second opinion for extraction is designed (`docs/design.md`
  §3) but not built. OCR and the arithmetic checks were sufficient for the
  samples.
- Line-level VAT override is not automated. An existing product whose VAT
  differs from the source goes to manual review.
- Order-level discount and shipping values in an image are not mapped. The
  flow enforces 0 % / free shipping, as brief 4.2 states for images without
  them.
- There is no generic watcher for unexpected dialogs. The known dialogs are
  handled; anything else times out with a screenshot.
- The parser handles this document family, with its labelled sections. Other
  layouts would need the LLM path.
- Resume covers "Order saved, Invoice not yet". An earlier stop needs the
  unsaved Order discarded and a rerun. Master data created before the stop is
  reused on the rerun, because exact matches are then found.
- The run needs an unattended desktop: it captures the screen and moves the
  mouse.

## Written question: with 3 more hours

1. **Harden recovery.** Add a dialog watcher that runs between actions and
   classifies or aborts unknown modals. Make resume work from every step,
   driven by `run.json` and DB state. Add a `--cleanup` that soft-deletes this
   run's records through the UI.
2. **LLM cross-check.** Add a schema-constrained vision-LLM extraction behind a
   flag, with a normalized field-by-field diff against OCR. Disagreement on a
   key or money field goes to manual review. This also makes the tool robust
   to other order layouts.
3. **More test coverage.** Generate a batch of synthetic orders (the renderer
   exists) covering ambiguous debtors, duplicate SKUs, VAT conflicts, unknown
   payment methods and three or more lines that need scrolling the item table.
   Run them nightly against a disposable workspace copy, restored from a
   zipped baseline before each run.
4. **Line-level VAT.** Set the in-cell VAT combo when an existing product's
   VAT differs, instead of stopping for review.
5. **Speed.** Cache the UIA subtree per editor and replace fixed settle sleeps
   with event waits. Today a full run takes about 100 s.

## Repository layout

```
src/image2cash/
  extract/   ocr.py (Indra + boxes), layout.py (label-anchored parser)
  ui/        core.py (locators, actions, editors, screenshots), grid.py (OCR grids),
             datefield.py (segmented date entry)
  steps/     order.py, debtor.py, product.py, invoice.py, selector.py, context.py
  verify/    db.py (read-only HSQLDB)
  models.py  validate.py  flow.py  cli.py  config.py
tests/       35 unit tests + recorded OCR fixture
tools/       make_sample.py (synthetic order renderer)
samples/     assessment image + two synthetic orders (JSON specs + PNG)
docs/        design.md, egm_review.md, screenshots/
```

## About the Elephant-Goldfish Model

The design was drafted and stress-tested with Google's EGM method. A
context-rich "elephant" agent drafted it; two zero-context "goldfish" reviewer
subagents critiqued it; the findings were resolved and the risky mechanics
spiked before implementation. See `docs/egm_review.md`.

The EGM plugin was not installed into Claude Code. Local policy requires an
NVIDIA SkillSpector scan of any plugin first, and installing the scanner was
blocked here. The workflow was applied by hand instead.
