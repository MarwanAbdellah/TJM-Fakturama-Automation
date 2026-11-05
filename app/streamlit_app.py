"""Image-to-Cash cockpit: upload an order image, check what OCR read, then let the
automation book it in Fakturama.

    uv run streamlit run app/streamlit_app.py

Runs on the PC where Fakturama is open (it drives the real desktop). Local only.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
from pydantic import ValidationError

from image2cash import config, flow
from image2cash.extract.layout import ExtractionError, parse
from image2cash.extract.ocr import ocr_image
from image2cash.models import OrderDraft
from image2cash.validate import PAYMENT_CODES, check_order, vat_name

RUNS = config.RUNS_DIR
SECTIONS = {  # key prefix -> (label, colour)
    "items": ("Items", "#d97706"),
    "debtor.billing": ("Addresses", "#7c3aed"),
    "debtor.delivery": ("Addresses", "#7c3aed"),
    "debtor": ("Customer", "#2563eb"),
    "payment": ("Payment", "#059669"),
    "net_total": ("Totals", "#dc2626"),
    "vat_total": ("Totals", "#dc2626"),
    "gross_total": ("Totals", "#dc2626"),
    "": ("Order", "#0f766e"),
}
EXIT = {
    0: ("Done and verified", "success"),
    1: ("Automation error", "error"),
    2: ("Stopped for manual review", "warning"),
    3: ("Rejected by validation", "error"),
    4: ("Another run is in progress", "warning"),
}

st.set_page_config(page_title="Image-to-Cash", page_icon="💊", layout="wide")


def section(key: str) -> tuple[str, str]:
    return next(v for k, v in SECTIONS.items() if key.startswith(k))


@st.cache_data(show_spinner="Reading the image (local OCR)...")
def extract(path: str) -> tuple[dict | None, dict, str | None]:
    lines, _ = ocr_image(path)
    boxes: dict = {}
    try:
        draft = parse(lines, source=path, boxes=boxes)
        return json.loads(draft.model_dump_json()), boxes, None
    except ExtractionError as exc:
        return None, boxes, str(exc)


def annotate(path: str, boxes: dict, focus: str | None) -> Image.Image:
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for key, rects in boxes.items():
        _, colour = section(key)
        width = 7 if key == focus else 3
        for x0, y0, x1, y1 in rects:
            draw.rectangle((x0 - 4, y0 - 3, x1 + 4, y1 + 3), outline=colour, width=width)
    return img


# ---------------------------------------------------------------- review form
def review_form(d: dict, k: str) -> dict:
    """Editable copy of the draft. ``k`` namespaces widget keys per uploaded file."""
    o, c, p = d, d["debtor"], d["payment"]
    st.subheader("Order")
    a, b, cc = st.columns(3)
    o["external_ref"] = a.text_input("External reference", o["external_ref"], key=k + "ref")
    o["order_date"] = b.text_input("Order date (YYYY-MM-DD)", o["order_date"], key=k + "date")
    o["currency"] = cc.text_input("Currency", o["currency"], key=k + "cur")

    st.subheader("Customer")
    a, b = st.columns(2)
    c["company"] = a.text_input("Company", c["company"], key=k + "co")
    c["alias"] = b.text_input("Alias", c.get("alias") or "", key=k + "alias")
    c["first_name"] = a.text_input("First name", c["first_name"], key=k + "fn")
    c["last_name"] = b.text_input("Last name", c["last_name"], key=k + "ln")
    c["email"] = a.text_input("E-mail", c.get("email") or "", key=k + "mail")
    c["phone"] = b.text_input("Phone", c.get("phone") or "", key=k + "tel")
    cols = st.columns(2)
    for col, part in zip(cols, ("billing", "delivery")):
        ad = c[part]
        col.markdown(f"**{part.title()} address**")
        for f in ("name", "street", "zip", "city", "country"):
            ad[f] = col.text_input(f.title(), ad[f], key=f"{k}{part}{f}")

    st.subheader("Payment")
    a, b, cc = st.columns(3)
    methods = sorted(set(PAYMENT_CODES) | {p["method"]})
    p["method"] = a.selectbox("Method", methods, methods.index(p["method"]), key=k + "pm")
    statuses = ["PAID", "UNPAID", "OTHER"]
    p["status"] = b.selectbox("Status", statuses, statuses.index(p["status"]), key=k + "ps")
    p["paid_date"] = cc.text_input("Paid on (YYYY-MM-DD)", p.get("paid_date") or "", key=k + "pd") or None

    st.subheader("Items")
    cols = ["pos", "sku", "description", "qty", "unit", "unit_net", "discount_pct", "vat_pct", "line_net"]
    edited = st.data_editor(
        pd.DataFrame(d["items"])[cols], num_rows="dynamic", use_container_width=True, key=k + "items"
    )
    d["items"] = [
        {kk: (None if pd.isna(v) else str(v) if kk not in ("pos",) else int(v)) for kk, v in row.items()}
        for row in edited.to_dict("records")
    ]

    st.subheader("Totals")
    a, b, cc = st.columns(3)
    o["net_total"] = a.text_input("Net", o["net_total"], key=k + "net")
    o["vat_total"] = b.text_input("VAT", o["vat_total"], key=k + "vat")
    o["gross_total"] = cc.text_input("Gross", o["gross_total"], key=k + "gross")
    return d


# ---------------------------------------------------------------- preview
def preview(draft: OrderDraft) -> list[tuple[str, str]]:
    """Read-only DB look-ahead: what the run will select vs create."""
    from image2cash.verify import db

    try:
        return _preview(draft, db)
    except Exception as exc:  # noqa: BLE001 - JDBC errors surface as many types
        return [("Fakturama", f"not reachable: start it on the test workspace ({type(exc).__name__})")]


def _preview(draft: OrderDraft, db) -> list[tuple[str, str]]:
    docs = db.documents_with_ref(draft.external_ref)
    d = draft.debtor
    out = [("Fakturama", "reachable")]
    out.append(
        ("Order", f"exists already: {[x['number'] for x in docs]} → run will stop" if docs else "will be created")
    )
    n = len(db.contacts_matching(d.company, d.first_name, d.last_name, d.billing.zip, d.billing.city))
    out.append(
        (
            f"Debtor {d.company}",
            {0: "will be created", 1: "exists → will be selected"}.get(n, f"{n} matches → manual review"),
        )
    )
    n = len(db.payments_named(draft.payment.method))
    out.append(
        (
            f"Payment {draft.payment.method}",
            {0: "will be created", 1: "exists → reused"}.get(n, "ambiguous → manual review"),
        )
    )
    for rate in sorted({i.vat_pct for i in draft.items}):
        n = len(db.vats_named(vat_name(rate)))
        out.append((vat_name(rate), {0: "will be created", 1: "exists → reused"}.get(n, "ambiguous → manual review")))
    for i in draft.items:
        n = len(db.products_with_sku(i.sku))
        out.append(
            (
                f"Product {i.sku}",
                {0: "will be created", 1: "exists → will be selected"}.get(n, "ambiguous → manual review"),
            )
        )
    return out


# ---------------------------------------------------------------- run + progress
def start(draft: OrderDraft, resume: bool = False, run_dir: Path | None = None) -> None:
    run_dir = run_dir or RUNS / f"app-{datetime.now():%Y%m%d-%H%M%S}-{draft.external_ref}"
    run_dir.mkdir(parents=True, exist_ok=True)
    order_json = run_dir / "draft.json"
    order_json.write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    cmd = [sys.executable, "-m", "image2cash.cli", "run", "--draft", str(order_json), "--run-dir", str(run_dir)]
    st.session_state.proc = subprocess.Popen(
        cmd + (["--resume"] if resume else []), creationflags=subprocess.CREATE_NO_WINDOW
    )
    st.session_state.run_dir = run_dir


def read_log(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # the run rewrites it on every step
        return None


def show_run(run_dir: Path, code: int | None) -> None:
    log = read_log(run_dir)
    if code is None:
        st.info("Running in Fakturama. Don't touch the mouse or keyboard.", icon="🖱️")
    else:
        text, kind = EXIT.get(code, (f"exit {code}", "error"))
        getattr(st, kind)(text)
    if not log:
        st.caption("Waiting for the first step...")
        return
    steps = [s for s in log["steps"] if s["status"] != "start"]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "step": s["step"],
                    "status": s["status"],
                    "detail": s.get("reason")
                    or ", ".join(
                        f"{k}={v}"
                        for k, v in s.items()
                        if k
                        not in (
                            "t",
                            "step",
                            "status",
                            "shot",
                            "shots",
                            "trace",
                            "contact",
                            "addresses",
                            "invoice",
                            "order",
                            "payment",
                            "ui",
                            "computed",
                            "items",
                            "db",
                        )
                    )[:160],
                }
                for s in steps
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    shots = sorted((run_dir / "screenshots").glob("*.png"))
    if shots:
        st.image(str(shots[-1]), caption=shots[-1].stem)
    if code is not None and log.get("order_no"):
        a, b = st.columns(2)
        a.metric("Order", log["order_no"])
        b.metric("Invoice", log.get("invoice_no") or "—")
        for w in log.get("warnings", []):
            st.warning(w)
        final = next((s for s in reversed(steps) if s["step"] == "5.5 verify"), None)
        if final:
            with st.expander("Database verification"):
                st.json({k: final[k] for k in ("invoice", "order", "payment") if k in final})
        with st.expander(f"All screenshots ({len(shots)})"):
            for s in shots:
                st.image(str(s), caption=s.stem)
        saved = any(s["step"] == "4.5 order in DB" and s["status"] == "ok" for s in steps)
        if code != 0 and saved and st.button("Resume: create the Invoice for the saved Order"):
            start(
                OrderDraft.model_validate(log["order"]),
                resume=True,
                run_dir=run_dir.with_name(run_dir.name + "-resume"),
            )
            st.rerun()


@st.fragment(run_every=1.0)
def progress() -> None:
    proc = st.session_state.get("proc")
    if proc is None:
        return
    show_run(st.session_state.run_dir, proc.poll())


# ---------------------------------------------------------------- page
st.title("💊 Image-to-Cash")
st.caption(
    "Order image → Fakturama Order → linked Invoice with payment status. "
    "Everything is checked against Fakturama's database."
)

with st.sidebar:
    st.header("Past runs")
    for log_path in sorted(RUNS.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
        log = read_log(log_path.parent) or {}
        last = (log.get("steps") or [{}])[-1]
        st.caption(
            f"**{log_path.parent.name}** · {log.get('order_no', '')} {log.get('invoice_no', '')}"
            f" · {last.get('status', '?')}"
        )

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "samples").glob("*.png"))
a, b = st.columns([3, 2])
up = a.file_uploader("Order image", type=["png", "jpg", "jpeg"])
pick = b.selectbox("...or a bundled sample", ["(none)"] + [s.name for s in SAMPLES])
if up is not None:
    data, suffix = up.getvalue(), Path(up.name).suffix.lower()
elif pick != "(none)":
    data, suffix = (SAMPLES[0].parent / pick).read_bytes(), ".png"
else:
    st.stop()

sha = hashlib.sha1(data).hexdigest()[:12]
path = RUNS / "uploads" / f"{sha}{suffix}"
path.parent.mkdir(parents=True, exist_ok=True)
if not path.exists():
    path.write_bytes(data)

raw, boxes, error = extract(str(path))
left, right = st.columns([5, 6])
with left:
    st.subheader("What OCR read")
    legend = " ".join(f"<span style='color:{c}'>■ {n}</span>" for n, c in dict(SECTIONS.values()).items())
    st.markdown(legend, unsafe_allow_html=True)
    focus = st.selectbox("Highlight a field", ["(none)"] + sorted(boxes), key=sha + "focus")
    st.image(annotate(str(path), boxes, None if focus == "(none)" else focus), use_container_width=True)
if error:
    right.error(f"Could not read this image as an order: {error}")
    st.stop()

with right:
    edited = review_form(json.loads(json.dumps(raw)), sha)
    try:
        draft = OrderDraft.model_validate(edited)
        problems = check_order(draft)
    except ValidationError as exc:
        draft, problems = None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]

st.divider()
st.subheader("Checks")
if problems:
    for pr in problems:
        st.error(pr)
else:
    st.success("All arithmetic and format checks pass.")
    st.table(pd.DataFrame(preview(draft), columns=["What", "Fakturama will"]))

running = st.session_state.get("proc") is not None and st.session_state.proc.poll() is None
if st.button("Proceed: book it in Fakturama", type="primary", disabled=bool(problems) or running or flow.busy()):
    start(draft)
    st.rerun()
progress()
