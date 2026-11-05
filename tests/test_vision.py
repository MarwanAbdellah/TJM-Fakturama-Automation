"""The opt-in AI fallback, offline: a mock HTTP transport stands in for the API."""

import json
from pathlib import Path

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")

from image2cash import extract as ex  # noqa: E402
from image2cash.extract import vision  # noqa: E402
from image2cash.extract.layout import parse  # noqa: E402
from image2cash.extract.ocr import load  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TRUTH = parse(load(Path(__file__).parent / "fixtures" / "order_WEB-2026-0714-A17.ocr.json"), source="fixture")


def client(reply: dict, seen: list):
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    return anthropic.Anthropic(
        api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(handle))
    )


def message(text: str, stop: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": vision.MODEL,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def test_request_and_parse():
    seen: list = []
    draft = vision.vision_draft(SAMPLES / "order_RX-1001.png", client(message(TRUTH.model_dump_json()), seen))
    body = seen[0]
    assert body["model"] == vision.MODEL
    assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert draft.items == TRUTH.items and draft.gross_total == TRUTH.gross_total
    assert draft.engine == vision.ENGINE


def test_refusal_is_an_error():
    with pytest.raises(vision.VisionError, match="refusal"):
        vision.vision_draft(SAMPLES / "order_RX-1001.png", client(message("", "refusal"), []))


def test_fallback_only_when_parser_fails(monkeypatch):
    called = []
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))
    monkeypatch.setattr(vision, "vision_draft", lambda image: called.append(image) or TRUTH)

    assert ex.extract_draft(SAMPLES / "order_RX-1001.png").error  # off: the parser's error stays
    assert not called
    r = ex.extract_draft(SAMPLES / "order_RX-1001.png", ai=True)
    assert r.engine == vision.ENGINE and r.draft == TRUTH and r.boxes == {}


def test_fallback_failure_keeps_parser_error(monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))

    def fail(image):
        raise vision.VisionError("no key")

    monkeypatch.setattr(vision, "vision_draft", fail)
    r = ex.extract_draft(SAMPLES / "order_RX-1001.png", ai=True)
    assert r.draft is None and "AI fallback: no key" in r.error
