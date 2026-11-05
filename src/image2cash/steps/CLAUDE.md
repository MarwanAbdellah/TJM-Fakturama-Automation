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
  `validate.close`, whose tolerance (`drift`) grows with quantity; any
  difference is a warning.
- **Lines are filled column by column (`product.fill_lines`).** All products
  are added first; each add is proven by Total Net rising by the catalogue
  price (no OCR). Then the finished table is read once, page by page (`walk`:
  2 wheel notches per page from the top, 1 if 2 would skip lines), which gives
  every line's page and y and checks every SKU. Before each edit the page is
  replayed from the top (`_view`), because every edit throws the view there.
  Then all Qty cells, then all Discount cells are typed, with Total Net checked
  after each column. Cells already at their default (Qty 1, Discount 0) are
  skipped.
- **Why not line by line:** Fakturama rebuilds the item table after every cell
  edit: the selection goes back to line 1 and the scroll back to the top. Rows
  are not one height either (they fit the product name). So never rely on the
  highlight, the scroll position or a row pitch after an edit.
- **Before saving**, `verify_lines` reads the whole table page by page
  and compares every line's SKU, Qty and Discount. An unreadable cell is a
  warning; a readable mismatch is ManualReview.
- **`ctx.back_to_order()`** returns at once when our Order tab is already
  active (same editor pane handle as last verified).
- **Payment method.** It must exist *before* the New Debtor editor opens,
  because that editor loads its Payment list only once.

## Fakturama 2.2.0 bugs worked around here

- **Save crashes** with an NPE while the "Delivery address" tab is active.
  Select "Invoice address" first. For the Order, the address check (2.4,
  `debtor.verify_order_addresses`) runs right before Save and reads Delivery
  first, Invoice last, so it leaves the right tab active.
- **The "New product" nav link** throws a ClassCastException after a selector
  pick. Use the toolbar's "Create a new product".
- **The e-mail validator rejects `.test` domains.** It is reported as a warning.

## Speed notes (measured live)

- OCR of the item table takes about 0.2 s; UIA lookups are the slow part
  (editor folder ~0.4 s, a label ~0.2 s, the four totals ~0.75 s). So
  controls that do not move while the Order is open are cached in
  `ctx.cache` (selector icon, "Items" label, totals per editor pane) and
  found again only if a cached one fails. `UI._editor_folder` is cached too.
- A dialog counts as closed when Win32 says its handle is gone or hidden
  (`selector._gone`): the UIA wrapper of a closed SWT shell still says visible.
- Screenshots are written by a background thread (`core._SAVER`).
