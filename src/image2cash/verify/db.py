"""Read-only HSQLDB access to Fakturama's live database.

Used for two things only:
  * deciding exact matches (0 / 1 / many) before choosing select vs. create,
    because selector grids truncate cells and OCR cannot prove absence;
  * verifying what a Save actually persisted.

Only SELECT statements are issued and the JDBC connection is set read-only.
All writes happen through Fakturama's UI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from image2cash import config


def _clob(value: Any) -> Any:
    if value is not None and hasattr(value, "getSubString"):
        return str(value.getSubString(1, int(value.length())))
    return value


@contextmanager
def connect() -> Iterator[Any]:
    import jaydebeapi

    if not os.environ.get("JAVA_HOME") or not Path(os.environ["JAVA_HOME"]).is_dir():
        jre = config.FAKTURAMA_HOME / "jre"
        if jre.is_dir():
            os.environ["JAVA_HOME"] = str(jre)
    conn = jaydebeapi.connect(
        "org.hsqldb.jdbc.JDBCDriver",
        config.HSQL_URL,
        [config.HSQL_USER, config.HSQL_PASSWORD],
        [str(config.HSQL_JAR)],
    )
    try:
        conn.jconn.setReadOnly(True)
        yield conn
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[tuple]:
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError("read-only module: SELECT only")
    with connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            return [tuple(_clob(v) for v in row) for row in cur.fetchall()]
        finally:
            cur.close()


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


_LIVE = "COALESCE({a}DELETED, FALSE) = FALSE"


def live(alias: str = "") -> str:
    return _LIVE.format(a=f"{alias}." if alias else "")


# ---- master data ------------------------------------------------------------
@dataclass
class ContactRow:
    id: int
    number: str
    company: str
    first_name: str
    name: str
    address_id: int
    street: str
    zip: str
    city: str
    country: str
    types: list[str]
    payment_id: int | None


def contacts_matching(company: str, first: str, last: str, zip_: str, city: str) -> list[ContactRow]:
    """Debtors whose Company, First Name, Name, ZIP and City all match exactly
    (brief 2.3). Whitespace is trimmed on the DB side; case is significant."""
    rows = query(
        'SELECT c."ID", c."CUSTOMERNUMBER", c."COMPANY", c."FIRSTNAME", c."NAME", '
        'a."ID", a."STREET", a."ZIP", a."CITY", a."COUNTRYCODE", c."FK_PAYMENT" '
        'FROM "FKT_CONTACT" c JOIN "FKT_ADDRESS" a ON a."CONTACT_ADDRESSES" = c."ID" '
        f"WHERE c.\"DTYPE\" = 'Debitor' AND {live('c')} AND {live('a')} "
        'AND TRIM(c."COMPANY") = ? AND TRIM(c."FIRSTNAME") = ? AND TRIM(c."NAME") = ? '
        'AND TRIM(a."ZIP") = ? AND TRIM(a."CITY") = ?',
        (company.strip(), first.strip(), last.strip(), zip_.strip(), city.strip()),
    )
    out: dict[int, ContactRow] = {}
    for cid, num, comp, fn, nm, aid, street, z, ct, cc, pay in rows:
        if cid in out:
            continue
        out[cid] = ContactRow(
            cid,
            num or "",
            comp or "",
            fn or "",
            nm or "",
            aid,
            street or "",
            z or "",
            ct or "",
            cc or "",
            address_types(aid),
            pay,
        )
    return list(out.values())


def contact_addresses(contact_id: int) -> list[dict]:
    rows = query(
        'SELECT "ID", "NAME", "STREET", "ZIP", "CITY", "COUNTRYCODE", "EMAIL", "PHONE", "ADDRESSADDON" '
        f'FROM "FKT_ADDRESS" WHERE "CONTACT_ADDRESSES" = ? AND {live()} ORDER BY "ID"',
        (contact_id,),
    )
    keys = ("id", "name", "street", "zip", "city", "country", "email", "phone", "spec")
    return [dict(zip(keys, r), types=address_types(r[0])) for r in rows]


def address_types(address_id: int) -> list[str]:
    return [
        r[0]
        for r in query(
            'SELECT "T_ELEMENT" FROM "FKT_ADDRESS_CONTACTTYPES" WHERE "ADDRESS_CONTACTTYPES" = ?', (address_id,)
        )
    ]


def contact(contact_id: int) -> dict | None:
    rows = query(
        'SELECT "ID", "CUSTOMERNUMBER", "COMPANY", "FIRSTNAME", "NAME", "T_ALIAS", "DISCOUNT", '
        '"USENETGROSS", "FK_PAYMENT" FROM "FKT_CONTACT" WHERE "ID" = ?',
        (contact_id,),
    )
    if not rows:
        return None
    keys = ("id", "number", "company", "first_name", "name", "alias", "discount", "use_net_gross", "payment_id")
    return dict(zip(keys, rows[0]))


def payments_named(name: str) -> list[dict]:
    rows = query(
        'SELECT "ID", "NAME", "DESCRIPTION", "CODE", "DISCOUNTVALUE", "DISCOUNTDAYS", "NETDAYS" '
        f'FROM "FKT_PAYMENT" WHERE TRIM("NAME") = ? AND {live()}',
        (name.strip(),),
    )
    keys = ("id", "name", "description", "code", "discount", "discount_days", "net_days")
    return [dict(zip(keys, r)) for r in rows]


def vats_named(name: str) -> list[dict]:
    rows = query(
        f'SELECT "ID", "NAME", "DESCRIPTION", "TAXVALUE", "CODE" FROM "FKT_VAT" WHERE TRIM("NAME") = ? AND {live()}',
        (name.strip(),),
    )
    return [dict(id=r[0], name=r[1], description=r[2], value=_dec(r[3]), code=r[4]) for r in rows]


def products_with_sku(sku: str) -> list[dict]:
    rows = query(
        'SELECT p."ID", p."ITEMNUMBER", p."NAME", p."DESCRIPTION", p."PRICE1", p."COSTPRICE", p."QUANTITY", '
        'v."NAME", v."TAXVALUE" FROM "FKT_PRODUCT" p LEFT JOIN "FKT_VAT" v ON v."ID" = p."FK_VAT" '
        f'WHERE TRIM(p."ITEMNUMBER") = ? AND {live("p")}',
        (sku.strip(),),
    )
    keys = ("id", "sku", "name", "description", "price", "cost", "stock", "vat_name", "vat_value")
    return [dict(zip(keys, r)) for r in rows]


# ---- documents --------------------------------------------------------------
def documents_with_ref(cust_ref: str, dtype: str | None = None) -> list[dict]:
    sql = (
        'SELECT "ID", "NAME", "DTYPE", "CUSTOMERREF", "DOCUMENTDATE", "ORDERDATE", "TOTALVALUE", '
        '"PAID", "PAYDATE", "PAIDVALUE", "FK_PAYMENT", "FK_SRCDOCUMENT", "FK_CONTACT", "FK_DELIVERYCONTACT", '
        f'"NETGROSS", "PROGRESS" FROM "FKT_DOCUMENT" WHERE TRIM("CUSTOMERREF") = ? AND {live()}'
    )
    params: tuple = (cust_ref.strip(),)
    if dtype:
        sql += ' AND "DTYPE" = ?'
        params += (dtype,)
    keys = (
        "id",
        "number",
        "dtype",
        "cust_ref",
        "date",
        "order_date",
        "total",
        "paid",
        "pay_date",
        "paid_value",
        "payment_id",
        "src_id",
        "contact_id",
        "delivery_contact_id",
        "net_gross",
        "progress",
    )
    out = []
    for r in query(sql + ' ORDER BY "ID"', params):
        d = dict(zip(keys, r))
        d["total"] = _dec(d["total"])
        d["paid_value"] = _dec(d["paid_value"])
        d["date"] = str(d["date"])[:10] if d["date"] is not None else None
        d["order_date"] = str(d["order_date"])[:10] if d["order_date"] is not None else None
        d["pay_date"] = str(d["pay_date"])[:10] if d["pay_date"] is not None else None
        out.append(d)
    return out


def document_items(document_id: int) -> list[dict]:
    rows = query(
        'SELECT i."POSNR", i."ITEMNUMBER", i."NAME", i."QUANTITY", i."PRICE", i."ITEMREBATE", '
        'v."NAME", v."TAXVALUE", i."FK_PRODUCT" FROM "FKT_DOCUMENTITEM" i '
        'LEFT JOIN "FKT_VAT" v ON v."ID" = i."FK_VAT" '
        f'WHERE i."FK_DOCUMENT" = ? AND {live("i")} ORDER BY i."POSNR"',
        (document_id,),
    )
    keys = ("pos", "sku", "name", "qty", "price", "rebate", "vat_name", "vat_value", "product_id")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        for k in ("qty", "price", "rebate", "vat_value"):
            d[k] = _dec(d[k])
        out.append(d)
    return out


def document_receivers(document_id: int) -> list[dict]:
    rows = query(
        'SELECT "BILLINGTYPE", "COMPANY", "FIRSTNAME", "NAME", "STREET", "ZIP", "CITY", "COUNTRYCODE" '
        f'FROM "FKT_DOCUMENTRECEIVER" WHERE "FK_DOCUMENT" = ? AND {live()} ORDER BY "ID"',
        (document_id,),
    )
    keys = ("type", "company", "first_name", "name", "street", "zip", "city", "country")
    return [dict(zip(keys, r)) for r in rows]


def payment(payment_id: int) -> dict | None:
    rows = query('SELECT "ID", "NAME", "DESCRIPTION", "CODE" FROM "FKT_PAYMENT" WHERE "ID" = ?', (payment_id,))
    return dict(zip(("id", "name", "description", "code"), rows[0])) if rows else None
