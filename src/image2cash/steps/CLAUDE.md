# steps/

One module per brief section: `order` (§1, §4), `debtor` (§2), `product` (§3),
`invoice` (§5), `selector` (the Order's two selector dialogs), and `context`
(`Ctx`: run state, `record`, screenshots, return-to-Order).

## Rules

- **Exact match is a DB count, never OCR:**
  - 0 → create;
  - 1 → select in the Order's selector;
  - 2 or more → ManualReview.

  OCR only finds the row to click, using `selector.ocr_eq`, which tolerates
  O/0, I/L/1, S/5 and stray `|`.
- **Selectors auto-accept** as soon as the filter leaves one row. Paste the
  whole term (`selector._paste`); never type it key by key.
- **Prices.** `product._check_master` confirms the catalogue price and VAT
  *before* the line is added. U.Price is never typed. Totals compare with
  `validate.close` (1 cent per line); any drift is a warning.
- **Line edits.**
  - The first edit reuses the read that proved the line.
  - The row is re-read (with its SKU checked) before a second edit.
  - Cells already at their default (Qty 1, Discount 0) are skipped.
- **Payment method.** It must exist *before* the New Debtor editor opens,
  because that editor loads its Payment list only once.

## Fakturama 2.2.0 bugs worked around here

- **Save crashes** with an NPE while the "Delivery address" tab is active.
  Select "Invoice address" first.
- **The "New product" nav link** throws a ClassCastException after a selector
  pick. Use the toolbar's "Create a new product".
- **The e-mail validator rejects `.test` domains.** It is reported as a warning.
