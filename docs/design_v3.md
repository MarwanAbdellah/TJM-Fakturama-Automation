# Design v3: speed, queue, PDF input, unseen layouts

Status: implemented (see §6 for the EGM review). It builds on `design.md` and `design_v2.md`.

## 1. Speed

**What we measured.** An 8-line order took 264 s. Of that, 34 OCR reads of the
Order's item table took about 120 s (about 3.5 s each). Each line did:
- one read in `_check_new_row`;
- one read before *each* of the three cell edits (`complete_line` → `last_line`).

**Change.**
- `_check_new_row` returns the grid and row it found.
- `complete_line` edits Qty, U.Price and Discount at that row's `y` without
  re-reading.
- After the three edits, the running `Total Net` (a cheap UIA read) must equal
  the source. A wrong row or a wrong value fails this check and raises
  ManualReview, exactly as today.

This takes a line from about 5 reads to 1. Expected saving: about 15 s per
line.

**Risk.** An Enter could scroll the table, leaving the stored `y` stale. In
calibration, Enter never scrolled the table; the selection stays on the edited
row. If the totals check fails, the retry re-reads once and repeats the edits
on the fresh row. Only if that also fails does the run stop for manual review.

## 2. Queue

- **App.** `st.file_uploader(accept_multiple_files=True)`.
  - Each file is extracted, cached by hash, and listed in a queue table with
    columns: file, reference, checks, preview, status.
  - The review form edits the selected queue item.
  - **Proceed all** books every item whose checks pass, one at a time.
- **Execution.** One run at a time; the OS file lock already guarantees this.
  - The app keeps a queue in `st.session_state`. When the current subprocess
    exits, the progress fragment starts the next item.
  - An item that ends in "manual review" or "failed" is marked, and the queue
    moves on. Its Fakturama state is left as is; the next run opens a new
    Order tab.
- **Not doing:** a background queue that survives closing the browser tab.
  This is a local, single-user tool.

## 3. PDF input

- The first page of a PDF is rendered to PNG at 200 dpi with `pypdfium2`,
  which is already installed. The result is cached by file hash; everything
  after that is unchanged.
- A multi-page PDF uses page 1 only and shows a warning. One order per image is
  the brief's scope.

## 4. Unseen layouts (built last, behind a switch)

- **Chain:** the template parser first. If it fails, or its draft does not
  pass `check_order`, *and* the caller opted in (`--ai`, or the app toggle),
  `extract/vision.py` asks an AI model to read the image (any OpenAI-compatible
  chat API, Gemini `gemini-3.6-flash` by default; JSON-schema output into
  `OrderDraft`). It first used Claude; it was switched so no Anthropic API is needed.
- **Same checks either way:** `check_order`, human review in the app, and the
  DB checks.
- **No key:** the error stays, worded as "this layout is not supported
  offline".

## 5. Test data

Four new invoices in layouts the parser has never seen:
1. a hospital purchase order with different labels and column order;
2. a rotated, noisy scan;
3. a two-column distributor form;
4. a PDF.

They test 1–4 honestly. A sample that fails offline is a valid result, and it
documents why section 4 exists.

## 6. EGM review resolutions (design critic and zoom-out)

| Finding | Resolution |
|---|---|
| Queue "moves on" after a failure and builds on a half-done Fakturama | **Accepted.** Continue only after exit 0 or 3. Pause after 1 or 2 with "Resume queue" (after a human clears Fakturama). Exit 4 → paused, retry later. |
| Per-order edits live in widget state and are lost when switching orders | **Accepted.** Edits are stored on `orders[sha]["edited"]` every rerun. Queueing freezes a `snapshot`. |
| The queue driver sits after `st.stop()`; one `proc` slot can be overwritten | **Accepted.** `drive_queue()` runs before any `st.stop()`. `start()` refuses while a run is alive. Resume is hidden while a queue is active. The fragment reruns the app when a run ends. |
| The speed retry can't repair a wrong-row edit; a mismatch was an AutomationError | **Accepted.** No retry. A totals mismatch is an explicit ManualReview. The row is re-read (with its SKU checked) before a second edit. Cells already at their default are skipped. |
| PDF "unchanged downstream" was false; the app and CLI used different entry points | **Accepted.** `extract.extract_draft()` is the single entry point and returns the raster the boxes refer to. The uploader and the sample picker accept PDF. `pypdfium2` is a direct dependency. |
| The fallback trigger and its privacy | **Accepted, for checkpoint 4.** It is explicit opt-in, it triggers on ExtractionError *or* `check_order` problems, it is part of the cache key, and its draft carries no boxes and shows a "check every field" banner. |
| Page reload loses the queue | **Deferred.** `shortcut:` this is a local single-user tool; the run lock still prevents a second driver. |

## 7. User decision: prices come from the catalogue

The user chose: **never type U.Price**. If an existing product's catalogue net
price (to the cent) differs from the image, the run stops with ManualReview
before the line is added. Products the automation creates take their price
from the image, entered as gross per brief 3.9, which leaves a net remainder.
So totals compare within 1 cent per line (`validate.close`), and any drift is
reported as a warning.
