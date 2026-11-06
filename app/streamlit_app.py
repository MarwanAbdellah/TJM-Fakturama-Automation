"""Image-to-Cash cockpit: upload order images or PDFs, check what OCR read, then let the
automation book them in Fakturama, one after another.

    uv run streamlit run app/streamlit_app.py

Runs on the PC where Fakturama is open (it drives the real desktop). Local only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
from pydantic import ValidationError

from image2cash import config
from image2cash.extract import extract_draft
from image2cash.extract.vision import ENGINE
from image2cash.flow import batch_step
from image2cash.models import PAYMENT_CODE_LABELS, PAYMENT_CODES, OrderDraft, Payment
from image2cash.validate import check_order, vat_name

RUNS = config.RUNS_DIR


@st.cache_resource  # once per process: each browser tab runs this script in its own thread, and
def _app_log() -> logging.Logger:  # an unguarded check raced, writing every line 2-3 times
    lg = logging.getLogger("image2cash.app")
    RUNS.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(RUNS / "app.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    lg.handlers = [fh]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg


log = _app_log()
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


def plain(text: str) -> str:
    """Show text as written: Markdown would hide the backslash in a path such as 'F:\\x\\.y'."""
    return text.replace("\\", "\\\\")


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


@st.cache_data(show_spinner="Reading the order...")
def extract(path: str, ai: bool = False, attempt: int = 0) -> tuple[dict | None, dict, str | None, str, str]:
    """-> (draft as JSON dict, field boxes, error, path of the image the boxes refer to, engine).
    ``ai`` and ``attempt`` are only cache keys: switching AI on, or "Try the AI again", re-reads."""
    r = extract_draft(path, ai)
    if r.draft is None:
        log.warning("could not read %s as an order: %s", path, r.error)
        return None, r.boxes, r.error, str(r.image), r.engine
    d = r.draft
    log.info("extracted %s with %s: %s, %d items", path, r.engine, d.external_ref, len(d.items))
    return json.loads(d.model_dump_json()), r.boxes, None, str(r.image), r.engine


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
    p["method"] = a.selectbox("Method", methods, methods.index(p["method"]), accept_new_options=True, key=k + "pm")
    known = Payment(method=p["method"], status="UNPAID", code=p.get("code")).payment_code
    p["code"] = a.selectbox(
        "Payment code",
        PAYMENT_CODE_LABELS,
        PAYMENT_CODE_LABELS.index(known) if known else None,
        placeholder="pick the code for this method",
        help="Fakturama's payment code; a method Fakturama lacks is created with it",
        key=k + "pc",
    )
    statuses = ["PAID", "UNPAID", "OTHER"]
    p["status"] = b.selectbox("Status", statuses, statuses.index(p["status"]), key=k + "ps")
    p["paid_date"] = cc.text_input("Paid on (YYYY-MM-DD)", p.get("paid_date") or "", key=k + "pd") or None

    st.subheader("Items")
    cols = ["pos", "sku", "description", "qty", "unit", "unit_net", "discount_pct", "vat_pct", "line_net"]
    edited = st.data_editor(pd.DataFrame(d["items"])[cols], num_rows="dynamic", width="stretch", key=k + "items")
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
    from image2cash.ui.core import AutomationError, target_instance
    from image2cash.verify import db

    try:
        target_instance()  # never read (or later drive) a different Fakturama than the configured one
    except AutomationError as exc:
        return [("Fakturama", f"not ready: {exc}")]
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
STATUS = {0: "done", 1: "failed", 2: "review", 3: "rejected", 4: "busy"}


def running() -> bool:
    proc = st.session_state.get("proc")
    return proc is not None and proc.poll() is None


def start(draft: OrderDraft, resume: bool = False, run_dir: Path | None = None) -> bool:
    """The only way a run starts. Refuses while a run is alive (one Fakturama, one driver)."""
    if running():
        return False
    safe_ref = re.sub(r"[^\w.-]", "_", draft.external_ref)  # the reference is user-editable text
    run_dir = run_dir or RUNS / f"app-{datetime.now():%Y%m%d-%H%M%S}-{safe_ref}"
    run_dir.mkdir(parents=True, exist_ok=True)
    order_json = run_dir / "draft.json"
    order_json.write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    cmd = [sys.executable, "-m", "image2cash.cli", "run", "--draft", str(order_json), "--run-dir", str(run_dir)]
    if resume:
        cmd.append("--resume")
    out = open(run_dir / "output.log", "ab")  # the run's console: errors before run.log exists land here
    st.session_state.proc = subprocess.Popen(
        cmd,
        stdout=out,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    out.close()  # the child has its own handle
    st.session_state.run_dir = run_dir
    st.session_state.logged_exit = False
    log.info("started run %s (pid %s): %s", run_dir.name, st.session_state.proc.pid, " ".join(cmd[1:]))
    return True


def launch(sha: str, resume: bool = False) -> None:
    """Start the queued order ``sha`` from the snapshot frozen when it was queued."""
    item = st.session_state.orders[sha]
    item["tries"] = item.get("tries", 0) + 1
    if start(OrderDraft.model_validate(item["snapshot"]), resume=resume):
        item["status"] = "running"
        item["run_dir"] = str(st.session_state.run_dir)
        st.session_state.current = sha


def drive_queue() -> None:
    """Record the finished run and start the next one. A failed run is tried once more
    (from the saved Order if it got that far); then, like a review stop or a rejection,
    the order is skipped and listed with its reason. Only a busy lock pauses the queue."""
    ss = st.session_state
    cur = ss.get("current")
    if cur is None or running():
        return
    code = ss.proc.poll()
    item = ss.orders[cur]
    item["status"] = STATUS.get(code, f"exit {code}")
    ss.current = None
    log.info("queue: %s -> %s", item["name"], item["status"])
    if not ss.get("batch"):  # a single booking ends here; its run is shown below
        return
    step, tries = batch_step(code, item["tries"]), item["tries"]
    if step == "retry":
        launch(cur, resume=order_saved(read_log(Path(item["run_dir"]))))
        return
    if step == "skip":
        why = stop_reason(read_log(Path(item["run_dir"]))) or "no reason logged"
        ss.skipped.append((item["name"], f"{item['status']} (tried {tries}x): {why}"))
    if step == "pause":
        item["tries"] -= 1  # the lock, not this order, stopped it
        ss.queue.insert(0, cur)
        ss.paused = "Queue paused: another image2cash run holds Fakturama. Press Resume queue when it has finished."
    elif ss.queue:
        launch(ss.queue.pop(0))


def read_log(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # the run rewrites it on every step
        return None


def stop_reason(log: dict | None) -> str | None:
    """'<step>: <reason>' of a run that stopped, from its run.json."""
    steps = log["steps"] if log else []
    i = next((i for i in range(len(steps) - 1, 0, -1) if steps[i]["step"] == "stopped"), None)
    return None if i is None else f"{steps[i - 1]['step']}: {steps[i].get('reason', '')}"


def order_saved(log: dict | None) -> bool:
    return bool(log) and any(s["step"] == "4.5 order in DB" and s["status"] == "ok" for s in log["steps"])


def tail(path: Path, n: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def show_run(run_dir: Path, code: int | None, sha: str | None = None) -> None:
    log = read_log(run_dir)
    if code is None:
        st.info("Running in Fakturama. Don't touch the mouse or keyboard.", icon="🖱️")
    else:
        text, kind = EXIT.get(code, (f"exit {code}", "error"))
        if why := stop_reason(log):  # say why up front; the step table below has the detail
            text += f"\n\n**Stopped at {plain(why)}**"
        getattr(st, kind)(text)
    # run.log has every step and UI action; output.log is the raw console (crashes before logging started).
    lines = tail(run_dir / "run.log", 15 if code is None else 60) or tail(run_dir / "output.log", 60)
    if lines:
        with st.expander("Live log" if code is None else "Run log", expanded=code is None or code != 0):
            st.code("\n".join(lines), language="log")
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
        width="stretch",
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
        saved = order_saved(log)
        if (
            code != 0
            and saved
            and not st.session_state.get("queue")
            and st.button("Resume: create the Invoice for the saved Order")
        ):
            if (
                start(
                    OrderDraft.model_validate(log["order"]),
                    resume=True,
                    run_dir=run_dir.with_name(run_dir.name + "-resume"),
                )
                and sha
            ):
                ss_ = st.session_state
                ss_.orders[sha].update(run_dir=str(ss_.run_dir), status="running")
                ss_.current, ss_.batch = sha, False  # drive_queue records it like a single booking
            st.rerun()


@st.fragment(run_every=1.0)
def progress() -> None:
    """The live run. A finished run is shown under Runs, one order at a time."""
    proc = st.session_state.get("proc")
    if proc is None:
        return
    code = proc.poll()
    if code is not None:
        if not st.session_state.get("logged_exit"):
            st.session_state.logged_exit = True
            log.info("run %s finished: exit %s (%s)", st.session_state.run_dir.name, code, EXIT.get(code, ("?", ""))[0])
            st.rerun(scope="app")  # drive_queue records it and starts the next order; Runs shows it
        return
    show_run(st.session_state.run_dir, None)


def fakturama_ready() -> tuple[bool, str]:
    from image2cash.ui.core import AutomationError, target_instance

    try:
        target_instance()
        return True, "reachable"
    except AutomationError as exc:
        return False, f"not ready: {exc}"


def draft_of(item: dict) -> tuple[OrderDraft | None, list[str]]:
    try:
        draft = OrderDraft.model_validate(item["edited"])
        return draft, check_order(draft)
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]


# ---------------------------------------------------------------- page
ss = st.session_state
ss.setdefault("orders", {})  # sha -> one uploaded order and its review state
ss.setdefault("queue", [])
ss.setdefault("current", None)
ss.setdefault("paused", None)
ss.setdefault("skipped", [])  # (order, why) of the last batch
ss.setdefault("attempts", {})  # sha -> AI re-reads asked for (a 503 is cached otherwise)

st.title("💊 Image-to-Cash")
st.caption(
    "Order images or PDFs → Fakturama Order → linked Invoice with payment status. "
    "Everything is checked against Fakturama's database."
)
drive_queue()  # before anything that can st.stop(): the queue must advance on every rerun

with st.sidebar:
    st.header("Past runs")
    for log_path in sorted(RUNS.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
        past = read_log(log_path.parent) or {}
        last = (past.get("steps") or [{}])[-1]
        st.caption(
            f"**{log_path.parent.name}** · {past.get('order_no', '')} {past.get('invoice_no', '')}"
            f" · {last.get('status', '?')}"
        )

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"
# Friendly names for some bundled samples; shown as the options themselves (AppTest cannot
# drive format_func), and mapped back to the file below.
SAMPLE_LABELS = {"order_WEB-2026-0714-A17.png": "Home Test Example"}
SAMPLE_FILES = {
    SAMPLE_LABELS.get(p.name, p.name): p.name
    for p in sorted(SAMPLES_DIR.iterdir())
    if p.suffix.lower() in (".png", ".jpg", ".pdf")
}
SAMPLES = sorted(SAMPLE_FILES, key=lambda label: (label not in SAMPLE_LABELS.values(), label))  # named ones first
a, b = st.columns([2, 3])
picks = [SAMPLE_FILES[label] for label in a.multiselect("Bundled samples", SAMPLES, default=["Home Test Example"])]
uploads = b.file_uploader(
    "...or your own order images or PDFs", type=["png", "jpg", "jpeg", "pdf"], accept_multiple_files=True
)
ai = st.toggle(
    "AI fallback for unknown layouts",
    help="If the local parser cannot read a file, or its result fails the checks, send the image to "
    "an AI model (OpenRouter by default; LLM_BASE_URL / LLM_MODEL in .env) to read it. Off by default: "
    "the image leaves this machine unless LLM_BASE_URL is a local server. Needs LLM_API_KEY in .env.",
)
for name, src, data in [(f.name, f.name, f.getvalue()) for f in uploads or []] + [
    (f"{SAMPLE_LABELS[n]} ({n})" if n in SAMPLE_LABELS else n, n, (SAMPLES_DIR / n).read_bytes()) for n in picks
]:
    sha = hashlib.sha1(data).hexdigest()[:12]
    known = ss.orders.get(sha)
    attempt = ss.attempts.get(sha, 0)
    retry = known and known.get("attempt", 0) != attempt
    if known and not retry and not (ai and not known["ai_tried"] and known["problems"] and known["status"] == "new"):
        continue
    path = RUNS / "uploads" / f"{sha}{Path(src).suffix.lower()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    raw, boxes, error, image, engine = extract(str(path), ai, attempt)
    item = {"name": name, "image": image, "raw": raw, "boxes": boxes, "error": error, "status": "new", "engine": engine}
    item["ai_tried"] = ai  # turning the toggle on re-reads each failing file once
    item["attempt"] = attempt
    item["edited"] = json.loads(json.dumps(raw)) if raw else None
    item["problems"] = draft_of(item)[1] if raw else [f"unreadable: {error}"]
    ss.orders[sha] = item

if not ss.orders:
    progress()
    st.stop()

st.subheader("Orders")
st.dataframe(
    pd.DataFrame(
        [
            {
                "file": it["name"],
                "reference": (it["edited"] or {}).get("external_ref", "—"),
                "checks": "unreadable"
                if it["error"]
                else ("ok" if not it["problems"] else f"{len(it['problems'])} problem(s)"),
                "status": it["status"],
            }
            for it in ss.orders.values()
        ]
    ),
    width="stretch",
    hide_index=True,
)

keys = list(ss.orders)
labels = [f"{n}. {ss.orders[k]['name']}" for n, k in enumerate(keys, 1)]
if ss.get("review_pick") not in labels:
    ss.review_pick = labels[0]


def step_order(by: int) -> None:
    """Previous/Next: move the review (form, checks, run) to the neighbouring order."""
    ss.review_pick = labels[(labels.index(ss.review_pick) + by) % len(labels)]


back, pick, fwd = st.columns([1, 8, 1], vertical_alignment="bottom")
back.button("◀ Previous", on_click=step_order, args=(-1,), disabled=len(labels) < 2, width="stretch")
pick.selectbox("Review order", labels, key="review_pick")
fwd.button("Next ▶", on_click=step_order, args=(1,), disabled=len(labels) < 2, width="stretch")
sel = keys[labels.index(ss.review_pick)]
item = ss.orders[sel]
left, right = st.columns([5, 6])
with left:
    st.subheader("What OCR read")
    legend = " ".join(f"<span style='color:{c}'>■ {n}</span>" for n, c in dict(SECTIONS.values()).items())
    st.markdown(legend, unsafe_allow_html=True)
    focus = st.selectbox("Highlight a field", ["(none)"] + sorted(item["boxes"]), key=sel + "focus")
    st.image(annotate(item["image"], item["boxes"], None if focus == "(none)" else focus), width="stretch")

draft, problems = None, list(item["problems"])
if item["engine"] == ENGINE:
    right.warning(
        "Read by the AI fallback, not the local parser: there are no field boxes to compare against. "
        "Check every field against the image before booking."
    )
if item["error"]:
    right.error(f"Could not read this file as an order: {item['error']}")
    if not item["ai_tried"]:
        right.info("Turn on **AI fallback for unknown layouts** above to let the AI model read it.")
    elif right.button("Try the AI again", key=sel + "retry"):
        ss.attempts[sel] = item["attempt"] + 1
        st.rerun()
else:
    with right:
        # Edits are stored on the item, so switching orders keeps them and the queue books what you saw.
        item["edited"] = review_form(json.loads(json.dumps(item["edited"])), sel)
        draft, problems = draft_of(item)
        item["problems"] = problems

st.divider()
back, mid, fwd = st.columns([1, 8, 1], vertical_alignment="center")
back.button("◀ Previous", key="checks_back", on_click=step_order, args=(-1,), disabled=len(labels) < 2, width="stretch")
mid.markdown(f"**{item['name']}** · {item['status']} · order {labels.index(ss.review_pick) + 1} of {len(labels)}")
fwd.button("Next ▶", key="checks_fwd", on_click=step_order, args=(1,), disabled=len(labels) < 2, width="stretch")
st.subheader("Checks")
fk_ok, fk_msg = fakturama_ready()
fk_closed = not fk_ok and "is not open" in fk_msg  # Book starts it; any other problem needs a human
if problems:
    for pr in problems:
        st.error(plain(pr))
elif draft is not None:
    st.success("All arithmetic and format checks pass.")
    rows = preview(draft) if fk_ok else [("Fakturama", "closed: Book starts it" if fk_closed else fk_msg)]
    if fk_closed:
        st.info("Fakturama is closed. Booking starts it on the test workspace first.")
    elif not fk_ok:
        st.error(plain(fk_msg))
    st.table(pd.DataFrame(rows, columns=["What", "Fakturama will"]))

busy = running() or bool(ss.queue) or ss.current is not None
bookable = [k for k, it in ss.orders.items() if not it["problems"] and it["status"] in ("new", "busy")]


def ensure_fakturama() -> bool:
    """Start Fakturama if it is closed and wait until it serves its database."""
    from image2cash import config
    from image2cash.ui.core import AutomationError, start_fakturama

    first = (
        " First run: Fakturama asks for a working directory in a dialog that may open behind this "
        "browser window. Choose a new, empty folder (not your real Fakturama data); the run then continues."
        if config.first_run()
        else ""
    )
    try:
        with st.spinner(f"Starting Fakturama on the test workspace…{first}"):
            start_fakturama()
        ss.start_error = None
        return True
    except AutomationError as exc:
        ss.start_error = f"Could not start Fakturama: {exc}"
        return False


def enqueue(keys: list[str], batch: bool = False) -> None:
    if not ensure_fakturama():
        return
    # a batch skips orders that cannot be booked as they stand, and says why
    ss.skipped = [
        (it["name"], f"not booked: {it['problems'][0]}")
        for k, it in ss.orders.items()
        if batch and k not in keys and it["status"] == "new" and it["problems"]
    ]
    first: dict[str, str] = {}  # one booking per reference: a PDF and its page image are one order
    for k in list(keys):
        ref = ss.orders[k]["edited"]["external_ref"]
        if ref in first:
            keys.remove(k)
            ss.skipped.append((ss.orders[k]["name"], f"same order ({ref}) as {first[ref]}; booked once"))
        else:
            first[ref] = ss.orders[k]["name"]
    if not keys:
        return
    for k in keys:
        ss.orders[k]["snapshot"] = ss.orders[k]["edited"]  # freeze what was reviewed
        ss.orders[k]["status"] = "queued"
        ss.orders[k]["tries"] = 0
    ss.queue = list(keys)
    ss.batch = batch
    ss.paused = None
    launch(ss.queue.pop(0))
    log.info("queue started: %s", [ss.orders[k]["name"] for k in keys])


c1, c2, c3 = st.columns([2, 2, 3])
if c1.button(
    "Book this order",
    type="primary",
    disabled=busy
    or not (fk_ok or fk_closed)
    or bool(problems)
    or draft is None
    or item["status"] not in ("new", "busy"),
):
    enqueue([sel])
    st.rerun()
if c2.button(f"Book all ready ({len(bookable)})", disabled=busy or not (fk_ok or fk_closed) or not bookable):
    enqueue(bookable, batch=True)
    st.rerun()
if ss.paused:
    c3.warning(ss.paused)
    if c3.button("Resume queue", disabled=running() or not (fk_ok or fk_closed)) and ensure_fakturama():
        ss.paused = None
        launch(ss.queue.pop(0))
        st.rerun()
elif ss.queue or ss.current:
    c3.info(f"Booking {ss.orders[ss.current]['name'] if ss.current else '…'}; {len(ss.queue)} waiting.")
if ss.get("start_error"):
    st.error(plain(ss.start_error))
if ss.skipped and not (ss.queue or ss.current):
    st.warning(f"Batch finished; {len(ss.skipped)} order(s) skipped. Fakturama may hold their half-done editors.")
    st.table(pd.DataFrame(ss.skipped, columns=["Order", "Why it was skipped"]))
progress()

CODE = {status: code for code, status in STATUS.items()}


# The run follows the order picked at the top (one navigator for review, checks and run).
if item.get("run_dir") and not running():
    st.subheader("Run")
    st.markdown(f"**{item['name']}** · {item['status']}")
    show_run(Path(item["run_dir"]), CODE.get(item["status"], 1), sha=sel)
