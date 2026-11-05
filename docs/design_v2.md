# Design v2: scrolling, field boxes, Streamlit app, USD/pharma data

Status: implemented (see §6 for review resolutions). This extends `design.md` (v1); the scope is
`PLAN_v2.md` phases 1–4.

## 1. USD and pharmaceutical data

- **Fakturama currency.**
  - The test workspace renders money with the `ps/AF` locale (Persian digits,
    Afghani sign).
  - Set `PREFERENCE_CURRENCY_LOCALE=en/US` in
    `<workspace>/.metadata/.plugins/org.eclipse.core.runtime/.settings/com.sebulli.fakturama.rcp.prefs`.
    This must be done **while Fakturama is closed**: the running app rewrites
    the file on exit (calibrated earlier).
  - Back up the file first.
  - Verify afterwards: a New Order's `Total` field shows `$0.00`, and
    `ui.learn_locale` returns `.`.
  - The Persian-digit parsing stays; it is harmless.
- **Sample data.**
  - Pharmacies, clinics and distributors in the US and Canada; currency USD for
    all of them.
  - Products have NDC-style SKUs (e.g. `NDC-0093-4155`) and real-sounding
    generic names ("Amoxicillin 500 mg Capsules, 100 ct").
  - Tax: `VAT 0%` for prescription items, and a state-like rate for OTC
    supplies (`VAT 6.25%` and `VAT 8.875%`). Fakturama only has one tax concept
    (VAT); naming follows brief 3.4 (`VAT p%`).
  - The image renderer (`tools/make_sample.py`) already takes a JSON spec.
    Only the specs change, plus one layout fix: for a US address the line after
    the street reads `City, ST ZIP`.
- **Address parsing.** The parser assumes the line after the street is
  `<ZIP> <City>` (German style). US/Canadian lines look like
  `Boston, MA 02118` and `Toronto, ON M5V 2T6`. The parser gets an alternative
  pattern `<City>, <ST> <ZIP/postal>` that keeps the state or province.
  Fakturama has no state field, so the state is appended to the city
  (`Boston MA`). This is the smallest choice that keeps the address
  round-trippable; it is to be confirmed in the review.
- **Country names** must exist in Fakturama's Country combo ("United States",
  "Canada"); this is to be checked live.
- The assessment's own EUR/German image stays as the reference test.

## 2. Item-table scrolling

**Problem.** The Order editor shows about 3 item rows. Rows below that are
clipped, so the OCR grid read misses them. Calibrated facts:
- The item table sits inside a scrollable editor composite.
- The NatTable has its own vertical scrollbar once rows exceed its height.

**Approach**, smallest first:

1. **Maximize the main window** (UIA `Maximize` button on the title bar) at the
   start of a run. This roughly doubles the editor height. It is cheap and
   harmless.
2. **Paged read.** `read_items_all()`:
   - read the visible grid;
   - if the bottom row is clipped or the table has a scrollbar, scroll the
     mouse wheel by 3 notches over the grid and read again;
   - merge rows keyed by `Pos.`;
   - stop when a read adds no new `Pos.`, with a cap of 30 pages.

   Row identity is `Pos.` (unique per document). A row's screen `y` is only
   valid for the page it was read from.
3. **Edit a row:** scroll until the row with that `Pos.` is visible (read and
   wheel, up or down), then edit the cell at the current `y`.
4. **Count rows by the highest `Pos.`**, not by the number of rows visible.
5. **Selector dialogs:** the search is an exact SKU or the company name, so a
   result rarely exceeds the visible rows. Apply the same paged read when the
   grid's last row touches its bottom edge.

**Alternative rejected:** keyboard navigation inside NatTable (arrow keys
change the selection, but there is no way to read which row is selected).

**Verification is unchanged:** the running `Total Net` after every line, and
`FKT_DOCUMENTITEM` after save. A 7-line synthetic order is the acceptance test.

## 3. Field provenance (bounding boxes)

- `layout.parse` already finds every value through the `Line`s under its label.
- `parse` will also return `boxes: dict[str, list[tuple[x0, y0, x1, y1]]]`,
  keyed by field path (`debtor.company`, `debtor.billing`, `items[0].sku`,
  `net_total`, …).
- Implementation: `Page.value()` and `Page.below()` record the lines they
  return under the current field path. The table parser records each cell.
- `OrderDraft` gets `boxes: dict[str, list[list[float]]] = {}`. That is
  serialisable, and nothing else reads it.

## 4. Streamlit app

**Run** with `uv run streamlit run app/streamlit_app.py` on the PC that runs
Fakturama. It binds to localhost by default.

Pages, as one script with sections:

1. **Upload.** `st.file_uploader` (png/jpg). The file is saved to
   `runs/uploads/<sha1>.png`; OCR results are cached by hash
   (`st.cache_data`).
2. **Extraction view.** The image is drawn with PIL rectangles coloured by
   section (order, customer, addresses, payment, items, totals) and a legend.
   A select box chooses one field to highlight. Next to the image is an
   editable form: header and customer `st.text_input`s, items in
   `st.data_editor`. Every edit re-runs `check_order`; problems are shown in
   red, and **Proceed is disabled while problems exist**.
3. **Preview (read-only DB).**
   - Is Fakturama reachable (a JDBC connect)?
   - Does the Cust.Ref. already exist?
   - For the debtor, the payment method, each VAT and each product: will it be
     *selected* or *created*?
   - This reuses `verify/db.py`.
4. **Proceed.**
   - The edited draft is written to `runs/<id>/order.json`.
   - The flow starts as a **subprocess**: `python -m image2cash.cli run-draft
     runs/<id>/order.json --run-dir runs/<id>`. A new CLI command runs from a
     draft JSON and skips OCR.
   - Why a subprocess: UIA/COM state and the long blocking run stay out of the
     Streamlit server; a crash cannot take down the UI; and the same code path
     is used as the CLI.
   - A lock file `runs/.lock` (with the PID) allows one run at a time.
   - The page polls `run.json` every second (`st.rerun` with a short sleep) and
     shows the step table and the newest screenshot.
5. **Result.** The exit code is mapped to ok / manual review / failed. The page
   shows the Order and Invoice numbers, the final DB rows (from `run.json`), a
   screenshot gallery and a **Resume** button if the run stopped after the
   Order was saved.
6. **History.** A list of `runs/*/run.json` with their status.

**Constraints shown in the UI:**
- "Don't touch mouse or keyboard during a run."
- The browser window will lose focus to Fakturama.

**Not in scope:** authentication (localhost only), multiple users, hosting.

## 5. Risks

- **Mouse-wheel scrolling** might scroll the outer editor instead of the
  NatTable. Mitigation: wheel with the pointer inside the grid rectangle, and
  detect "no change" so the loop ends.
- **Locale switch** could change other formats, such as dates. The date parser
  already handles `Jul 14, 2026`.
- **Streamlit reruns** could double-start a run. The lock file plus a
  session-state flag prevent that.

## 6. EGM review resolutions (design critic and implementation readiness)

| Finding | Resolution |
|---|---|
| The paged read is over-engineered: the flow only ever edits the newest line | **Accepted.** Scroll the item table to the bottom (wheel until two reads agree), then take the row with the highest `Pos.` That `Pos.` must equal the line's index + 1, otherwise ManualReview. No merge-by-Pos. reader. |
| Wheel may scroll the outer editor; captures may leak outside the viewport; half-clipped rows | **Accepted.** If the header's y moves after a wheel → AutomationError. Clip the capture to the editor pane. Drop rows within half a row of the bottom edge. Re-locate the row before every cell edit. |
| `make_sample.py` fits only about 4 rows, and its VAT rounding differs from the validator | **Accepted.** Height and totals y follow the item count; totals use `validate.totals`. The City/ST/ZIP "fix" is dropped (lines render verbatim). |
| 8.875 % cannot be typed (2-decimal formatting) | **Accepted.** Rates are 0 % (Rx) and 6.25 % (OTC). |
| US line `Boston, MA 02118` misparses as a German line | **Accepted.** Try `City, ST ZIP` / `City, PR A1A 1A1` first, then `ZIP City`. FKT_ADDRESS has no state column (checked), so City = `Boston, MA`. |
| Appended state → duplicate-debtor risk | **Accepted.** A same Company + ZIP debtor with a different City → ManualReview. |
| EUR reference test under a USD workspace | **Accepted.** `open_order` compares the draft currency with Fakturama's currency sign; a mismatch → ManualReview. The EUR reference image now stops there by design. |
| `run-draft` duplicates `run`; resume undefined; exit 3 not mapped | **Accepted.** `run --draft order.json [--resume]`, with the image optional. The app maps 0/1/2/3 and launches with `sys.executable`. `check_order` re-runs in the subprocess (trust boundary). |
| Lock file goes stale; `os.kill` is unsafe on Windows | **Accepted.** `flow.run` holds `msvcrt.locking` on `runs/.lock` for the life of the process (the OS releases it on death). The app only probes it. |
| Partial `run.json` reads | **Accepted.** The poller treats decode and permission errors as "not ready". |
| Boxes on the contract model; `items[i]` keys | **Accepted.** `parse(..., boxes=dict)` fills a separate dict keyed `items.<pos>.<col>`. Explicit key arguments, no mutable state. |
| Selector paged read | **Deferred.** An exact SKU or company search narrows the results. If a selector's result list fills the grid → ManualReview. `shortcut:` comment. |
| Country names | Read from the Country combo live before writing the specs. |
| Foreground lock when started from Streamlit | Tested early. |
