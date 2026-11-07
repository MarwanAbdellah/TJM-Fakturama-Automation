# image2cash

Order image or PDF → Fakturama 2.2.0 Order → linked Invoice with payment status.
The tool drives Fakturama's GUI (UI Automation plus OCR) and verifies every step
with read-only HSQLDB queries.

## Commands

```bash
uv sync                                        # deps (Python 3.12, Windows only)
uv run pytest -q                               # unit tests, no Fakturama needed
uv run ruff check . && uv run ruff format .    # also run by pre-commit
uv run streamlit run app/streamlit_app.py      # the app (localhost:8501)
uv run image2cash run <image|pdf>              # CLI; --draft order.json, --resume
uv run image2cash extract <image|pdf>          # OCR + validate only, no UI
```

## Hard rules

- **Drive only the Fakturama that serves the database.** The automation drives
  the window titled `Fakturama - <config.workdir()>` (the test workspace). If
  that is not open, it uses the one Fakturama that is open, by the user's
  decision (2026-11-07), so it works with whatever Fakturama a tester opened.
  Either way the driven process must own the DB port, and with two Fakturamas
  open and no test one it stops: `ui.core.target_instance()` enforces this.
  Never drop the port-owner check: reading one instance while driving another
  verifies the wrong data. On a PC with live data, keep the test Fakturama
  open (or nothing) when running.
- **The DB is read-only.** `verify/db.py` issues SELECT only. Every write goes
  through Fakturama's UI.
- **Live runs move the real mouse and keyboard.** Don't run them while someone
  is using the PC. Unit tests never touch the UI.
- **Prices live in the catalogue.** An order line's U.Price is confirmed, never
  typed. A different image price → ManualReview (`steps/product._check_master`).
- **Stop, don't guess.** Ambiguous data or UI raises `ManualReview`; the UI is
  left as it is for a human.
- **Plan first.** For multi-part changes, show the plan and a short verdict and
  wait for approval.

## Where things are

- `app/`: the Streamlit app (queue, review, preview, live run log).
- `src/image2cash/`: the package. See the CLAUDE.md in each subfolder.
- `tests/`: unit tests plus a recorded OCR fixture.
- `tools/`: sample renderers and the diagram generator.
- `samples/`: pharma orders (RX-100x), `unseen/` layouts, and the assessment image.
- `docs/`: design v1–v3, the EGM review records, diagrams, screenshots.
- `runs/` (git-ignored): one folder per run with `run.log`, `run.json`,
  `output.log` and screenshots; `app.log` for the app.

## Debugging a run

Read `runs/<id>/run.log` first: it has every step and UI action. `run.json`
has the values read back and the DB rows. The step that failed has a
screenshot named `*_failure.png` or `*_manual_review.png`.
