"""Unit tests for the hybrid match_ko_to_past_papers (Claude call mocked).

Strategy under test: BM25 shortlist (programmatic) -> AI verification of each
shortlist, chunked. The AI is mocked; we assert the orchestration:
  - returned ids are validated against each KO point's shortlist (no hallucinations),
  - at most MATCH_MAX_PER_KO matches per KO point,
  - usage is summed across chunks,
  - a failed chunk is non-fatal.
"""
import json
import logging
import types

import backend.services.claude_service as cs


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn", in_tok=10, out_tok=20):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok)
        self.stop_reason = stop_reason


def _client_returning(fn):
    """fn(prompt) -> _FakeMessage. Lets a test react to each chunk's prompt."""
    class _C:
        class messages:
            @staticmethod
            def create(**kwargs):
                prompt = kwargs["messages"][0]["content"]
                return fn(prompt)
    return _C()


def _stub_settings(monkeypatch):
    monkeypatch.setattr(cs, "_get_ai_setting", lambda k: cs.AI_SETTING_DEFAULTS[k])


def test_returns_validated_matches(monkeypatch):
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water movement"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "movement of water"}]

    def respond(prompt):
        return _FakeMessage(json.dumps({"matches": [
            {"ko_question_id": 1, "past_paper_question_id": 2}
        ]}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, usage = cs.match_ko_to_past_papers(ko, pp)
    assert matches == [{"ko_question_id": 1, "past_paper_question_id": 2}]
    assert usage["cost_usd"] > 0


def test_drops_hallucinated_ids_not_in_shortlist(monkeypatch):
    """An id the model returns that isn't in the KO point's candidate list is
    dropped — this is the core anti-hallucination guarantee."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "water"}]

    def respond(prompt):
        return _FakeMessage(json.dumps({"matches": [
            {"ko_question_id": 1, "past_paper_question_id": 2},      # valid
            {"ko_question_id": 1, "past_paper_question_id": 9999},   # invented
        ]}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert matches == [{"ko_question_id": 1, "past_paper_question_id": 2}]


def test_caps_matches_per_ko(monkeypatch):
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Explain osmosis water cell membrane", "answer_text": "water"}]
    # five genuinely osmosis-related candidates so BM25 shortlists them all
    pp = [{"id": 100 + i,
           "question_text": f"Question about osmosis water membrane number {i}",
           "answer_text": "water"} for i in range(5)]

    def respond(prompt):
        ids = _payload(prompt)["ko_points"][0]["candidate_ids"]
        return _FakeMessage(json.dumps({"matches": [
            {"ko_question_id": 1, "past_paper_question_id": pid} for pid in ids
        ]}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert len(matches) == cs.MATCH_MAX_PER_KO


def test_chunks_and_sums_usage(monkeypatch):
    """More KO points than MATCH_CHUNK_SIZE -> multiple calls, usage summed."""
    _stub_settings(monkeypatch)
    monkeypatch.setattr(cs, "MATCH_CHUNK_SIZE", 2)
    ko = [{"id": i, "question_text": f"Define term osmosis concept {i}", "answer_text": "x"}
          for i in range(1, 6)]  # 5 KO points -> 3 chunks at size 2
    pp = [{"id": 50, "question_text": "Explain the term osmosis concept", "answer_text": "x"}]

    calls = {"n": 0}
    def respond(prompt):
        calls["n"] += 1
        return _FakeMessage(json.dumps({"matches": []}), in_tok=10, out_tok=5)
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    _, usage = cs.match_ko_to_past_papers(ko, pp)
    assert calls["n"] == 3
    assert usage["input_tokens"] == 30  # 3 calls * 10


def test_failed_chunk_is_non_fatal(monkeypatch):
    """If one chunk errors, other chunks still contribute their matches."""
    _stub_settings(monkeypatch)
    monkeypatch.setattr(cs, "MATCH_CHUNK_SIZE", 1)
    ko = [{"id": 1, "question_text": "Define osmosis water", "answer_text": "x"},
          {"id": 2, "question_text": "Define respiration energy", "answer_text": "x"}]
    pp = [{"id": 10, "question_text": "Explain osmosis water", "answer_text": "x"},
          {"id": 20, "question_text": "Explain respiration energy", "answer_text": "x"}]

    def respond(prompt):
        item = _payload(prompt)["ko_points"][0]
        if item["ko_question_id"] == 1:
            return _FakeMessage("THIS IS NOT JSON")  # first chunk fails to parse
        return _FakeMessage(json.dumps({"matches": [
            {"ko_question_id": 2, "past_paper_question_id": 20}
        ]}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert matches == [{"ko_question_id": 2, "past_paper_question_id": 20}]


def test_long_answers_truncated_in_payload(monkeypatch):
    """Candidate answers (and KO answers) can be huge mark-scheme rubrics. The
    matcher only needs enough to judge topical equivalence, so the prompt payload
    must truncate them to MATCH_ANSWER_CHAR_CAP — otherwise a single chunk's input
    blows the per-minute input-token rate limit. Full text stays in the DB."""
    _stub_settings(monkeypatch)
    cap = cs.MATCH_ANSWER_CHAR_CAP
    long_ko_answer = "KO " + "osmosis water " * 200          # >> cap
    long_pp_answer = "PP " + "osmosis water " * 200
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": long_ko_answer}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": long_pp_answer}]

    captured = {}
    def respond(prompt):
        captured["payload"] = _payload(prompt)
        return _FakeMessage(json.dumps({"matches": []}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    cs.match_ko_to_past_papers(ko, pp)

    sent_pp = captured["payload"]["exam_questions"][0]["answer"]
    sent_ko = captured["payload"]["ko_points"][0]["ko_answer"]
    # Truncated (with room for the ellipsis) and the full rubric never sent.
    assert len(sent_pp) <= cap + 1
    assert len(sent_ko) <= cap + 1
    assert sent_pp.endswith("…") and sent_ko.endswith("…")
    assert long_pp_answer not in json.dumps(captured["payload"])
    assert long_ko_answer not in json.dumps(captured["payload"])


def test_short_answers_not_truncated_in_payload(monkeypatch):
    """Answers within the cap pass through unchanged — no spurious ellipsis."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water movement"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "movement of water"}]

    captured = {}
    def respond(prompt):
        captured["payload"] = _payload(prompt)
        return _FakeMessage(json.dumps({"matches": []}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    cs.match_ko_to_past_papers(ko, pp)
    assert captured["payload"]["exam_questions"][0]["answer"] == "movement of water"
    assert captured["payload"]["ko_points"][0]["ko_answer"] == "water movement"


def test_parses_json_with_prose_preamble(monkeypatch):
    """Some models ignore 'return ONLY JSON' and add a preamble. The matcher must
    still extract the JSON object rather than failing json.loads at char 0
    (the real-world cause of a whole reblend silently returning 0 matches)."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "water"}]
    body = json.dumps({"matches": [{"ko_question_id": 1, "past_paper_question_id": 2}]})

    def respond(prompt):
        return _FakeMessage("Sure! Here are the matches you asked for:\n\n" + body)
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert matches == [{"ko_question_id": 1, "past_paper_question_id": 2}]


def test_parses_json_from_non_first_content_block(monkeypatch):
    """If the model emits a non-text (e.g. thinking) block first, the JSON lives
    in a later block. The matcher must scan all text blocks, not just content[0]."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "water"}]
    body = json.dumps({"matches": [{"ko_question_id": 1, "past_paper_question_id": 2}]})

    def respond(prompt):
        msg = _FakeMessage(body)
        # leading block has no `.text` (mimics a thinking block); JSON is in [1].
        msg.content = [types.SimpleNamespace(thinking="reasoning…"),
                       types.SimpleNamespace(text=body)]
        return msg
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert matches == [{"ko_question_id": 1, "past_paper_question_id": 2}]


def test_unparseable_response_logs_raw_preview(monkeypatch, caplog):
    """When a chunk truly can't be parsed, the warning must carry the raw response
    preview + stop_reason so the cause is diagnosable from logs alone."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define osmosis", "answer_text": "water"}]
    pp = [{"id": 2, "question_text": "What is osmosis?", "answer_text": "water"}]

    def respond(prompt):
        return _FakeMessage("I cannot help with that request.", stop_reason="end_turn")
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(respond))

    with caplog.at_level(logging.WARNING, logger="backend.services.claude_service"):
        matches, _ = cs.match_ko_to_past_papers(ko, pp)
    assert matches == []
    warning = " ".join(r.message for r in caplog.records)
    assert "raw_preview" in warning and "I cannot help" in warning


def test_empty_shortlist_logs_and_makes_no_api_call(monkeypatch, caplog):
    """When BM25 finds no lexical candidates for any KO point, the matcher must
    log it and skip the client entirely (no API call, no cost)."""
    _stub_settings(monkeypatch)
    ko = [{"id": 1, "question_text": "Define photosynthesis", "answer_text": ""}]
    pp = [{"id": 2, "question_text": "Calculate the momentum of a trolley", "answer_text": "kg m/s"}]

    def boom():
        raise AssertionError("client must not be built when the shortlist is empty")
    monkeypatch.setattr(cs, "get_client", boom)

    with caplog.at_level(logging.INFO, logger="backend.services.claude_service"):
        matches, usage = cs.match_ko_to_past_papers(ko, pp)

    assert matches == []
    assert usage["cost_usd"] == 0.0
    assert any("shortlist" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]


def test_empty_inputs_make_no_api_call(monkeypatch):
    _stub_settings(monkeypatch)
    def boom():
        raise AssertionError("client should not be built for empty inputs")
    monkeypatch.setattr(cs, "get_client", boom)
    assert cs.match_ko_to_past_papers([], [{"id": 1, "question_text": "x"}]) == (
        [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": None})


def _payload(prompt: str) -> dict:
    """Extract the embedded {exam_questions, ko_points} JSON object from the
    formatted prompt by brace-matching from its first '{'."""
    start = prompt.index("{")
    depth = 0
    for i in range(start, len(prompt)):
        if prompt[i] == "{":
            depth += 1
        elif prompt[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(prompt[start:i + 1])
    raise ValueError("no JSON object found in prompt")
