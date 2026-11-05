"""Opt-in fallback for layouts the template parser cannot read: Claude reads the image.

This sends the order image to Anthropic, so it only runs when the caller asks for it
(app checkbox, ``--ai`` on the CLI) and credentials exist. Its draft goes through the
same ``check_order``, human review and DB checks as a parsed one; it has no field boxes.
"""

from __future__ import annotations

import base64
from pathlib import Path

from image2cash.models import OrderDraft

MODEL = "claude-opus-5-5"
ENGINE = "claude-vision"
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


def available() -> bool:
    """The SDK is installed (``uv sync --extra llm``). Credentials are checked by the call itself."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def vision_draft(image: Path, client=None) -> OrderDraft:
    """One structured-output call: image -> OrderDraft. Raises VisionError on any failure."""
    try:
        import anthropic
    except ImportError as exc:
        raise VisionError("the AI fallback needs the 'llm' extra: uv sync --extra llm") from exc
    media = MEDIA.get(image.suffix.lower())
    if media is None:
        raise VisionError(f"unsupported image type {image.suffix}")
    data = base64.standard_b64encode(image.read_bytes()).decode()
    try:
        client = client or anthropic.Anthropic()
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a refused request is retried on another model inside the same call
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            output_format=OrderDraft,
        )
    except anthropic.AuthenticationError as exc:
        raise VisionError("no valid Anthropic credentials (set ANTHROPIC_API_KEY)") from exc
    except anthropic.APIError as exc:  # rate limit, 5xx and network errors are already retried by the SDK
        raise VisionError(f"Claude API error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - missing credentials raise a plain TypeError, bad JSON a ValidationError
        raise VisionError(f"AI extraction failed: {exc}") from exc
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise VisionError(f"AI extraction stopped: {response.stop_reason}")
    return response.parsed_output.model_copy(update={"source": str(image), "engine": ENGINE, "min_confidence": 0.0})
