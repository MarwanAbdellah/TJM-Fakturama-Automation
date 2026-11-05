# tools/

- `make_sample.py <spec.json>` renders a "SALES ORDER INPUT" order (the layout
  the parser knows) from a JSON spec in `samples/`. The page grows with the
  item count, and totals use the validator's per-rate VAT rounding.
- `make_unseen.py` renders 4 orders in layouts the parser has never seen, into
  `samples/unseen/`, each with a `.truth.json`:
  - a hospital purchase order;
  - a rotated, noisy fax (JPEG);
  - a two-column distributor form;
  - a letter as a PDF.
- `diagrams/` generates the hand-drawn SVGs in `docs/diagrams/`, in Sketchy
  style, using rough.js and the embedded Kalam font (SIL OFL):
  `npm install && node make_diagrams.mjs`.

Sample data is fictional: pharmacies and hospitals in the US and Canada, USD,
`example.com` e-mail addresses, and 555 phone numbers.
