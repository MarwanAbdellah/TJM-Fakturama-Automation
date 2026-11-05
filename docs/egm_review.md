# Design review: Elephant-Goldfish method

The design was produced with the Elephant-Goldfish Model (EGM) workflow
(github.com/google/elephant-goldfish-model):

1. **Elephant:** the drafting agent wrote `design.md` v1 with full context: the
   brief, the earlier prototype in `F:\Fakturama_Automation`, and a live
   UIA probe of Fakturama.
2. **Goldfish:** two zero-context reviewers read only the draft, the brief and
   the calibration notes. Neither saw the drafting conversation.
   - *Design critic / comprehension:* did the draft meet the brief?
   - *Implementation readiness / structure:* could it be built in the timebox?
3. **Resolve:** each finding was accepted, adapted or rejected (below). Every
   UI mechanic a reviewer called unproven was then spiked against the live app
   before implementation.

The EGM plugin itself was **not installed** into Claude Code. The local policy
requires an NVIDIA SkillSpector scan of any plugin before installation, and
installing the scanner was blocked in this environment. The method was
therefore applied by hand: same roles, same isolation (fresh subagents with no
conversation history). The plugin's own prompts were not used. The repository
was cloned only for inspection, at commit
`0dfb5bf3eebd551d1e805c3ab16cbaa9b1c2c42a`. To install it after a passing scan,
run `claude plugin marketplace add google/elephant-goldfish-model`, then
`claude plugin install egm@egm`.

## Findings and resolutions

| # | Sev | Finding (abridged) | Resolution |
|---|-----|--------------------|------------|
| 1 | high | "Exact" match decided by OCR on truncated grid cells is unreliable, and an OCR miss would create duplicates | **Accepted.** Exact 0/1/many is decided by read-only HSQLDB `SELECT`s (`verify/db.py`). OCR only locates the row to click. A grid row that looks right but that the DB does not confirm goes to ManualReview. |
| 2 | high | Billing ≠ delivery (true for the sample) is not handled | **Accepted.** Main address = Invoice role. If delivery differs, the `+` button adds "additional address #1" with additional name, street, ZIP/city and country, plus the Delivery role. Both roles are verified in `FKT_ADDRESS_CONTACTTYPES`, and both tabs on the Order. |
| 3 | high | Item-cell editing is unspecified | **Accepted and spiked.** Double-click the cell located by OCR column span, Ctrl+A, type, Enter. Each line is verified by the UIA `Total Net` running total, then in `FKT_DOCUMENTITEM` after save. |
| 4 | high | SWT date fields ignore plain text set | **Accepted and spiked.** WM_SETTEXT only *looked* right: the model reverted on blur. Built `ui/datefield.py`: press Up to see which segment changes, undo, type the digits, read back. Works for every date field. |
| 5 | high | Return-to-Order by TabItem name may not work in SWT | **Rejected after probing.** CTabFolder items *are* exposed as UIA TabItems. Added a postcondition (Cust.Ref. reads back) and a fallback that reopens the Order from Data > Documents. |
| 6 | high | Payment-method branch misplaced and under-specified | **Adapted.** All 2.10.x values are set, and the code is mapped and verified via UNTDID (30/54/59). Calibration showed the Debtor editor loads its Payment list only when it opens, so a method created while it is open cannot be selected. The flow therefore ensures the method *before* opening New Debtor; the Order stays open. A stored code that conflicts goes to ManualReview; an empty stored code produces a warning. |
| 7 | high | Paid controls unspecified | **Accepted and spiked.** UIA CheckBox `paid`, then the payment combo on that row, then the date field (found by its parseable content), then `Value`. Verified in `PAID`, `PAYDATE`, `PAIDVALUE` and `FK_PAYMENT`. |
| 8 | med | Locale and number formats | **Accepted; mattered more than expected.** The workspace renders amounts in Persian digits (`ps/AF`). Form fields need the locale decimal separator `٫` (typing `297.50` gave 29,750). Grid cells need `.`. The separator is learned from each field's display, and every amount is read back. |
| 9 | med | U.Price may drift a cent after gross→net | **Accepted.** U.Price is always overwritten with the source net. The product master's stored net (`PRICE1`) is checked against the source. |
| 10 | med | Missing verification observables | **Accepted.** Each step names its observable: tab title loses `*` and becomes the number, a DB row appears, fields read back, totals match. |
| 11 | med | Idempotency and recovery | **Accepted.** Preflight refuses a second document with the same Cust.Ref. `--resume` continues a run whose Order is already saved: it verifies the Order and then goes to the Invoice. |
| 12 | med | Dialog watcher hand-waved | **Partly.** Known dialogs (Save Parts, Confirm deleting, selector dialogs) are handled explicitly. Unknown states end in a timeout with a screenshot. A generic watcher is listed as future work. |
| 13 | low | Cut the LLM second opinion | **Accepted.** It is documented as an extension. OCR plus arithmetic cross-checks covers the samples. |
| 14 | low | DB check beats Documents-list OCR | **Accepted.** The Documents list is kept as screenshot evidence. |
| 15 | — | Minimum slice and spike the risky mechanics first | **Followed.** Date entry, grid edits, selectors, Save Parts and the paid row were all spiked before the steps were written. |
| C9 | med | VAT rounding ambiguous | **Accepted.** VAT is rounded half-up per rate group. Mixed 7 % / 19 % was tested. |
| C11 | med | Order-level discount and shipping (4.2) | **Accepted.** Discount is forced to 0 % and Shipping to "Free of shipping costs", and both are read back. |
| C12 | med | 5.5 also needs the Order unchanged and the link checked | **Accepted.** Checks: Invoice `FK_SRCDOCUMENT` = Order id; Order still unpaid with the same Cust.Ref. and total. |

## Things only the live app revealed (not caught by any reviewer)

- **Selector auto-accept.** The selector closes by itself as soon as its filter
  leaves exactly one row. Typing an SKU one key at a time could auto-pick a
  different product at an intermediate prefix. The fix is to paste the whole
  term at once.
- **Fakturama 2.2.0 save NPE.** Saving a document while its dynamically added
  "Delivery address" tab is active throws a NullPointerException in
  `DocumentEditor.doSave` ("Failed to persist contents of part"). The same
  message is visible in the brief's Figures 1 and 5. The fix is to select the
  Invoice address tab before every document save.
- **Save Parts checkboxes.** "Save Parts" ignores programmatic unchecking, so
  the first cleanup saved three blank records. They were removed again through
  Fakturama's Delete. Unchecking now clicks the checkbox glyph and verifies the
  result; if that fails it cancels.
- **E-mail validation.** Fakturama's e-mail validator rejects `example.test`
  and silently stores no e-mail. The DB check detects this and reports it as a
  warning.

## v2 round (Streamlit app, scrolling, USD/pharma data)

This time the installed EGM plugin's reviewer agents were used, so its own
prompts applied: `egm-design` and `egm-implementation-readiness`, each in a
fresh context. They produced 20 findings. All resolutions are in
`design_v2.md` §6. The ones that changed the design most:

- **Stop over-engineering the scroll fix.** The flow only edits the newest
  line, so a "merge pages by Pos." reader was unnecessary. The result: scroll
  to the bottom, then check that the bottom row's `Pos.` equals the line number.
- **Booking the EUR reference image under a USD workspace would book wrong
  amounts.** Fix: a currency guard in `open_order`, which raises ManualReview.
- **A 7-line sample could not be rendered.** The renderer's totals box was at a
  fixed height. The renderer's VAT rounding also differed from the validator's.
- **The run lock and resume semantics were vague.** The OS file lock is now
  held by the run process itself, and `run --draft ... --resume` was added.

Found only live, after the reviews:

- **"New product" link crash.** The navigation link throws a
  ClassCastException after a selector pick. The toolbar button works.
- **Item-table scrollbar.** The scrollbar buttons ignore UIA Invoke, and the
  UIA "Page down" region is missing when the overflow is small. Mouse-wheel
  scrolling works.
- **Faint headers.** OCR reads only some header captions. Columns are now named
  by a majority-offset vote over the drawn grid lines.
- **Editor-folder detection.** When the editor area is maximized, the Documents
  view floats over it at the same top edge. The editor folder is now found as
  the folder that holds the "Fakturama" start page.
- **OCR character swaps** in grid cells: `S`/`5` in Canadian postal codes,
  `O`/`0` in customer numbers. One shared OCR-tolerant comparison now handles
  them. Identity is still decided by the DB.

**Verdict on EGM, second use.** The goldfish reviews again earned their cost
(about 2 minutes, about 140k tokens) by catching design-level mistakes
cheaply. As in v1, every expensive problem came from the live app, not from
review.
