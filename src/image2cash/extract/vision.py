"""Opt-in fallback for layouts the template parser cannot read: an AI model reads the image.

Any OpenAI-compatible chat-completions API works (config.VLM_BASE_URL / VLM_MODEL / VLM_API_KEY);
the default is Gemini. The image leaves this machine unless the URL is a local server, so it
only runs when the caller asks for it (app toggle, ``--ai`` on the CLI). Its draft goes through
the same ``check_order``, human review and DB checks as a parsed one; it has no field boxes.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import requests

from image2cash import config
from image2cash.models import PAYMENT_CODE_LABELS, PAYMENT_CODES, OrderDraft

ENGINE = "ai-vision"
RETRY = {429, 500, 502, 503, 504}  # busy or rate-limited: worth another try
WAITS = (3, 10, 30)  # seconds before tries 2, 3 and 4
HINTS = {
    400: "the API rejected the request; check VLM_MODEL in .env",
    401: "check VLM_API_KEY in .env",
    403: "check VLM_API_KEY in .env",
    404: "check VLM_MODEL and VLM_BASE_URL in .env",
    429: "rate limit or quota reached; wait or check the API plan",
    503: "the model is overloaded; try again in a few minutes",
}
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

PROMPT = f"""This image is a customer's sales order. Copy it into the schema.
- Copy values exactly as printed. Never compute, guess or fill in a value the order does not show;
  a missing text field is an empty string and a missing number is 0 (the validator flags both).
- vat_pct: if the order prints one VAT rate for the whole order, use it on every line.
- Money and quantities are plain numbers with "." as the decimal separator; no currency signs.
- Percentages are numbers (19 means 19 %). Dates are YYYY-MM-DD.
- currency is the ISO code (USD, EUR, ...).
- line_net, net_total, vat_total and gross_total are the totals printed on the order.
- payment.method is one of {", ".join(PAYMENT_CODES)} when the printed method means the same;
  otherwise copy it as printed and set payment.code to the matching entry of
  {", ".join(PAYMENT_CODE_LABELS)} (leave it null if none clearly fits).
- payment.status is PAID, UNPAID or OTHER; paid_date only if the order prints one.
- If the order shows one address only, use it for both billing and delivery.
- external_ref is the customer's order or PO number; it is often labelled Order #, Order No.,
  PO, PO Number, Purchase Order or Reference. customer_id is a customer or account number.
Leave source, engine and min_confidence at their defaults."""


class VisionError(RuntimeError):
    pass


def _api_error(r) -> str:
    """'AI API error 503: <the provider's own message> (<what to do>)'."""
    try:
        body = r.json()
        body = body[0] if isinstance(body, list) else body  # Gemini wraps the error in a list
        msg = body["error"]["message"]
    except Exception:  # noqa: BLE001 - not the usual error shape: show the raw text
        msg = (r.text or "").strip()[:300]
    hint = HINTS.get(r.status_code)
    return f"AI API error {r.status_code}: {msg}" + (f" ({hint})" if hint else "")


def _again(problems) -> str:
    """Second reading: tell the model what the checks found in its first one."""
    if not problems:
        return ""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "\n\nA first reading of this image failed these checks. Read the image again and fix every "
        f"one that is a reading mistake; keep values that really are printed that way:\n{listed}"
    )


def _retry_after(r) -> float | None:
    try:
        return min(float(r.headers.get("Retry-After")), 60.0)
    except (AttributeError, TypeError, ValueError):
        return None


def vision_draft(image: Path, post=requests.post, sleep=time.sleep, feedback: list[str] = ()) -> OrderDraft:
    """One structured-output call: image -> OrderDraft. Raises VisionError on any failure."""
    media = MEDIA.get(image.suffix.lower())
    if media is None:
        raise VisionError(f"unsupported image type {image.suffix}")
    local = config.VLM_BASE_URL.startswith(("http://localhost", "http://127.0.0.1"))
    if not config.VLM_API_KEY and not local:
        raise VisionError("no API key: set VLM_API_KEY in .env")
    data = base64.standard_b64encode(image.read_bytes()).decode()
    body = {
        "model": config.VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT + _again(feedback)},
                    {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}},
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "OrderDraft", "schema": OrderDraft.model_json_schema()},
        },
    }
    for tries, wait in enumerate((*WAITS, None), 1):
        r = None
        try:
            r = post(
                config.VLM_BASE_URL.rstrip("/") + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {config.VLM_API_KEY}"},
                timeout=180,
            )
        except requests.RequestException as exc:
            error = f"AI API unreachable: {exc} (check the network and VLM_BASE_URL)"
        else:
            if r.status_code == 200:
                break
            error = _api_error(r)
            if r.status_code not in RETRY:
                raise VisionError(error)
        if wait is None:
            raise VisionError(f"{error}; gave up after {tries} tries")
        sleep((r is not None and _retry_after(r)) or wait)
    try:
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") not in ("stop", None):
            raise VisionError(f"AI extraction stopped: {choice['finish_reason']}")
        draft = OrderDraft.model_validate(json.loads(choice["message"]["content"]))
    except VisionError:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed reply, invalid JSON or a draft that fails the schema
        raise VisionError(f"AI reply unusable: {exc}") from exc
    return draft.model_copy(update={"source": str(image), "engine": ENGINE, "min_confidence": 0.0})
