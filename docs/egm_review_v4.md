# EGM review v4: whole working directory

Date: 2026-11-05. Read-only review. Nothing was run, and Fakturama was not touched.

**Scope.** `src/`, `app/`, `tools/`, `tests/`, `docs/`, `README.md`, every `CLAUDE.md` and `pyproject.toml`. Not reviewed: `graphify-out/`, `runs/`, binaries under `samples/`, `uv.lock` (checked only for `httpx2`), `.venv`, `node_modules` and `__pycache__`.

**Part A (design_v4) was cancelled.** The user keeps the brief's Order-first flow and `docs/design_v4.md` has been deleted. This report covers Part B only and gives no verdict on v4. The one part of v4 that remains in the code, the VAT separator change, is reviewed below as code (B-M1).

## Reviewer status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-codebase-details` | correctness bugs in src/app/tools | completed |
| `egm-zoom-out` | dead code, simplification, dependencies | completed |
| `egm-comprehension` | docs against code and files on disk | completed |
| `egm-implementation-readiness` | missing tests for risky logic | completed |
| `egm-design`, `egm-zoom-out`, `egm-implementation-readiness`, `egm-codebase-details` (Part A) | design_v4 | completed, then discarded because Part A was cancelled |

**How findings were filtered:**
- Findings reported by more than one reviewer are merged into one entry.
- The synthesizer re-checked these findings against the code: B-H1, B-M1, B-M8, B-L1 and B-L2.
- One finding was dropped. A reviewer said `test_vision` skips because `importorskip("httpx2")` names an undeclared package. In fact `uv.lock` lists `httpx2` as a dependency of `anthropic`, so the test runs.

## Part B: findings

### Correctness

| id | sev | file:line | finding | suggested fix |
| --- | --- | --- | --- | --- |
| B-H1 | high | `src/image2cash/steps/order.py:180-193`, `steps/invoice.py:125-137` | The saved Order and Invoice are never checked against the debtor that was chosen. `contact_id` and `delivery_contact_id` are fetched (`verify/db.py:209-210`) but never compared, and `db.document_receivers` (`db.py:244`) has no caller. The only debtor check is `verify_order_addresses`, which tests street, ZIP and city as substrings and ignores company and names. So a wrong selector row (OCR pick, or auto-accept per B-M5) passes as OK. | Store the selected or created contact id in `ctx`. In 4.5 and 5.5, assert `d["contact_id"] == ctx.contact_id`. Optionally compare `document_receivers` with `o.debtor`. |
| B-M2 | med | `steps/order.py:190-192` | The discount check accepts two scales: `rebate*100 ≈ pct` OR `rebate ≈ pct`. A 0.5 % line passes with a stored rebate of 0.5 (50 %), and a 0 % line passes any rebate below 0.01. Only the total check (within `drift`) remains. | Calibrate the scale `ITEMREBATE` uses and check only that one. |
| B-M3 | med | `steps/product.py:160-161` vs `steps/order.py:189` | The two price checks disagree at the half-cent boundary. The catalogue check accepts `money(price) == unit_net` (HALF_UP), but the post-save check needs `< 0.005`. A price of x.xx5 passes before save and fails after it every time, including on `--resume`. | Use one rule in both places (`money(it["price"]) == src.unit_net`). |
| B-M4 | med | `ui/core.py:329` | `amount_text` quantizes to 0.01 with the default HALF_EVEN. A qty or discount with three decimals (2.125 %) is typed as 2.12, and the ±0.01 check plus the `drift` slack let it through. | Reject more than 2 decimals for qty and discount in `check_order`, or use `ROUND_HALF_UP` to match `validate.money`. |
| B-M5 | med | `steps/selector.py:51` vs `ui/core.py:164-176` | `search()` decides "auto-accepted" with `title not in ui.dialogs()`. `dialogs()` lists only `main.children()`, but `dialog()` can also find the selector through the `Desktop` fallback. A selector found that way counts as auto-accepted at once, and debtor/product record "ok" with nothing picked. Combined with B-H1, nothing catches it. | Decide with `_gone(dialog)` (the window handle), as `press()` already does. |
| B-M6 | med | `steps/order.py:57-59` | The currency guard passes when the currency is not recognised: `booked_currency` knows only `$ € £ ؋`, so `0,00 CHF` or `0.00 EUR` returns `None` and the run continues. | Treat `None` as `ManualReview`. Also match 3-letter ISO codes. |
| B-M7 | med | `steps/context.py:70-72` | `is_order_tab` matches any "New Order" tab. A stale `*New Order` left by a failed run with the same Cust.Ref. passes the postcondition, so the run can drive the old editor. | Refuse to start (ManualReview) when a "New Order" tab is already open, or remember the pane handle from `open_order`. |
| B-M8 | med | `steps/debtor.py:121-125`, `verify/db.py:92-103` | The address checks are loose. `verify_order_addresses` uses substrings: ZIP "1011" matches "10115", "York" matches "New York", "Main St 1" matches "Main St 12". `contacts_matching` joins every address of a contact with no BILLING role filter and no street check. | Normalise the values and compare per line. In `contacts_matching`, require the BILLING role and compare the street. |
| B-M9 | med | `flow.py:100`, `extract/layout.py:104-108` | `draft or extract(...)` is outside any try. `layout._date` raises a plain `ValueError` for 2026-13-01, and `extract_draft` catches only `ExtractionError`. A bad source therefore gives a CLI traceback and exit 1, and no `rejected.txt` is written, instead of `INVALID_SOURCE`. | Wrap the extraction and map the error to `ExitCode.INVALID_SOURCE`. Make `_date` raise `ExtractionError`. |
| B-L1 | low | `steps/product.py:194-197` (new) | The new VAT fallback catches any `AutomationError` from the '.' attempt, not only a read-back mismatch, and the order "'.' then ','" is based on one observation (run RX-2003). `set_text` clears the field with `^a{BACKSPACE}`, so the retry starts from an empty field. The DB check at :202 guards the result. | Acceptable as is. Add a one-line `shortcut:` comment naming the calibration run. When '.' is confirmed, drop the fallback. |
| B-L2 | low | `steps/product.py:374-376` (`_num`), `extract/layout.py:111-116` (`_dec`) | Both remove every `,` before parsing. In a comma-decimal UI, "2,00" becomes 200, giving a false ManualReview in `verify_lines`. In the parser, "1.234,56" becomes 1.23456. `check_order` rejects the parser case, so that one fails safe. | Use `validate.parse_amount` in both. |
| B-L3 | low | `ui/datefield.py:56-58, 87-88` | Dates with '/' are always read as D/M/Y, so a US display passes a self-consistent wrong read-back. Impossible dates raise `ValueError` instead of returning None, and `invoice.py:110` loops over fields with this function. | Return None on `ValueError`. Pin the '/' order with a calibrated test. |
| B-L4 | low | `steps/product.py:88` | The wait predicate returns `net`, so a Total Net of 0.00 (a free first line) is falsy and times out. | Return `True`. |
| B-L5 | low | `tools/make_sample.py:121` | The unit price is printed with `:.2f`, but the line net uses full precision, so a 3-decimal spec produces an image that contradicts itself. | Round the unit price before computing the net. |

### Dead code and simplifications

The removals were checked:
- `last_line`, `is_line`, `BOTTOM`, the row-pitch, row-y and highlight helpers, `_check_new_row` and `complete_line` are gone from the code.
- `TOP` remains at `steps/product.py:244`. It is live (the wheel-notch home at :284 and :290), not a leftover.
- `extract/layout.py` is the live local parser.

| id | sev | file:line | finding | suggested fix |
| --- | --- | --- | --- | --- |
| B-D1 | med | `ui/core.py:352-398` | `UI.close_editor` (about 47 lines, with Save Parts handling) has no caller. | Delete. |
| B-D2 | med | `ui/core.py:126-127` | `self.w32_app` and `self.w32` are never read, yet they make a second win32 `connect` on every run. | Delete both lines. |
| B-D3 | med | `verify/db.py:76-122` | Callers of `ContactRow` use only `.id` and `.number`. The `types` field costs an extra `address_types` query (a new JDBC connection) per row. | Drop `types` and the unused fields. |
| B-D4 | med | `pyproject.toml:6-14` | `pandas` (`app/streamlit_app.py:21`) and `pywin32` (`win32clipboard`, `win32gui`) are imported but resolve only through other packages. | Declare both. |
| B-D5 | med | `ui/core.py:224-229`, `steps/debtor.py:238-242`, `steps/invoice.py:47-50, 103-108`, `steps/product.py:110-116` | The same-row control search is written out five times. | Make `core.row_controls` take parameters and call it from all five. |
| B-D6 | low | `ui/grid.py:42-44`, `:39` | `Grid.column_x` has no caller, and `Grid.raw` is never read. | Delete both. |
| B-D7 | low | `extract/vision.py:36-42`, `verify/db.py:244-251` | `available()` has no caller. `document_receivers` has none either; use it for B-H1 or delete it. | See B-H1. |
| B-D8 | low | `steps/product.py:11,28` | `logging` and `log` are unused. | Delete. |
| B-D9 | low | `extract/ocr.py:66-76` | `ocr_image` opens the image only to return a size that every caller discards. | Return only the lines, and update the 2 callers and the test monkeypatches. |
| B-D10 | low | `validate.parse_amount`, `product._num`, `layout._dec`, `tools/make_sample.py:107-130`, `tools/make_unseen.py:35-43` | Three number parsers, and two copies of `totals` and `font()` in the tools. | Reuse `parse_amount` and `validate.totals`. Share `font`. |
| B-D11 | low | `core.py:316`, `context.py:94`, `datefield.py:97`, `debtor.py:344` | Local re-imports of modules already imported at module level (the one at `debtor.py:344` is inside a loop). | Use the top-level imports. |
| B-D12 | low | `selector.py:23` and `context.py:112` | `context.py` reaches into the private alias `S._paste`. | Import `paste` from `ui.core`. |
| B-D13 | low | `debtor.py:302`, `debtor.py:205` | A loop that runs once, and an `if cid` that is always true. | Write a plain `if`, and call `read(cid)` directly. |
| B-D14 | low | `app/streamlit_app.py:52-58, 192` | The `EXIT` and `STATUS` tables duplicate the codes in `flow.ExitCode`. `preview()` re-runs `target_instance()` (a netstat subprocess). | Merge the tables, keyed by `ExitCode`. Drop the second check. |
| B-D15 | low | `extract/__init__.py:21`, `layout.py:267` | The string `"indra-ocr+layout"` is hard-coded twice. | Make it one `layout.ENGINE` constant. |
| B-D16 | low | `steps/product.py:5-6` | The docstring still mentions "the highlight". | Drop the clause. |

### Stale docs

| id | sev | file:line | finding | suggested fix |
| --- | --- | --- | --- | --- |
| B-S1 | high | `README.md:54` | The assessment-image row says it "stops for manual review: order is in EUR, Fakturama books in USD". The workspace is now EUR, so the guard no longer fires. | Rewrite the row for EUR: refused by preflight as already booked, or booked. |
| B-S2 | high | `README.md:73-76` vs `src/image2cash/verify/CLAUDE.md:22-24` | The README says to edit `PREFERENCE_CURRENCY_LOCALE` in the `.prefs` file. verify/CLAUDE.md says to use File → Preferences, because the value also lives in `FKT_USERPROPERTY`. | Keep one procedure (Preferences) and add a check (the New Order Total shows €). |
| B-S3 | med | `README.md:44-53`, `README.md:22`, `tests/test_rules.py:73`, `CLAUDE.md:42` | The results table and the 18 screenshots come from USD runs of RX-1001..1003, which are not in `samples/` (only RX-2001..2003 exist). README:44 labels them as historic, but there is no EUR result, and CLAUDE.md:42 still says "RX-100x". | Add an EUR results row, including the RX-2003 VAT failure and its fix. Change CLAUDE.md to RX-200x. Label the screenshots as USD-era. |
| B-S4 | med | `README.md:74`, `README.md:203`, `src/image2cash/ui/CLAUDE.md:23-24`, `ui/core.py:324-325` | The docs say "form fields parse in the UI locale". After the new change, the VAT percent field is typed with '.', falling back to ','. README:74 also says ga/IE shows `1,234.57`, yet the RX-2003 run saw `32,75` in Price. | Document the VAT field exception. Confirm the live separator and correct README:74. |
| B-S5 | med | `docs/design_v3.md:7-26`, `docs/design_v3.md:3` | v3 §1 describes `_check_new_row`, `complete_line`, `last_line` and U.Price typing, all of which are gone, and still says "Status: implemented". The column-wise fill that actually runs (`fill_lines`, `walk`, `_view`, `TOP`) is described only in README and steps/CLAUDE.md. | Mark v3 §1 as superseded and add a short column-fill section. |
| B-S6 | med | `docs/design.md:3,41,73,115,138,179`, `docs/design_v2.md:3,9-18,53-63,110-118`, `docs/PLAN_v2.md:1,25-42`, `docs/egm_review.md:38-43` | The historic docs read as current: ps/AF, USD, U.Price overwrite, merge-by-Pos., "LLM not built", exit codes 0-3, "not yet implemented", sample names that do not exist. | Add a one-line "superseded by …" status at the top of each. Do not rewrite them. |
| B-S7 | med | `README.md:200`, `tests/CLAUDE.md:10` | Both still describe the removed rule "the newest line is the bottom row with Pos. = n". | Describe `walk`/`locate`/`_numbered` instead. |
| B-S8 | med | `tools/diagrams/make_diagrams.mjs:116,121` (`flow.svg`) | The diagram's input node shows "PNG / JPG" (PDF is supported too), and its per-line node shows "Qty · U.Price · Disc." (U.Price is never typed). | Fix the labels and regenerate. |
| B-S9 | low | `README.md:133,282` | The "71 unit tests" count is stale: there are about 77 test cases plus the new VAT test. | Drop the number. |
| B-S10 | low | `README.md:79,85-93` | WORKSPACE and WORKDIR are not explained, and the env var list omits `FAKTURAMA_WORKSPACE`, `IMAGE2CASH_RUNS`, `HSQL_USER` and `HSQL_PASSWORD`. | Explain the two in one sentence and list every key from `config.py`. |
| B-S11 | low | `validate.py:146-147`, `ui/core.py:314-315`, `tests/CLAUDE.md:9` | The comments describe ps/AF as the live workspace. | Reword as "legacy ps/AF support, harmless". |
| B-S12 | low | `CLAUDE.md:14`, `README.md:51,56-67`, `src/image2cash/CLAUDE.md:6`, `design_v3.md:92` | Assorted drift: `order.json` vs `draft.json`, "Proceed" vs "Book this order", `output.log` only for app runs, and "1 cent per line" vs the real `drift` rule. | Correct each one. |
| B-S13 | low | throughout | "The brief" is cited by section number, but no brief file is in the repo. | Add a one-line summary for each section cited. |

### Missing tests (pure logic, no GUI needed)

| id | sev | file:line | gap | one-line test |
| --- | --- | --- | --- | --- |
| B-T1 | high | `steps/order.py:172-200`, `steps/invoice.py:115-144` | The post-save DB verifiers, the last line of defence, have no tests. | Monkeypatch `db.*` and change one field at a time; expect `AutomationError`. Add a contact_id case once B-H1 lands. |
| B-T2 | high | `steps/debtor.py:44-84`, `steps/product.py:38-64` | The select-or-create table that prevents duplicates (many matches, near match, auto-accept without a DB match, grid without DB confirmation) has no tests. | Parametrize `(db_rows, near, grid)` with `S.*` stubbed. Assert ManualReview or that `create` is called. |
| B-T3 | med | `ui/core.py:312-330` | `learn_locale` and `amount_text` have one test (the new VAT one). | Add `learn_locale("1.234,56 €") == ","`, a no-match case that keeps the previous value, and `amount_text(19.00) == "19"`. |
| B-T4 | med | `flow.py:124-145` | Untested: the resume splice ([preflight, verify saved order, invoice]) and the exit-code mapping. | Stub `UI`, `STEPS` and `RESUME`, then assert the call order and the `ExitCode`. |
| B-T5 | med | `tests/test_flow.py:27-30` | The tests use the real `runs/.lock`, so they fail while a live run holds it. | `monkeypatch.setattr(flow, "LOCK", tmp_path / ".lock")`. |
| B-T6 | low | `product._num`, `datefield.parse_display`, `validate.parse_amount` ("1,234" and "1.234"), `db.query` SELECT-only guard, `vat_name(10)`, `check_order` edge branches, `_escape` | Small pure cases are not pinned. | Add one parametrized line for each. |

## Summary

| Severity | Count |
| --- | --- |
| High | 5 (B-H1, B-S1, B-S2, B-T1, B-T2) |
| Medium | 23 |
| Low | 31 |

Fix B-H1 first: the run never checks that the debtor it booked is the debtor it chose. B-M5 (a false auto-accept) and B-M8 (loose address matching) make that gap reachable.
