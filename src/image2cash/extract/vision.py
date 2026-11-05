"""Opt-in fallback for layouts the template parser cannot read: an AI model reads the image.

Any OpenAI-compatible chat-completions API works (config.VLM_BASE_URL / VLM_MODEL / VLM_API_KEY);
the default is Gemini. The image leaves this machine unless the URL is a local server, so it
only runs when the caller asks for it (app toggle, ``--ai`` on the CLI). Its draft goes through
the same ``check_order``, human review and DB checks as a parsed one; it has no field boxes.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import requests

from image2cash import config
from image2cash.models import OrderDraft

ENGINE = "ai-vision"
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

PROMPT = """This image is a customer's sales order. Copy it into the schema.
- Copy values exactly as printed. Never compute, guess or fill in a value the order does not show;
  if a required field is missing, use an empty string and the validator will flag it.
- Money and quantities are plain numbers with "." as the decimal separator; no currency signs.
- Percentages are numbers (19 means 19 %). Dates are YYYY-MM-DD.
- currency is the ISO code (USD, EUR, ...).
- line_net, net_total, vat_total and gross_total are the totals printed on the order.
- payment.status is PAID, UNPAID or OTHER; paid_date only if the order prints one.
- If the order shows one address only, use it for both billing and delivery.
- external_ref is the customer's order or PO number.
Leave source, engine and min_confidence at their defaults."""


class VisionError(RuntimeError):
    pass


def vision_draft(image: Path, post=requests.post) -> OrderDraft:
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
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}},
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "OrderDraft", "schema": OrderDraft.model_json_schema()},
        },
    }
    try:
        r = post(
            config.VLM_BASE_URL.rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {config.VLM_API_KEY}"},
            timeout=180,
        )
    except requests.RequestException as exc:
        raise VisionError(f"AI API unreachable: {exc}") from exc
    if r.status_code != 200:
        raise VisionError(f"AI API error {r.status_code}: {r.text[:300]}")
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
