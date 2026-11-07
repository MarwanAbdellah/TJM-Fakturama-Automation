[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Fakturama](https://img.shields.io/badge/Fakturama-2.2.0-1F6FB2.svg)](https://www.fakturama.info/)
[![Windows UI Automation](https://img.shields.io/badge/Windows-UI%20Automation-0078D4.svg)](https://learn.microsoft.com/windows/win32/winauto/entry-uiauto-win32)
[![pywinauto](https://img.shields.io/badge/pywinauto-0.6.9-3776AB.svg)](https://pywinauto.readthedocs.io/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.66-FF4B4B.svg)](https://streamlit.io/)
[![Pydantic](https://img.shields.io/badge/Pydantic-2.14-E92063.svg)](https://docs.pydantic.dev/)
[![OCR](https://img.shields.io/badge/OCR-indra--ocr%201.1-6A5ACD.svg)](https://pypi.org/project/indra-ocr/)
[![pypdfium2](https://img.shields.io/badge/PDF-pypdfium2%205.14-8A2BE2.svg)](https://pypdfium2.readthedocs.io/)
[![HSQLDB](https://img.shields.io/badge/HSQLDB-JayDeBeApi%201.2-8B4513.svg)](https://github.com/baztian/jaydebeapi)
[![uv](https://img.shields.io/badge/uv-managed-green.svg)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)
[![Ruff](https://img.shields.io/badge/lint%20%26%20format-ruff-E6001A.svg)](https://docs.astral.sh/ruff/)

# Fakturama Image-to-Cash

This tool turns an order image or PDF into a saved, verified **Order** and a linked
**Invoice** in Fakturama 2.2.0, with the payment status applied. It drives
Fakturama's real GUI with **Microsoft UI Automation**, reads the image and
Fakturama's custom-drawn tables with **local OCR** (Indra), and checks every step
through UI read-back and **read-only** queries on Fakturama's database.

> [!IMPORTANT]
> **▶ Watch the demo (79 s): [youtu.be/JQI2nbLEsoI](https://youtu.be/JQI2nbLEsoI)**
>
> [![Image-to-Cash demo video](https://img.youtube.com/vi/JQI2nbLEsoI/hqdefault.jpg)](https://youtu.be/JQI2nbLEsoI)

- **Architecture and design rationale (3 pages):** [docs/paper/architecture.pdf](docs/paper/architecture.pdf) (LaTeX source alongside)
- **Design:** [`docs/design.md`](docs/design.md), [`docs/design_v2.md`](docs/design_v2.md), [`docs/design_v3.md`](docs/design_v3.md)
- **Reviews:** [`docs/egm_review.md`](docs/egm_review.md) and [`docs/egm_review_v4.md`](docs/egm_review_v4.md) (Elephant-Goldfish)
- **Diagrams:** [`docs/diagrams/`](docs/diagrams) · **Evidence:** [`docs/screenshots/`](docs/screenshots)

## Quick start

![Setup, step by step](docs/diagrams/setup.svg)

```bash
git clone https://github.com/MarwanAbdellah/TJM-Fakturama-Automation.git
```
```bash
cd TJM-Fakturama-Automation
```
```bash
uv run image2cash setup
```
```bash
uv run streamlit run app/streamlit_app.py
```

`setup` installs the packages (like `uv sync`) and downloads the OCR models once.
The app opens at `localhost:8501`. Three things to know:

- **Prepare Fakturama once.** Start it, pick a working directory (a new folder,
  never your real data), and set **File → Preferences** currency locale to
  Germany (`de/DE`). A New Order's Total must show `€`. Keep it open: Book uses
  the Fakturama that is open, and starts one if none is.
- **Hands off while it books.** It moves the real mouse and keyboard. Press
  **Esc** to stop it before its next action.
- **Requirements:** Windows 10/11, Fakturama 2.2.0 in English, display scaling
  at 100 %, Python 3.12, [uv](https://docs.astral.sh/uv/) and git.

## Using the app

1. Pick a bundled sample (**Home Test Example** is preselected) or upload your own
   images or PDFs. Each becomes an order in the list.
2. Check what OCR read (a box is drawn around each field) and correct anything.
3. The **Checks** table previews what Fakturama will select or create.
4. **Book this order** books the one shown. **Book all ready** books the list one
   order at a time:
   - a failed or stopped order is tried once more, then skipped;
   - an order already booked (Order and Invoice) is skipped without running; one
     with only its Order saved gets its Invoice;
   - **Esc** stops the current order and pauses the queue (**Resume queue** goes on);
   - each skipped order is listed with the reason.
5. Each new order first closes the finished Order and Invoice tabs of the last
   one (unsaved tabs are never closed). At the end, the Documents list opens with
   **Invoices** selected, enlarged so it can be checked.

**Runs** pages through finished runs: step table, log and screenshots.

## How it works

![Architecture](docs/diagrams/architecture.svg)

![Order flow](docs/diagrams/flow.svg)

1. **Extract.** Indra OCR gives text with boxes; a label-anchored parser maps
   them to an `OrderDraft`. Arithmetic checks (line totals, net, VAT per rate,
   gross) reject a misread before the UI is touched.
2. **Ground.** Controls are found by UIA name, then by their label, then by the
   options a combo offers. Fakturama's custom-drawn grids are read by OCR, with
   columns taken from the drawn grid lines. No coordinates are stored.
3. **Decide.** Whether a debtor, product, VAT or payment method exists is a
   read-only database count: 0 → create, 1 → select, 2 or more → manual review.
4. **Verify.** Every typed value is read back; the running Total Net must match
   after each line; after each Save the database row is checked; at the end the
   Invoice is linked to the Order, with the right paid flag, date, value and method.

![Control grounding](docs/diagrams/grounding.svg)

## Configuration

Nothing is tied to one PC; everything below is found on each machine
(`src/image2cash/config.py`). Set a value in `.env` only if detection fails.

| Setting | Found from | Override |
|---|---|---|
| Fakturama's folder | the running `Fakturama.exe`, where it last ran, `PATH`, the App Paths registry, Windows' installed-programs list, any `Fakturama*` folder in Program Files | `FAKTURAMA_HOME` |
| Workspace | `.fakturama2_test` in your user folder | `FAKTURAMA_WORKSPACE` |
| Working directory, database login | that workspace's Fakturama preferences | `FAKTURAMA_WORKDIR`, `HSQL_URL`, `HSQL_USER`, `HSQL_PASSWORD` |
| HSQLDB driver | the `org.hsqldb.hsqldb_*.jar` Fakturama ships | `HSQL_JAR` |

**Which Fakturama is driven:** the test one if it is open, otherwise the one
Fakturama that is open, and only if that same program serves the database (port
9001), so checks never read another instance's data. With two open, or one still
starting (its title says `null`), the run stops and says why. If you also keep a
live Fakturama, have the test one (or none) open when you book.

## CLI

```bash
uv run image2cash extract samples/order_RX-2001.png
```
```bash
uv run image2cash run samples/order_RX-2001.png
```
```bash
uv run image2cash run --draft runs/<id>/draft.json
```
```bash
uv run image2cash verify RX-2001
```

`extract` only reads and checks an image; `run` books it; `run --draft` books a
reviewed draft; `verify` shows the documents for a reference (read-only). A
reference with a saved Order and no Invoice continues at the Invoice by itself.

| Exit code | Meaning |
|---|---|
| 0 | Done and verified |
| 1 | Automation error |
| 2 | Stopped for manual review (Fakturama is left as it is) |
| 3 | Source rejected; nothing was touched |
| 4 | Another run is in progress (one run at a time) |
| 5 | Stopped by you (Esc) |

**Tests and hooks:** 306 unit tests, plus ruff and hygiene hooks; pytest also runs on push.
```bash
uv run pytest -q
```
```bash
uv run pre-commit install --install-hooks -t pre-commit -t pre-push
```
Regenerate the diagrams with `cd tools/diagrams && npm install && node make_diagrams.mjs`.

## Results

The samples are a fictional pharmaceutical distributor's orders (rendered from the
JSON specs in `samples/`): 0 % VAT for prescription items, 6.25 % for
over-the-counter supplies. They were first booked in USD as RX-1001 to RX-1003;
the workspace now books in EUR, so they were re-rendered as `order_RX-2001` to
`order_RX-2003` (same customers and amounts, new references).

| Image | What it exercises | Outcome |
|---|---|---|
| `order_RX-1001.png`, Boston pharmacy | New debtor and product; PAID by Credit Card; started from the app | Order PO000006, Invoice INV000007, paid, verified |
| `order_RX-1002.png`, Toronto distributor | Canadian postal code; separate delivery warehouse; PAID by Bank Transfer | Order PO000007, Invoice INV000008 ($633.76), 109 s |
| `order_RX-1003.png`, Chicago hospital | 8 lines (the table scrolls); new VAT 6.25 %; 8 new products; UNPAID | Order PO000005, Invoice INV000006 ($2,035.58) |
| `order_WEB-2026-0714-A17.png`, the brief's own image (EUR) | Reference input ("Home Test Example") | Booked and verified in the EUR workspace |

Each run leaves `runs/<id>/`: `run.json` (every step, values read back, DB rows),
`run.log` (every UI action, timestamped), `output.log` (raw console, from the
app) and annotated `screenshots/`. The app keeps its own log in `runs/app.log`.

## Fakturama behaviours found live, and how they are handled

| Behaviour | Handling |
|---|---|
| Save throws an NPE when the dynamic "Delivery address" tab is active (also visible in the brief's figures) | Select the Invoice-address tab before saving |
| The "New product" link throws a ClassCastException after an address was picked | Use the toolbar's "Create a new product" button |
| Selectors auto-accept as soon as the filter leaves one row | Paste the whole search term at once |
| The Debtor editor loads its Payment list only when it opens | Ensure the payment method before New Debtor (a documented deviation from brief 2.10) |
| A narrow selector cuts off the City column; OCR can miss a name | A clearly read customer number decides the DB-confirmed row |
| "Duplicate Contact" warning on the same name and street | Checked in the DB first; the dialog is dismissed and the run stops for review |
| The same address written two ways (`04101 Portland, ME` vs `ME 04101 Portland`) | Taken as the saved contact's spelling |
| The item table scrolls after about 7 rows; its scrollbar ignores UIA | Mouse-wheel over the grid; each row is checked by its `Pos.` |
| Faint gray header captions | Columns from drawn grid lines, named by a majority vote |
| OCR joins a right-aligned quantity to the SKU beside it | The number is given back to Qty |
| Segmented date fields | Paste in the field's format, read back; step segments as a fallback |
| VAT percent takes `.` while price fields take `,` (`6,25` became 625 %) | Try `.`, then `,`, and read back |
| The e-mail validator rejects `.test` domains | Detected in the DB, reported as a warning |
| Documents opens as a floating panel over the editor, cut off on a small screen | Enlarged by its edges at the end of a run |
| The window title says `Fakturama - null` while it starts or asks for a working directory | Never driven until it shows a folder |
| `netstat` lists closed connections under pid 0, before the real listener | pid 0 is never taken as the database owner |
| A small or resized window puts fields off-screen | Maximize Fakturama, again whenever it was resized |
| "Save Parts" ignores programmatic unchecking | Click the checkbox glyph and verify; cancel if that fails |

## What is skipped or simplified

- **One document family offline.** The local parser reads the "Sales Order Input"
  layout; other layouts are rejected (exit 3) and nothing is touched.
- **AI fallback: still experimental.** For other layouts there is an opt-in AI
  reader (the app's toggle, `--ai` on the CLI; off by default, because the image
  leaves the machine). Its result goes through the same checks and your review,
  but it is not reliable enough to trust yet; see the paper.
- **Prices are never typed.** A product's catalogue price must match the image to
  the cent, or the run stops for review; totals may then differ by a cent per line,
  reported as a warning.
- **PDF input uses page 1 only.**
- **The queue lives in the browser tab**; reloading the page loses it.
- **Not mapped:** line-level VAT overrides, order-level discount and shipping
  (0 % and free shipping are enforced, brief 4.2).
- **Selector dialogs do not scroll**; a search that fills the list stops for review.
- **No generic watcher for unexpected dialogs**, and **no cleanup or rollback** of a
  failed run's records. Resume covers "Order saved, Invoice not yet".
- **Speed:** an 8-line order takes about 1.5 to 2 minutes; each queued order starts
  a fresh process, about 10 s between orders.
- **Local only:** no authentication, one user, and the desktop must stay unlocked.

## Written question: with 3 more hours

1. **Recovery.** Watch for dialogs between actions; resume from any step using
   `run.json` and the DB state; a `--cleanup` that removes a failed run's records.
2. **Batch regression.** A few dozen synthetic orders (ambiguous debtors, VAT
   conflicts, long orders) run nightly against a workspace restored from a baseline.
3. **Reproducible environment.** A Docker image for everything that needs no screen
   (OCR, parsing, checks, tests, with the OCR models baked in), plus a scripted
   Windows VM or Windows Sandbox for full runs: UI Automation needs a real Windows
   desktop, which a container does not have.
4. **Speed.** Run a whole queue in one process instead of one per order.
5. More in the [paper](docs/paper/architecture.pdf).

## Repository layout

```
app/streamlit_app.py     samples / upload → review → checks → book → runs
src/image2cash/
  extract/   ocr.py (Indra, PDF page 1), layout.py (label-anchored parser), vision.py
  ui/        core.py (locators, editors, windows), grid.py (OCR grids), stop.py (Esc), datefield.py
  steps/     order.py, debtor.py, product.py, invoice.py, selector.py, context.py
  verify/    db.py (read-only HSQLDB)
  models.py  validate.py  flow.py (steps, run lock, batch rules)  cli.py  config.py
tests/       306 unit tests + recorded OCR fixture
tools/       make_sample.py, make_unseen.py, diagrams/ (rough.js)
samples/     pharma orders (JSON + PNG), the brief's image, unseen/ (4 layouts)
docs/        paper/, design*.md, egm_review*.md, diagrams/, screenshots/
```

## Tooling used

- **Elephant-Goldfish Model (EGM).** Each design was drafted with full context and
  reviewed by zero-context "goldfish" reviewers; the whole repository was reviewed
  again in v4 (5 high, 23 medium, 31 low findings; all high ones fixed). Findings
  and resolutions: `docs/egm_review.md`, `docs/egm_review_v4.md`.
- **CLAUDE.md in each source folder** records that folder's rules and the
  Fakturama quirks it works around.
- **Ponytail.** A whole-repo audit removed dead code and added tests for the rules
  that guard identity, currency and idempotency.
