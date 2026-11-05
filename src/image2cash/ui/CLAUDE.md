# ui/

Fakturama 2.2.0 is Eclipse SWT. Grounding order:
1. UIA name;
2. label anchor (`UI.labelled`, `row_controls`, `icon_beside`);
3. combo by its options (`combo_with_option`);
4. OCR of custom-drawn NatTable grids (`grid.py`).

No stored coordinates: every click comes from the current run.

## Calibrated quirks (don't re-learn them)

- **Instance guard.** `target_instance()` passes only if the window titled
  `Fakturama - <WORKDIR>` exists and its process owns the HSQL port.
- **Minimized window.** UIA treats it as invisible, so `UI()` restores it via
  win32 first.
- **Editor folder.** When the editor area is maximized, views float at the same
  top edge. The editor folder is the one holding the "Fakturama" start page.
- **Segmented dates** (`datefield.py`): press Up to find the segment, type its
  digits, read back. WM_SETTEXT changes only the display, not the model.
- **Amounts.** Form fields parse in the UI locale (`learn_locale`). Grid cells
  always take `.`.
- **Save Parts dialog.** Programmatic unchecking is ignored; click the checkbox
  glyph and verify.
- **Grid columns** come from the drawn grid lines, in the header band or the
  first body row, then named by a majority vote over the captions OCR read.
  Lines that cross a column line are split by word position.
- **Grid capture.** A screen capture only sees what is on top, so the owning
  window is focused first.
