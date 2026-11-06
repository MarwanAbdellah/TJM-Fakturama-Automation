"""Brief §2: select or create the Debtor from the open Order."""

from __future__ import annotations

import time

from image2cash.models import Address
from image2cash.steps import selector as S
from image2cash.steps.context import Ctx
from image2cash.ui.core import AutomationError, ManualReview, _visible, read, wait_for
from image2cash.verify import db

HEADERS = ["No.", "First Name", "Name", "Company", "ZIP", "City", "address type"]
TITLE = "Select the address"

# UNTDID 4461 codes behind Fakturama's payment-code dropdown (calibrated:
# the seeded 'Cash' row stores ZZZ = 'Mutually defined').
UNTDID = {"Credit transfer": "30", "Credit card": "54", "SEPA direct debit": "59"}


def _exact(ctx: Ctx):
    d = ctx.order.debtor
    return db.contacts_matching(d.company, d.first_name, d.last_name, d.billing.zip, d.billing.city)


def chosen_id(ctx: Ctx) -> int | None:
    """The Debtor this run picked; on resume (no pick this run) the one exact match,
    after the same address respelling the debtor step does."""
    if ctx.contact_id is None:
        _adopt_stored_spelling(ctx)
        matches = _exact(ctx)
        return matches[0].id if len(matches) == 1 else None
    return ctx.contact_id


def is_debtor_of(ctx: Ctx, document_id: int) -> bool:
    """Every address on the saved document comes from the Debtor this run picked.
    (FKT_DOCUMENT.FK_CONTACT stays empty; each receiver row keeps ORIGINCONTACTID.)"""
    want = chosen_id(ctx)
    rows = db.document_receivers(document_id)
    return want is not None and bool(rows) and all(r["contact_id"] == want for r in rows)


def _row_is_target(ctx: Ctx, contact=None):
    d = ctx.order.debtor

    def ok(r) -> bool:
        if contact is not None and contact.number and r.get("No.") and not S.ocr_eq(r.get("No."), contact.number):
            return False
        return (
            S.prefix_match(r.get("Company"), d.company)
            and S.prefix_match(r.get("First Name"), d.first_name)
            and S.prefix_match(r.get("Name"), d.last_name)
            and S.ocr_eq(r.get("ZIP"), d.billing.zip)
            and S.prefix_match(r.get("City"), d.billing.city)
        )

    return ok


def _adopt_stored_spelling(ctx: Ctx) -> None:
    """The order's '04101 Portland, ME' is a saved contact's 'ME 04101 Portland': the same
    address, spelled another way (same company, name and street). Use the saved spelling,
    so the exact match (brief 2.3) selects that contact and every later check agrees."""
    from image2cash.validate import same_place

    d = ctx.order.debtor
    b = d.billing
    rows = db.query(
        'SELECT a."ZIP", a."CITY" FROM "FKT_CONTACT" c JOIN "FKT_ADDRESS" a ON a."CONTACT_ADDRESSES" = c."ID" '
        'WHERE TRIM(c."COMPANY") = ? AND TRIM(c."FIRSTNAME") = ? AND TRIM(c."NAME") = ? AND TRIM(a."STREET") = ? '
        f"AND {db.live('c')} AND {db.live('a')}",
        (d.company.strip(), d.first_name.strip(), d.last_name.strip(), b.street.strip()),
    )
    stored = {((z or "").strip(), (c or "").strip()) for z, c in rows if same_place(z or "", c or "", b.zip, b.city)}
    if len(stored) != 1 or stored == {(b.zip, b.city)}:
        return
    z, c = stored.pop()

    def respelled(a: Address) -> Address:
        return a.model_copy(update={"zip": z, "city": c}) if a.key() == b.key() else a

    debtor = d.model_copy(update={"billing": respelled(b), "delivery": respelled(d.delivery)})
    ctx.order = ctx.order.model_copy(update={"debtor": debtor})
    ctx.warnings.append(f"address {b.zip} {b.city} taken as the saved contact's {z} {c}")
    ctx.record("2.3 address spelling", "adopted", order=f"{b.zip} {b.city}", saved=f"{z} {c}")


def select_or_create(ctx: Ctx) -> None:
    _adopt_stored_spelling(ctx)
    d = ctx.order.debtor
    matches = _exact(ctx)
    if len(matches) > 1:
        raise ManualReview(
            f"{len(matches)} debtors match {d.company}/{d.first_name} {d.last_name}/{d.billing.zip} {d.billing.city}"
        )
    if not matches:
        # Probably the same customer, spelled differently: same company and ZIP ('Boston'
        # vs 'Boston, MA'), or Fakturama's own duplicate rule, same name and street
        # ('04101' vs 'ME 04101'). A human decides rather than we create a duplicate.
        near = db.query(
            'SELECT c."CUSTOMERNUMBER", a."STREET", a."ZIP", a."CITY" FROM "FKT_CONTACT" c '
            'JOIN "FKT_ADDRESS" a ON a."CONTACT_ADDRESSES" = c."ID" '
            'WHERE ((TRIM(c."COMPANY") = ? AND TRIM(a."ZIP") = ?) '
            'OR (TRIM(c."FIRSTNAME") = ? AND TRIM(c."NAME") = ? AND TRIM(a."STREET") = ?)) '
            f"AND {db.live('c')} AND {db.live('a')}",
            (
                d.company.strip(),
                d.billing.zip.strip(),
                d.first_name.strip(),
                d.last_name.strip(),
                d.billing.street.strip(),
            ),
        )
        if near:
            raise ManualReview(
                f"debtor {d.company!r} looks like an existing contact with other details: {near} "
                f"(order has {d.billing.street}, {d.billing.zip} {d.billing.city}); fix the order or the contact"
            )

    # 2.1-2.3: the existence check is done in the Order's selector, as briefed.
    dialog, icon = S.open_selector(ctx, "Addresses", TITLE)
    grid = S.search(ctx, dialog, d.company, HEADERS, auto_accepts=False)
    shot = ctx.shot("debtor_selector", [(icon, "select existing contact")])
    if grid is None:
        # auto-accepted a single filtered row
        if len(matches) != 1:
            raise ManualReview("selector auto-accepted a contact that is not an exact match; review the Order")
        ctx.contact_id = matches[0].id
        ctx.record("2.3 debtor select", "ok", how="auto-accepted unique row", contact=matches[0].id, shot=shot)
    elif len(matches) == 1:
        S.pick(ctx, dialog, grid, _row_is_target(ctx, matches[0]), "Company")
        ctx.contact_id = matches[0].id
        ctx.record("2.3 debtor select", "ok", how="picked exact row", contact=matches[0].id, shot=shot)
    else:
        visible = [r.cells for r in grid.rows if _row_is_target(ctx)(r)]
        if visible:
            raise ManualReview(f"grid shows a matching-looking row the DB does not confirm: {visible}")
        S.press(dialog, "Cancel")
        ctx.record("2.3 debtor select", "missing", rows_seen=len(grid.rows), shot=shot)
        create(ctx)
        reselect(ctx)
    # 2.4 (the Order's address boxes) is checked just before Save: order.complete_and_save


def reselect(ctx: Ctx) -> None:
    """2.12: reopen Select the address and pick the newly saved Debtor."""
    d = ctx.order.debtor
    matches = _exact(ctx)
    if len(matches) != 1:
        raise AutomationError(f"after saving, {len(matches)} exact debtors exist (expected 1)")
    dialog, _ = S.open_selector(ctx, "Addresses", TITLE)
    grid = S.search(ctx, dialog, d.company, HEADERS, auto_accepts=False)
    if grid is not None:
        S.pick(ctx, dialog, grid, _row_is_target(ctx, matches[0]), "Company")
    ctx.contact_id = matches[0].id
    ctx.record("2.12 debtor reselect", "ok", contact=matches[0].id, shot=ctx.shot("debtor_reselected"))


def verify_order_addresses(ctx: Ctx) -> None:
    """2.4 / 2.13: the populated Invoice/Delivery address boxes match the image.

    Runs just before the Order is saved. Delivery is read first and Invoice last, so
    the Invoice address tab is left active: Fakturama 2.2.0 crashes saving with the
    Delivery tab active. No other step checks the addresses (the DB check after
    saving covers number, date, total and lines)."""
    ui, d = ctx.ui, ctx.order.debtor
    ctx.back_to_order()
    ed = ui.editor_pane()
    seen = {}
    tabs = {c.element_info.name: c for c in ed.descendants(control_type="TabItem") if _visible(c)}
    for tab, addr in (("Delivery address", d.delivery), ("Invoice address", d.billing)):
        item = tabs.get(tab)
        if item is None:
            seen[tab] = None
            continue
        ui.click(item)
        folder = item.parent()

        def shown(folder=folder, addr=addr):
            edits = [c for c in folder.descendants(control_type="Edit") if _visible(c)]
            text = read(edits[0]) if edits else ""
            flat = " ".join(text.split())
            return text, [v for v in (addr.street, addr.zip, addr.city) if v not in flat]

        try:  # poll until the tab's box shows the address (was a fixed 0.5 s wait)
            wait_for(lambda: not shown()[1], 1.5, interval=0.1, what=f"{tab} box")
        except AutomationError:
            pass
        text, missing = shown()
        seen[tab] = text
        if missing:
            ctx.record(
                "2.4 order addresses", "FAIL", tab=tab, missing=missing, text=text, shot=ctx.shot("order_addresses")
            )
            raise ManualReview(f"{tab} on the Order lacks {missing}: {text!r}")
    if seen.get("Delivery address") is None and not d.same_delivery:
        ctx.warnings.append("Order shows no separate Delivery address tab; delivery address verified in DB only")
    ctx.record(
        "2.4 order addresses",
        "ok",
        **{k: (v or "").replace("\r\n", " | ") for k, v in seen.items()},
        shot=ctx.shot("order_addresses"),
    )


# ---------------------------------------------------------------------------
def create(ctx: Ctx) -> None:
    """2.5-2.11 with the Order tab left open."""
    ui, d = ctx.ui, ctx.order.debtor
    # 2.10.1-2.10.6 first. Calibrated deviation from the brief's order: the
    # Debtor editor loads its Payment list once when it opens, so a payment
    # method created while it is open is not selectable there. The Order stays
    # open either way.
    description = ensure_payment_method(ctx)
    ui.nav("New Contact")  # 2.5
    wait_for(lambda: ui.active_editor().lstrip("*") == "New Debtor", 10, what="New Debtor editor")
    ed = ui.editor_pane()
    cid = ui.labelled("Customer ID", "Edit", root=ed)
    ui.set_text(ui.labelled("Company", "Edit", root=ed), d.company, leave="none")  # multi-line field
    first, last = ui.row_controls("First Name Last Name", "Edit", root=ed)[:2]
    ui.set_text(first, d.first_name)
    ui.set_text(last, d.last_name)
    # Salutation stays '---' (2.6)

    _fill_address(ctx, d.billing, with_contact=True)  # 2.7
    types = ["Invoice address"] + (["Delivery address"] if d.same_delivery else [])
    _set_address_types(ctx, types)  # 2.8
    shot_main = ctx.shot("debtor_main_address")
    if not d.same_delivery:
        _add_delivery_address(ctx, d.delivery)

    # 2.9 Miscellaneous
    ui.click(ui.find("Miscellaneous", "TabItem", root=ed))
    time.sleep(0.6)
    if d.alias:
        ui.set_text(ui.labelled("Alias name", "Edit", root=ed), d.alias)
    disc = ui.labelled("Discount", "Edit", root=ed)
    from image2cash.validate import parse_amount

    if parse_amount(read(disc)) != 0:
        ui.set_text(disc, "0", check=lambda v: parse_amount(v) == 0)
    ui.combo_select(ui.labelled("Net or Gross", "ComboBox", root=ed), "Net")

    # 2.10 Payment (the method was ensured before this editor opened)
    pay = ui.labelled("Payment", "ComboBox", root=ed)
    if description.strip() not in [i.strip() for i in ui.combo_items(pay)]:
        raise ManualReview(f"payment {description!r} not offered in the open Debtor editor: {ui.combo_items(pay)}")
    ui.combo_select(pay, description)
    shot_misc = ctx.shot("debtor_misc", [(pay, "Payment")])

    ui.save()  # 2.11
    wait_for(
        lambda: not ui.active_editor().startswith("*") and ui.active_editor() != "New Debtor", 10, what="debtor saved"
    )
    rows = _exact(ctx)
    if len(rows) != 1:
        raise AutomationError(f"debtor not saved as one exact row (found {len(rows)})")
    addrs = db.contact_addresses(rows[0].id)
    con = db.contact(rows[0].id)
    ctx.created.append(f"Debtor {rows[0].number} ({d.company})")
    ctx.record(
        "2.11 debtor saved",
        "ok",
        contact=con,
        addresses=addrs,
        customer_id_kept=read(cid) if cid else None,
        shots=[shot_main, shot_misc],
    )
    _check_saved_addresses(ctx, addrs)
    ctx.close_saved_tab()


def _no_duplicate_warning(ctx: Ctx) -> None:
    """Fakturama warns 'Duplicate Contact' (same name and street) when the Street field
    is left; the modal would swallow the next keystrokes. Dismiss it and stop."""
    from image2cash.ui.core import AutomationError

    try:
        dup = ctx.ui.dialog("Duplicate Contact", timeout=1)
    except AutomationError:
        return
    text = " ".join(t.window_text() for t in dup.descendants(control_type="Text")).strip()
    shot = ctx.shot("duplicate_contact")
    S.press(dup, "OK")  # only a warning; the unsaved New Debtor stays open for a human
    ctx.record("2.7 duplicate contact", "REVIEW", fakturama=text, shot=shot)
    raise ManualReview(f"Fakturama says the contact exists already: {text}")


def _fill_address(ctx: Ctx, a: Address, with_contact: bool) -> None:
    ui, d = ctx.ui, ctx.order.debtor
    ed = ui.editor_pane()
    if a.name and a.name != d.company:
        ui.set_text(ui.labelled("additional name", "Edit", root=ed), a.name)
    ui.set_text(ui.labelled("Street", "Edit", root=ed), a.street)
    _no_duplicate_warning(ctx)
    if a.extra_lines:  # brief 2.7: only when the source supplies it
        ui.set_text(ui.labelled("Address specification", "Edit", root=ed), " ".join(a.extra_lines))
    z, city = ui.row_controls("ZIP - City", "Edit", root=ed)[:2]
    ui.set_text(z, a.zip)
    ui.set_text(city, a.city)
    ui.combo_select(ui.labelled("Country", "ComboBox", root=ed), a.country)
    if with_contact:
        if d.email:
            ui.set_text(ui.labelled("E-Mail", "Edit", root=ed), d.email)
        if d.phone:
            ui.set_text(ui.labelled("Telephone", "Edit", root=ed), d.phone)


def _set_address_types(ctx: Ctx, wanted: list[str]) -> None:
    """The 'address type' field opens a popup with one checkbox per role."""
    from pywinauto.keyboard import send_keys

    ui = ctx.ui
    ed = ui.editor_pane()
    field = ui.labelled("address type", "Edit", root=ed)
    fr = field.rectangle()
    button = [
        b
        for b in ed.descendants(control_type="Button")
        if _visible(b) and abs(b.rectangle().top - fr.top) < 4 and b.rectangle().left >= fr.right - 2
    ]
    if not button:
        raise AutomationError("address type dropdown button not found")
    ui.click(button[0])
    boxes = wait_for(
        lambda: {c.element_info.name: c for c in ui.main.descendants(control_type="CheckBox") if _visible(c)} or None,
        5,
        what="address type checkboxes",
    )
    for name in ("Invoice address", "Delivery address"):
        if name not in boxes:
            raise AutomationError(f"address type option {name!r} missing: {list(boxes)}")
        want = name in wanted
        if bool(boxes[name].get_toggle_state()) != want:
            boxes[name].toggle()
            time.sleep(0.2)
        if bool(boxes[name].get_toggle_state()) != want:
            raise AutomationError(f"could not set {name} to {want}")
    send_keys("{ESC}")
    time.sleep(0.4)


def _add_delivery_address(ctx: Ctx, a: Address) -> None:
    """Billing != delivery: add an additional address with the Delivery role."""
    ui = ctx.ui
    ed = ui.editor_pane()
    before = {c.element_info.name for c in ed.descendants(control_type="TabItem")}
    ui.click(ui.find("+", "Button", root=ed))
    new_tab = wait_for(
        lambda: [
            c for c in ed.descendants(control_type="TabItem") if c.element_info.name not in before and _visible(c)
        ],
        5,
        what="additional address tab",
    )[0]
    ui.click(new_tab)
    time.sleep(0.5)
    _fill_address(ctx, a, with_contact=False)
    _set_address_types(ctx, ["Delivery address"])
    ctx.record("2.8 delivery address", "ok", tab=new_tab.element_info.name, shot=ctx.shot("debtor_delivery_address"))


def _check_saved_addresses(ctx: Ctx, addrs: list[dict]) -> None:
    d = ctx.order.debtor

    def find(a: Address):
        return [x for x in addrs if x["street"] == a.street and x["zip"] == a.zip and x["city"] == a.city]

    bill = find(d.billing)
    if len(bill) != 1 or "BILLING" not in bill[0]["types"]:
        raise AutomationError(f"saved billing address/role wrong: {addrs}")
    if d.email and bill[0]["email"] != d.email:
        # Calibrated: Fakturama's e-mail validator rejects some addresses
        # (e.g. the reserved .test domain) and silently keeps the field empty.
        ctx.warnings.append(
            f"Fakturama did not persist e-mail {d.email!r} (its validator marks the field red); "
            "debtor saved without e-mail"
        )
    if d.phone and bill[0]["phone"] != d.phone:
        raise AutomationError(f"telephone not persisted: {bill[0]['phone']!r}")
    for a, row in ((d.billing, bill[0]),):
        if a.extra_lines and (row["spec"] or "") != " ".join(a.extra_lines):
            raise AutomationError(f"address specification not persisted: {row['spec']!r}")
    deliv = find(d.delivery)
    if len(deliv) != 1 or "DELIVERY" not in deliv[0]["types"]:
        raise AutomationError(f"saved delivery address/role wrong: {addrs}")


# ---------------------------------------------------------------------------
def _check_new_payment(ctx: Ctx, payment_id: int) -> None:
    """2.10.3 / 2.10.5: the saved payment has the brief's blank fields.

    The editor's 'Account' field is the Category combo (FK_CATEGORY); the three
    free texts are the unpaid/deposit/paid notes."""
    p = ctx.order.payment
    rows = db.query(
        'SELECT "NAME", "DESCRIPTION", "FK_CATEGORY", "DISCOUNTVALUE", "DISCOUNTDAYS", "NETDAYS", '
        '"UNPAIDTEXT", "DEPOSITTEXT", "PAIDTEXT" FROM "FKT_PAYMENT" WHERE "ID" = ?',
        (payment_id,),
    )
    if len(rows) != 1:
        ctx.record("2.10.3/2.10.5 payment fields", "FAIL", payment=payment_id, rows=len(rows))
        raise AutomationError(f"saved payment {p.method!r} not found by id {payment_id}")
    name, description, account, discount, discount_days, net_days, *texts = rows[0]
    wrong = []
    if (name or "").strip() != p.method:
        wrong.append(f"name {name!r}")
    if (description or "").strip() != p.method:
        wrong.append(f"description {description!r}")
    if account is not None:
        wrong.append(f"account {account!r}")
    for label, value in (("cash discount", discount), ("discount days", discount_days), ("net days", net_days)):
        if value not in (None, 0):
            wrong.append(f"{label} {value!r}")
    for label, value in zip(("unpaid text", "deposit text", "paid text"), texts):
        if (value or "").strip():
            wrong.append(f"{label} {value!r}")
    if wrong:
        ctx.record("2.10.3/2.10.5 payment fields", "FAIL", payment=payment_id, wrong=wrong)
        raise AutomationError(f"payment {p.method!r} saved with {', '.join(wrong)}")
    ctx.record("2.10.3/2.10.5 payment fields", "ok", payment=payment_id)


def ensure_payment_method(ctx: Ctx) -> str:
    """2.10.1-2.10.6. Returns the entry the Debtor's Payment combo shows for it
    (Fakturama lists payment *descriptions* there)."""
    ui, p = ctx.ui, ctx.order.payment
    code_label = p.payment_code
    if code_label is None:
        raise ManualReview(f"no payment-code mapping for {p.method!r}")
    rows = db.payments_named(p.method)
    if len(rows) > 1:
        raise ManualReview(f"{len(rows)} payment methods named {p.method!r}")
    if len(rows) == 1:
        row = rows[0]
        stored = (row["code"] or "").strip()
        if stored and stored != UNTDID[code_label]:
            raise ManualReview(
                f"payment {p.method!r} exists with code {stored!r}, expected {UNTDID[code_label]} ({code_label})"
            )
        if not stored:
            ctx.warnings.append(f"payment {p.method!r} exists without a payment code; reused (not edited)")
        ctx.record("2.10.2 payment method", "exists", id=row["id"], description=row["description"], code=row["code"])
        return (row["description"] or row["name"]).strip()

    # create it, Debtor editor stays open
    ui.nav("terms of payment")  # 2.10.1
    ctx.shot("payment_list")
    ui.press("Create a new term of payment")
    wait_for(lambda: ui.active_editor().lstrip("*") == "New Term of Payment", 10, what="payment editor")
    ed = ui.editor_pane()
    ui.set_text(ui.labelled("Name", "Edit", root=ed), p.method)  # 2.10.3
    ui.set_text(ui.labelled("Description", "Edit", root=ed), p.method)
    code = ui.combo_with_option(code_label, root=ed)  # label is an untranslated key
    ui.combo_select(code, code_label)  # 2.10.4
    for label in ("Cash discount", "Discount Days", "Net Days"):  # 2.10.5
        from image2cash.validate import parse_amount

        f = ui.labelled(label, "Edit", root=ed)
        if parse_amount(read(f)) != 0:
            ui.set_text(f, "0", check=lambda v: parse_amount(v) == 0)
    shot = ctx.shot("payment_new", [(code, code_label)])
    ui.save()  # 2.10.6
    wait_for(lambda: len(db.payments_named(p.method)) == 1, 10, what="payment saved")
    row = db.payments_named(p.method)[0]
    _check_new_payment(ctx, row["id"])
    ctx.created.append(f"Payment {p.method}")
    ctx.record("2.10.6 payment created", "ok", id=row["id"], code=row["code"], shot=shot)
    ctx.close_saved_tab()
    return (row["description"] or row["name"]).strip()
