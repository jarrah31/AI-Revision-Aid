"""Unit tests for detect_multiple_response_batch (Claude call mocked)."""
import json
import types
import pytest
import backend.services.claude_service as cs
from backend.services.claude_service import _normalise_multi_response


class _FakeMessage:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)


def _fake_client(payload):
    class _C:
        class messages:
            @staticmethod
            def create(**kwargs):
                return _FakeMessage(json.dumps(payload))
    return _C()


def test_detect_returns_results_aligned(monkeypatch):
    payload = {"results": [
        {"question_id": 1, "is_multiple_response": True, "stem": "Which two?",
         "select_count": 2,
         "options": [{"text": "a", "is_correct": True},
                     {"text": "b", "is_correct": True},
                     {"text": "c", "is_correct": False}]},
        {"question_id": 2, "is_multiple_response": False},
    ]}
    monkeypatch.setattr(cs, "get_client", lambda: _fake_client(payload))
    monkeypatch.setattr(cs, "_get_ai_setting", lambda k: cs.AI_SETTING_DEFAULTS[k])

    questions = [
        {"id": 1, "question_text": "Which two? a b c", "answer_text": "a; b"},
        {"id": 2, "question_text": "Define osmosis.", "answer_text": "..."},
    ]
    results, usage = cs.detect_multiple_response_batch(questions, "Science")

    assert len(results) == 2
    assert results[0]["select_count"] == 2
    assert {o["text"] for o in results[0]["options"] if o["is_correct"]} == {"a", "b"}
    assert results[1] is None
    assert usage["input_tokens"] == 10


@pytest.mark.parametrize("r", [
    None,
    {"is_multiple_response": False},
    # fewer than 2 valid options
    {"is_multiple_response": True, "stem": "S", "select_count": 1,
     "options": [{"text": "a", "is_correct": True}]},
    # no correct option
    {"is_multiple_response": True, "stem": "S", "select_count": 2,
     "options": [{"text": "a", "is_correct": False}, {"text": "b", "is_correct": False}]},
    # select_count out of range (exceeds option count). NB: an absent/zero
    # select_count is NOT degenerate — it falls back to n_correct (see
    # _normalise_multi_response); only a non-falsy out-of-range value is rejected.
    {"is_multiple_response": True, "stem": "S", "select_count": 3,
     "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}]},
    # empty stem
    {"is_multiple_response": True, "stem": "", "select_count": 1,
     "options": [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": False}]},
])
def test_normalise_rejects_degenerate(r):
    assert _normalise_multi_response(r) is None


def test_normalise_aligns_select_count_to_correct_count():
    """An in-range but inconsistent select_count is clamped to the number of
    correct options, so the UI's 'Select N' label always matches marking."""
    r = {
        "is_multiple_response": True,
        "stem": "Pick the correct ones",
        "select_count": 3,  # model says 3, but only 2 options are correct
        "options": [
            {"text": "a", "is_correct": True},
            {"text": "b", "is_correct": True},
            {"text": "c", "is_correct": False},
            {"text": "d", "is_correct": False},
        ],
    }
    result = _normalise_multi_response(r)
    assert result is not None
    assert result["select_count"] == 2
