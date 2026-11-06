"""The opt-in AI fallback, offline: a fake HTTP post stands in for the API."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from image2cash import extract as ex
from image2cash.extract import vision
from image2cash.extract.layout import parse
from image2cash.extract.ocr import load

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TRUTH = parse(load(Path(__file__).parent / "fixtures" / "order_WEB-2026-0714-A17.ocr.json"), source="fixture")


@pytest.fixture(autouse=True)
def gemini(monkeypatch):
    monkeypatch.setattr(vision.config, "VLM_BASE_URL", "https://example.test/v1beta/openai/")
    monkeypatch.setattr(vision.config, "VLM_API_KEY", "test-key")


def fake_post(content: str, status: int = 200, finish: str = "stop", seen: list | None = None):
    def post(url, json, headers, timeout):
        if seen is not None:
            seen.append((url, json, headers))
        reply = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        return NS(status_code=status, text="bad request" if status != 200 else "", json=lambda: reply)

    return post


def test_request_and_parse():
    seen: list = []
    draft = vision.vision_draft(SAMPLES / "order_RX-2001.png", fake_post(TRUTH.model_dump_json(), seen=seen))
    url, body, headers = seen[0]
    assert url == "https://example.test/v1beta/openai/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert body["model"] == vision.config.VLM_MODEL
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["response_format"]["type"] == "json_schema"
    assert draft.items == TRUTH.items and draft.gross_total == TRUTH.gross_total
    assert draft.engine == vision.ENGINE


@pytest.mark.parametrize(
    "post, match",
    [
        (fake_post("", status=400), "AI API error 400"),
        (fake_post("{}", finish="length"), "stopped: length"),
        (fake_post("not json"), "AI reply unusable"),
        (fake_post(json.dumps({"external_ref": "X"})), "AI reply unusable"),  # fails the OrderDraft schema
    ],
)
def test_bad_replies_are_errors(post, match):
    with pytest.raises(vision.VisionError, match=match):
        vision.vision_draft(SAMPLES / "order_RX-2001.png", post)


def test_no_key_is_an_error_unless_the_server_is_local(monkeypatch):
    monkeypatch.setattr(vision.config, "VLM_API_KEY", "")
    with pytest.raises(vision.VisionError, match="VLM_API_KEY"):
        vision.vision_draft(SAMPLES / "order_RX-2001.png", fake_post(TRUTH.model_dump_json()))
    monkeypatch.setattr(vision.config, "VLM_BASE_URL", "http://localhost:11434/v1")
    assert vision.vision_draft(SAMPLES / "order_RX-2001.png", fake_post(TRUTH.model_dump_json())).items == TRUTH.items


def test_fallback_only_when_parser_fails(monkeypatch):
    called = []
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))
    monkeypatch.setattr(vision, "vision_draft", lambda image: called.append(image) or TRUTH)

    assert ex.extract_draft(SAMPLES / "order_RX-2001.png").error  # off: the parser's error stays
    assert not called
    r = ex.extract_draft(SAMPLES / "order_RX-2001.png", ai=True)
    assert r.engine == vision.ENGINE and r.draft == TRUTH and r.boxes == {}


def test_fallback_failure_keeps_parser_error(monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))

    def fail(image):
        raise vision.VisionError("no key")

    monkeypatch.setattr(vision, "vision_draft", fail)
    r = ex.extract_draft(SAMPLES / "order_RX-2001.png", ai=True)
    assert (
        r.draft is None and "could not read this layout" in r.error and r.error.endswith("AI fallback failed: no key")
    )


def replies(*statuses):
    """A post that answers with each status in turn; errors carry Gemini's error shape."""
    good = fake_post(TRUTH.model_dump_json())
    left = list(statuses)

    def post(url, json, headers, timeout):
        status = left.pop(0)
        if status == 200:
            return good(url, json, headers, timeout)
        err = [{"error": {"code": status, "message": "This model is busy."}}]
        return NS(status_code=status, text="", headers={}, json=lambda: err)

    return post


def test_busy_model_is_retried():
    waits: list = []
    draft = vision.vision_draft(SAMPLES / "order_RX-2001.png", replies(503, 429, 200), sleep=waits.append)
    assert draft.items == TRUTH.items and waits == list(vision.WAITS[:2])


def test_retries_give_up_with_a_readable_error():
    waits: list = []
    with pytest.raises(vision.VisionError) as exc:
        vision.vision_draft(SAMPLES / "order_RX-2001.png", replies(*[503] * 4), sleep=waits.append)
    assert str(exc.value) == (
        "AI API error 503: This model is busy. (the model is overloaded; try again in a few minutes); "
        "gave up after 4 tries"
    )
    assert len(waits) == 3


def test_a_bad_key_is_not_retried():
    with pytest.raises(vision.VisionError, match=r"401: This model is busy. \(check VLM_API_KEY"):
        vision.vision_draft(SAMPLES / "order_RX-2001.png", replies(401), sleep=pytest.fail)


def test_a_failing_ai_reading_is_asked_again_with_the_problems(monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))
    asked: list = []
    blank = TRUTH.model_copy(update={"external_ref": ""})

    def read(image, feedback=()):
        asked.append(list(feedback))
        return TRUTH if feedback else blank

    monkeypatch.setattr(vision, "vision_draft", read)
    r = ex.extract_draft(SAMPLES / "order_RX-2001.png", ai=True)
    assert r.draft.external_ref == TRUTH.external_ref
    assert asked == [[], ["external reference is empty"]]


def test_the_second_reading_is_kept_only_if_better(monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda image: ([], None))
    worse = TRUTH.model_copy(update={"external_ref": "", "currency": "Euro"})
    first = TRUTH.model_copy(update={"external_ref": ""})
    monkeypatch.setattr(vision, "vision_draft", lambda image, feedback=(): worse if feedback else first)
    assert ex.extract_draft(SAMPLES / "order_RX-2001.png", ai=True).draft == first


def test_the_feedback_reaches_the_prompt():
    seen: list = []
    vision.vision_draft(
        SAMPLES / "order_RX-2001.png",
        fake_post(TRUTH.model_dump_json(), seen=seen),
        feedback=["external reference is empty"],
    )
    assert "- external reference is empty" in seen[0][1]["messages"][0]["content"][0]["text"]
