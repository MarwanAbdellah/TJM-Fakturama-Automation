# Fakturama Image-to-Cash Automation: Design

Status: v2. This version follows the goldfish review (`egm_review.md`) and
live calibration against Fakturama 2.2.0.

## 1. Goal and scope

**Input:** one order image.
**Output:** inside a running Fakturama, one saved Order and one linked Invoice
whose debtor, items, VAT, totals and payment status match the image. Every
step is verified, and the run leaves evidence behind (a JSON log plus
annotated screenshots).

The flow is Order-first, as the brief prescribes. The Order's own Debtor and
Product selectors are the existence checks. Master data is created only when
there is no exact match, and the flow then returns to the same open Order.

**Out of scope:**
- Delivery, Correction and Dunning documents.
- Editing master data that already exists.
- Multi-page or multi-order images.
- Writing to the DB except through the UI. The DB is used read-only.

## 2. Architecture

```
image ─► extract ─► OrderDraft ─► validate ─► flow (steps 1..5) ─► run.json + screenshots
         OCR+layout   pydantic     arithmetic    │ act → wait → read back → verify
                                                 ▼
                    ui: UIA locators │ label anchoring │ OCR for custom grids
                    verify: read-only HSQLDB (exact-match decisions + persisted state)
```

| Package | Role |
|---|---|
| `extract` | Local OCR (Indra, with boxes) and a label-anchored layout parser that produces an `OrderDraft`. |
| `validate` | Pure arithmetic and format checks, plus the brief's formulas (3.9, 3.16) and the payment-code map (2.10.4). |
| `ui` | Locators, waits, the segmented-date setter, the OCR grid reader, editor/tab/dialog handling and annotated screenshots. |
| `steps` | `order`, `debtor`, `product`, `invoice`, `selector`. Each step ends with its own verification. |
| `verify/db` | `SELECT`-only access to Fakturama's HSQLDB. |
| `flow` | The state machine, `--resume`, and exit codes (0 ok, 1 failed, 2 manual review, 3 source rejected). |

There are two failure types: **ManualReview** (ambiguous data or state; a human
decides) and **AutomationError** (the UI did not do what was expected). Waits
retry for *timing* only, never for a business decision. On ManualReview the UI
is left exactly as it is for the human.

## 3. Image extraction

The **primary path** is local OCR with the layout parser.
- Indra returns lines with boxes and confidences, in about 3 s per page, offline.
- A value is the line(s) under its printed label that cover at least half of
  the label's width. This handles left-aligned fields and centred total boxes
  alike.
- The item table is split by header columns. The two-line headers
  (`Unit net (EUR)`, `Line net (EUR)`) come back from OCR as boxes without
  text; their columns are recovered from the box geometry.
- All thresholds are relative to the median text height, so the parser does
  not depend on resolution. A test covers this.

**Validation.** Nothing is invented, and the draft is rejected before any UI
action if a check fails:
- Each line: `qty × unit × (1 − disc/100) = line total`.
- `Σ lines = NET TOTAL`.
- VAT rounded half-up per rate group equals `VAT TOTAL`, and `net + VAT = GROSS`.
- Dates are ISO; the currency is a 3-letter code.
- PAID requires a payment date.
- The payment method must have a code mapping.
- No duplicate SKUs.

A misread digit breaks one of these equations (tests cover this).

**Extension, not built:** a vision LLM as a second opinion, with a field-wise
diff after normalization. A key or money disagreement goes to ManualReview; a
cosmetic one is only logged.

## 4. Control discovery and grounding

Probing showed that SWT exposes more through UIA than expected. The locator
strategies, in order of preference:

1. **Named UIA controls.** Toolbar buttons (named by tooltip, e.g.
   `Save the current contents`), navigation links (`New Contact`, `VATs`),
   list buttons (`Create a new tax rate`), the follow-up `Invoice` button in
   the group `Create a follow-up document`, editor TabItems, CheckBoxes
   (`paid`, `Invoice address`), and totals Edits named after their labels.
2. **Label anchoring.** Find the `Static` label, then the nearest control on
   the same row to its right, else the nearest one below, searching up to
   three containers out. `row_controls()` returns all controls on a label's
   row (First Name/Last Name, ZIP/City). Unnamed icon buttons are "the N-th
   Image sibling of label X", e.g. the upper icon beside `Addresses` or
   `Items`.
3. **Semantic combos.** A combo is identified by its options when its label is
   untranslated: the payment-code combo is labelled `!editorPaymentPaymentcode!`
   and is found as the combo offering "Credit transfer". Native ComboBoxes are
   set through Win32 `select()`, matched exactly after stripping whitespace,
   and read back.
4. **OCR for custom-drawn NatTable grids** (selectors, Order items, data lists).
   - The grid rectangle comes from UIA; its owner window is brought to the
     front before capture.
   - The capture is upscaled ×2 and OCR'd.
   - Column boundaries come from the *drawn grid lines*. A pixel column that
     differs from its own row's median over the band is a line; header captions
     and body text never fill the band. This handles centred and left-aligned
     headers, gray headers and the blue selected row.
   - OCR lines that cross a grid line are split by word position.
   - Captions OCR missed are named by position between recognised neighbours.
   - Clicks use cell centres computed in that run. No coordinate is stored.

Each mechanic is defined by what was calibrated:

| Mechanic | Method |
|---|---|
| Segmented date (Nebula CDateTime) | Press Up, see which segment changed, Down to undo, type the digits for that segment, step with Up/Down if still off. Every key is read back. WM_SETTEXT was rejected: the display changed but the model did not. |
| Amount fields | Learn the decimal separator from the field's own display (`٫` in ps/AF) and type with it; parse Persian digits on read-back. Grid cells take `.`. |
| Selectors | Paste the whole search term (typing key by key can trigger the one-row auto-accept on a prefix). Then: auto-accepted, or a picked row, or Cancel, according to the DB count. |
| Save | Toolbar Save once. Postcondition: the tab title loses `*` (documents: becomes the number) and the DB row exists. Before a document save, the *Invoice address* tab is selected to avoid Fakturama's NPE (§7). |
| Discarding editors | The Eclipse *Save Parts* checklist: click the checkbox glyph, verify, cancel if it cannot be unchecked. |

## 5. Flow and match rules

```
preflight (refuse a duplicate Cust.Ref.; --resume continues after a saved Order)
1  New Order → keep No. → Date → Cust.Ref. → Net → With VAT
2  DB exact debtor? ─1─► selector: pick row ──► verify Invoice/Delivery address on the Order
                    ─0─► selector shows no exact row → Cancel
                         → ensure payment method (name match; code conflict → review)
                         → New Debtor: company, names, main address [+ Invoice role
                           (+ Delivery if identical)], additional address with Delivery
                           role if different, alias, discount 0, Net, payment → Save
                         → DB: one exact debtor, both roles, phone/e-mail → reselect
                    ─2+► ManualReview
3  per item: DB exact SKU? ─1─► selector pick / auto-accept → new row shows that SKU
                            ─0─► Cancel → ensure "VAT p%" (value p, code S; else review)
                                 → New product: SKU, name, description, VAT,
                                   gross = round(net×(1+p/100), 2), cost 0, stock 0 → Save
                                 → DB: stored net = source net → reselect
            line: Qty, U.Price := source net, Discount → running Total Net matches
4  Discount 0 %, free shipping, totals = source → Save → DB: date, items, total
5  follow-up Invoice → Cust.Ref./totals copied → payment method → paid ✓, date,
   Value = invoice Total (or leave unpaid) → Save → DB: link, paid, date, value,
   method; Order unchanged and open
```

**Match rules:**
- **Debtor:** Company, First Name, Name, ZIP and City are all equal.
- **Product:** the SKU (Item Number) is equal.
- **VAT:** Name = `VAT p%`, value = p, and code `S`.
- **Payment method:** the Name is equal.

Matching is case-sensitive and trims whitespace. Zero matches → create; one →
select; two or more → ManualReview. An existing product whose VAT differs from
the source goes to ManualReview, because a line-level VAT override is not
automated.

## 6. Evidence

`runs/<id>/run.json` holds, for every step, its inputs, the values read back,
the DB rows and a pass/fail. `runs/<id>/screenshots/` holds a screenshot per
step, with the controls that were touched outlined and labelled. A curated set
is in `docs/screenshots/`.

## 7. Trade-offs and known issues

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Write path | GUI only | Webshop XML / DB insert | The brief requires the UI flow; it keeps Fakturama's own logic. |
| Exact match | DB `SELECT` | OCR of the grid | Grid cells truncate, and OCR cannot prove absence. A false "missing" would create a duplicate. |
| Grounding | UIA, then labels, then OCR grids | A vision model clicks | Deterministic, fast and verifiable. OCR is used only where UIA is blind. |
| Extraction | OCR + layout rules | LLM only | Offline, reproducible and testable. The rules fit this document family; an LLM covers layout drift (extension). |
| Payment method | Ensured before New Debtor | Inside the open Debtor (brief 2.10) | Calibrated: the Debtor editor never refreshes its Payment list. |
| Failure policy | Stop for review | Best guess | Wrong master data is expensive; stopping is cheap. |

Fakturama issues found and worked around:
1. NPE on Save when the Delivery-address tab is active.
2. Auto-accept in the selector on a single remaining row.
3. Save Parts ignores programmatic unchecking.
4. The e-mail validator rejects `.test` (reported as a warning).
5. The ps/AF locale makes the decimal separator differ between form fields and grid cells.

**Operational constraint:** the run drives the real mouse and keyboard and
captures the screen. Do not use the machine while it runs.
