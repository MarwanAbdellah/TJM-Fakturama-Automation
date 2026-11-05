# src/image2cash/

| Module | Job |
|---|---|
| `models.py` | `OrderDraft`, the contract between extraction, validation and the UI flow. Money is `Decimal`. |
| `validate.py` | Pure checks and formulas: `check_order`, `line_net`, `gross_price` (brief 3.9), `totals` (VAT rounded per rate group), `close` (1 cent per line), `parse_amount` (handles Persian digits). |
| `flow.py` | The state machine (`STEPS`, `RESUME`), the OS run lock, per-run `run.log`, and exit codes 0 ok / 1 failed / 2 review / 3 rejected / 4 busy. |
| `cli.py` | `extract`, `run`, `verify`. |
| `config.py` | Paths. Every one can be overridden by an env var (`FAKTURAMA_WORKDIR`, `HSQL_URL`, …). |
| `extract/` | Image or PDF → `OrderDraft`. |
| `ui/` | All contact with Fakturama's GUI. |
| `steps/` | One module per business step of the brief. |
| `verify/` | Read-only DB access. |

## Rules

- **Layering.** Steps call `ui` and `verify`. `ui` never imports `steps`.
  `extract` and `validate` never import `ui`.
- **Logging, not print.** Loggers are named `image2cash.<area>`. Steps record
  through `Ctx.record` (INFO), UI actions log at DEBUG, and everything lands in
  the run's `run.log`.
- **New business rules.** Put the rule in a small pure function and give it a
  test in `tests/test_rules.py`.
