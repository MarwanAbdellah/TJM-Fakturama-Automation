# Plan v2 (approved 2026-11-05, not yet implemented)

Approved by the user after they reviewed the plan and verdict. Each phase
ends with tests passing and, where Fakturama is involved, a live run.

## 0. Prerequisites (done)

- EGM (`egm@egm`) and Ponytail (`ponytail@ponytail`) are installed as Claude
  Code plugins at user scope. The user explicitly accepted installing them
  without a SkillSpector scan.
- Restart Claude Code before using `/egm:*` or `/ponytail*`.
- "Subagents cannot spawn subagents" is a built-in Claude Code limit, not a
  setting. EGM reviewers are spawned from the main session.

## 1. Pharmaceutical, US/Canada, USD data

- All synthetic samples become pharmaceutical:
  - Customers are pharmacies, clinics and distributors in the US and Canada,
    with US/Canadian addresses, ZIP/postal codes and phone formats.
  - Products are things like "Amoxicillin 500 mg capsules, 100 ct" or
    "Nitrile exam gloves, box 100", with NDC-style SKUs.
  - Currency is **USD** for every sample, including the Canadian customers.
  - Tax rates should be plausible (for example 0 % for Rx and a sales-tax
    rate for OTC). The exact rates are still to be decided.
- Replace `samples/order_SO-TEST-0002/0003` with pharma orders. Add a
  **7+ line** order to test scrolling.
- Fakturama currency: the test workspace currently uses the `ps/AF` locale
  (Persian digits, Afghani sign). Switch `PREFERENCE_CURRENCY_LOCALE` to
  `en/US` (USD) **while Fakturama is closed**, because the app overwrites its
  prefs file on exit. Then re-check the decimal-separator handling: it
  becomes `.`. Keep the Persian-digit parsing; it is harmless.
- The original assessment image (`WEB-2026-0714-A17`, EUR, German) is the
  company's own input. It stays unchanged as the reference test.

## 2. Item-table scrolling gap

- Maximize the Order editor during the item steps.
- Read the grid in pages: mouse-wheel scroll, merge rows by `Pos.`, and stop
  when no new rows appear.
- Count rows by the highest `Pos.` seen, not by the number of visible rows.
- Scroll the target row into view before editing a cell.
- Apply the same scrolling to selector dialogs.
- Test live with the 7+ line pharma order.

## 3. Field provenance for bounding boxes

- `layout.parse` records the source OCR box(es) for every field.

## 4. Streamlit app (local, `uv run streamlit run app/streamlit_app.py`)

- **Upload:** the image with boxes coloured by section, plus hover/select
  highlighting.
- **Review:** editable fields and items, with live validation.
- **Preview:** read-only DB check showing whether Fakturama is running,
  whether the reference already exists, and select-vs-create for the debtor,
  payment, VAT and each product.
- **Proceed:** runs the flow in a subprocess. Shows a live step log and the
  latest screenshot. Single-run lock. Resume button.
- **Result:** Order and Invoice numbers, the DB verification table, the
  screenshot gallery and run history.

## 5. pre-commit hooks

- ruff (lint and format) and basic hygiene hooks (whitespace, EOF, toml/yaml,
  a large-file guard).
- pytest on pre-push.

## 6. Ponytail pass

- Run `/ponytail-review`, `/ponytail-audit` and `/ponytail-debt`, and apply the
  simplifications that hold up.
- The unit tests and a live run must still pass afterwards.

## 7. Hand-drawn ("sketchy") diagrams

Three diagrams in the hand-drawn style used before:
- the architecture;
- the flow with its branches;
- the control-grounding strategy.

## 8. Final

- Run every sample live, then update the README (including the skipped list),
  `design.md` and `egm_review.md`, and commit.
- Use EGM (`/egm:egm-draft`, `/egm:egm-review`) for the design of phases 2–4
  before coding them.
