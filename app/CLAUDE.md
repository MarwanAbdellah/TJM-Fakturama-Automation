# app/

`streamlit_app.py` is the single-page app: upload → field boxes → review →
preview → book (queue) → live log → result.

## Rules

- **One run at a time.** `start()` is the only way to launch a run, and it
  refuses while a process is alive. The OS file lock in `flow.single_run` also
  covers CLI runs.
- **Session keys.** Never name a session-state key after a dict method:
  `st.session_state.items` is the `.items()` method. Orders live in
  `st.session_state.orders` (sha → order dict).
- **Edits persist.** Review edits are written back to `orders[sha]["edited"]` on
  every rerun. A queued order books the `snapshot` frozen when it was queued.
- **Queue rules** (`drive_queue`):
  - Continue after exit 0 (done) or 3 (rejected; it never touched Fakturama).
  - Pause after 1 or 2: Fakturama is left half-done for a human.
  - Exit 4 means busy.
- **Call `drive_queue()` before any `st.stop()`.** The `progress()` fragment
  reruns the whole app when a run ends, so the queue advances.
- **`fakturama_ready()`** wraps the instance guard. Booking is disabled when it
  fails.

## Logs

- `runs/app.log`: extractions, queue events, starts and exits.
- Each run's `run.log`: shown live in the page.

## Testing

`streamlit.testing.v1.AppTest` works headlessly; select boxes must not use
`format_func`, because AppTest can't drive it.
