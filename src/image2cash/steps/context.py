"""Shared run state and the step-record helper."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from image2cash.models import OrderDraft
from image2cash.ui.core import UI


@dataclass
class Ctx:
    ui: UI
    order: OrderDraft
    run_dir: Path
    order_no: str = ""  # the "No." Fakturama proposed (PO...)
    order_db_id: int | None = None
    invoice_no: str = ""
    resume: bool = False
    resume_at: str | None = None  # set by preflight when continuing a stopped run
    created: list[str] = field(default_factory=list)  # master data created by this run
    warnings: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    def record(self, step: str, status: str, **details: Any) -> None:
        entry = {"t": round(time.time(), 2), "step": step, "status": status, **details}
        self.steps.append(entry)
        print(f"[{status:>6}] {step}: " + ", ".join(f"{k}={v}" for k, v in details.items() if k != "shot"))
        self.flush()

    def shot(self, name: str, boxes=None) -> str | None:
        p = self.ui.screenshot(name, boxes)
        return str(p) if p else None

    def flush(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run.json").write_text(
            json.dumps(
                {
                    "order": json.loads(self.order.model_dump_json()),
                    "order_no": self.order_no,
                    "invoice_no": self.invoice_no,
                    "created": self.created,
                    "warnings": self.warnings,
                    "steps": self.steps,
                },
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )

    # --- the Order tab stays open throughout ---------------------------------
    def is_order_tab(self, title: str) -> bool:
        t = title.lstrip("*")
        return t == "New Order" or (bool(self.order_no) and t == self.order_no)

    def back_to_order(self) -> None:
        if not any(self.is_order_tab(t) for t in self.ui.editor_tabs()):
            self.open_saved_order()
        self.ui.activate_editor(self.is_order_tab)
        # postcondition: this really is our order (Cust.Ref. reads back)
        from image2cash.ui.core import AutomationError, read

        ref = read(self.ui.labelled("Cust.Ref.", "Edit", root=self.ui.editor_pane()))
        if ref != self.order.external_ref:
            raise AutomationError(f"returned to an editor with Cust.Ref. {ref!r}")

    def open_saved_order(self) -> None:
        """Resume path: open the saved Order from Data > Documents > Orders."""
        import time as _t

        from pywinauto import mouse

        from image2cash.ui import grid as G
        from image2cash.ui.core import AutomationError, _visible, wait_for

        ui = self.ui
        ui.nav("Documents")
        panel = ui.view_panel("Documents")
        orders = [c for c in panel.descendants(control_type="TreeItem") if c.element_info.name == "Orders"]
        if not orders:
            raise AutomationError("Documents tree has no 'Orders' node")
        ui.click(orders[0])
        _t.sleep(1)
        pane = G.grid_below_search(panel)
        g = G.read_stable(pane.rectangle(), ["Document", "Date", "Name", "Cust.Ref.", "State", "Total", "Printed"], owner=ui.main)
        rows = [r for r in g.rows if r.get("Document") == self.order_no]
        if len(rows) != 1:
            raise AutomationError(f"order {self.order_no} not found in the Documents list")
        x, y = rows[0].boxes["Document"]
        ui.focus()
        mouse.double_click(coords=(int(x), int(y)))
        wait_for(lambda: any(self.is_order_tab(t) for t in ui.editor_tabs()), 10, what=f"editor {self.order_no}")
