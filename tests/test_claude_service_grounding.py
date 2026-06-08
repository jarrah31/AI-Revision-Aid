"""Unit tests for ground_matches_to_ko (vision call mocked)."""
import json
import types

import backend.services.claude_service as cs


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn", in_tok=50, out_tok=30):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok)
        self.stop_reason = stop_reason


def _client_returning(fn):
    class _C:
        class messages:
            @staticmethod
            def create(**kwargs):
                return fn(kwargs)
    return _C()


def _stub_settings(monkeypatch):
    monkeypatch.setattr(cs, "_get_ai_setting", lambda k: cs.AI_SETTING_DEFAULTS[k])


KO = {"id": 1, "question_text": "What is an organ?", "answer_text": "a group of tissues"}
CANDS = [
    {"id": 10, "question_text": "Name a group of tissues working together", "answer_text": "organ"},
    {"id": 11, "question_text": "Define homeostasis", "answer_text": "keeping internal conditions stable"},
]


def test_parses_supported_and_unsupported(monkeypatch):
    _stub_settings(monkeypatch)
    body = json.dumps({"results": [
        {"id": 10, "supported": True, "reasoning": "Organ defined in the box.",
         "snippet": "Organ: a group of tissues", "bbox_pct": {"x": 5, "y": 10, "w": 40, "h": 12}},
        {"id": 11, "supported": False, "reasoning": "Homeostasis not on this page.",
         "snippet": "", "bbox_pct": None},
    ]})
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(lambda kw: _FakeMessage(body)))

    results, usage = cs.ground_matches_to_ko(KO, b"\x89PNG-fake", CANDS)
    by_id = {r["past_paper_question_id"]: r for r in results}
    assert by_id[10]["supported"] is True
    assert by_id[10]["bbox_pct"] == {"x": 5.0, "y": 10.0, "w": 40.0, "h": 12.0}
    assert by_id[11]["supported"] is False
    assert by_id[11]["bbox_pct"] is None
    assert usage["cost_usd"] > 0


def test_image_block_is_sent(monkeypatch):
    _stub_settings(monkeypatch)
    captured = {}
    def grab(kw):
        captured["content"] = kw["messages"][0]["content"]
        return _FakeMessage(json.dumps({"results": []}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(grab))
    cs.ground_matches_to_ko(KO, b"fake-png-bytes", CANDS)
    kinds = [b["type"] for b in captured["content"]]
    assert "image" in kinds and "text" in kinds


def test_invalid_bbox_becomes_none(monkeypatch):
    _stub_settings(monkeypatch)
    body = json.dumps({"results": [
        {"id": 10, "supported": True, "reasoning": "x", "snippet": "y",
         "bbox_pct": {"x": 5, "y": 10, "w": 0, "h": 12}},  # zero width → invalid
    ]})
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(lambda kw: _FakeMessage(body)))
    results, _ = cs.ground_matches_to_ko(KO, b"png", [CANDS[0]])
    assert results[0]["bbox_pct"] is None
    assert results[0]["supported"] is True


def test_nonfinite_bbox_becomes_none(monkeypatch):
    _stub_settings(monkeypatch)
    body = json.dumps({"results": [
        {"id": 10, "supported": True, "reasoning": "x", "snippet": "y",
         "bbox_pct": {"x": 0, "y": 0, "w": float("nan"), "h": float("inf")}},
    ]})
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(lambda kw: _FakeMessage(body)))
    results, _ = cs.ground_matches_to_ko(KO, b"png", [CANDS[0]])
    assert results[0]["bbox_pct"] is None
    assert results[0]["supported"] is True


def test_parse_failure_keeps_all_supported_and_records_cost(monkeypatch):
    """A garbled body must NOT drop real matches — they survive ungrounded, and
    the call still records its cost."""
    _stub_settings(monkeypatch)
    monkeypatch.setattr(cs, "get_client",
                        lambda: _client_returning(lambda kw: _FakeMessage("not json at all")))
    results, usage = cs.ground_matches_to_ko(KO, b"png", CANDS)
    assert all(r["supported"] for r in results)
    assert all(r["bbox_pct"] is None for r in results)
    assert usage["cost_usd"] > 0


def test_api_error_keeps_all_supported(monkeypatch):
    _stub_settings(monkeypatch)
    def boom(kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(boom))
    results, usage = cs.ground_matches_to_ko(KO, b"png", CANDS)
    assert [r["past_paper_question_id"] for r in results] == [10, 11]
    assert all(r["supported"] for r in results)
    assert usage["cost_usd"] == 0.0


def test_no_candidates_makes_no_call(monkeypatch):
    _stub_settings(monkeypatch)
    def boom(kw):
        raise AssertionError("should not be called")
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(boom))
    results, usage = cs.ground_matches_to_ko(KO, b"png", [])
    assert results == []
    assert usage["cost_usd"] == 0.0
