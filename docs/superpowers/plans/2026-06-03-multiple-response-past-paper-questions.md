# Multiple-Response Past-Paper Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Dispatch all sub-agents on the `sonnet` model.**

**Goal:** Detect "tick N boxes" multiple-response past-paper questions, structure them (stem + options + correct set), present them as a proper checkbox question in the quiz with objective auto-marking, and hide the Multiple Choice mode when Past Paper is the sole quiz source.

**Architecture:** A single standalone AI detector (one batched Haiku call per paper) structures already-extracted past-paper questions; it runs automatically after upload and on demand via a Re-detect button. Structured data lives in a new nullable `questions.options_json` column. The quiz renders any question carrying that data as an intrinsic `multi_response` format, marked by exact set comparison on the backend.

**Tech Stack:** FastAPI + SQLite (backend), Alpine.js v3 (frontend, no build step), pytest (tests), Anthropic SDK (Claude Haiku).

---

## Spec

See `docs/superpowers/specs/2026-06-03-multiple-response-past-paper-questions-design.md`.

## File Structure

- **Create** `backend/prompts/multiple_response_detection.py` — the detection prompt constant.
- **Create** `backend/services/multi_response_service.py` — DB-facing detect-and-store + apply logic.
- **Create** `tests/test_multi_response_service.py` — service + apply unit tests.
- **Modify** `backend/database.py` — add `options_json` column via `_add_column_if_missing`.
- **Modify** `backend/services/claude_service.py` — `detect_multiple_response_batch()`, model constant, AI-setting defaults.
- **Modify** `backend/routers/admin.py` — register the new AI settings in `_AI_SETTING_METADATA`.
- **Modify** `backend/routers/upload.py` — call detection in the past-paper post-processing block.
- **Modify** `backend/routers/past_papers.py` — `POST /{batch_id}/detect-multi-response` endpoint.
- **Modify** `backend/routers/quiz.py` — strip correctness for client; `multi_response` marking branch.
- **Modify** `frontend/pages/quiz.html` — `multi_response` rendering + state; `availableModes()` mode-hiding.
- **Modify** `frontend/pages/past-papers.html` — Re-detect button.
- **Modify** `tests/test_past_papers.py` — re-detect endpoint tests.
- **Modify** `tests/test_quiz.py` — multi_response marking + strip tests.

## Data Contracts (used across tasks)

**`questions.options_json`** (DB column, JSON string or NULL):
```json
{ "select_count": 2,
  "options": [ {"text": "...", "is_correct": false}, {"text": "...", "is_correct": true} ] }
```

**`detect_multiple_response_batch(questions, subject)`** returns `(results, usage)` where `results` is a list aligned 1:1 with input `questions`. Each element is either `None` or:
```python
{"select_count": 2, "stem": "…", "options": [{"text": "…", "is_correct": True}, …]}
```

**Client-facing question** (after stripping, in quiz start/resume): the raw `options_json` key is removed and replaced with:
```python
q["multi_response"] = {"select_count": 2, "options": [{"text": "…"}, …]}   # is_correct removed
```
Normal questions have `q["multi_response"] = None` (or key absent).

**`/answer` request** for `quiz_format == "multi_response"`: `student_answer` is `JSON.stringify([...ticked option texts])`.
**`/answer` response** for multi_response additionally carries `"correct_options": [sorted correct texts]`.

---

### Task 1: Add `options_json` column

**Files:**
- Modify: `backend/database.py` (the `_add_column_if_missing` list near `init_db()`)
- Test: `tests/test_database.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py` (create the file if it does not exist):

```python
"""Schema migration tests."""


def test_questions_has_options_json_column(db_conn):
    cols = [r["name"] for r in db_conn.execute("PRAGMA table_info(questions)").fetchall()]
    assert "options_json" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py::test_questions_has_options_json_column -q`
Expected: FAIL — `options_json` not in column list.

- [ ] **Step 3: Add the column**

In `backend/database.py`, find the list of `_add_column_if_missing(...)` / `ALTER TABLE questions ADD COLUMN ...` statements (around lines 311-314, where `question_source`, `question_ref` are added) and append:

```python
        # Structured options for multiple-response ("tick N boxes") past-paper
        # questions. JSON: {"select_count": int, "options":[{"text","is_correct"}]}.
        # NULL for ordinary questions.
        "ALTER TABLE questions ADD COLUMN options_json TEXT DEFAULT NULL",
```

Match the exact surrounding style (these are entries in a list iterated by the migration helper — add it as a sibling entry, not a bare statement).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_database.py::test_questions_has_options_json_column -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_database.py
git commit -m "feat: add questions.options_json column for multiple-response questions"
```

---

### Task 2: Detection prompt + `detect_multiple_response_batch`

**Files:**
- Create: `backend/prompts/multiple_response_detection.py`
- Modify: `backend/services/claude_service.py`
- Test: `tests/test_claude_service_multi_response.py`

- [ ] **Step 1: Create the prompt**

Create `backend/prompts/multiple_response_detection.py`:

```python
MULTIPLE_RESPONSE_DETECTION_PROMPT = """You are reviewing extracted {subject} GCSE/A-Level exam questions.

Some exam questions present a list of candidate statements and ask the student to
tick a FIXED NUMBER of boxes — e.g. "Which two sentences describe X? Tick two boxes."
followed by several candidate sentences. Your job is to identify ONLY those
fixed-count multiple-response questions and structure them.

You are given a JSON array of questions, each with an id, the full question text
(stem and options may be run together), and the mark-scheme answer text.

For EACH input question return one result object. Return results in the SAME ORDER
as the input. Use this exact structure:

{{
  "results": [
    {{
      "question_id": 12,
      "is_multiple_response": true,
      "stem": "The question/instruction WITHOUT the option sentences",
      "select_count": 2,
      "options": [
        {{"text": "First candidate statement, verbatim", "is_correct": false}},
        {{"text": "Second candidate statement, verbatim", "is_correct": true}}
      ]
    }},
    {{
      "question_id": 13,
      "is_multiple_response": false
    }}
  ]
}}

Rules:
- A question is multiple-response ONLY if it lists candidate statements/options AND
  asks for a fixed number of ticks (two boxes, three boxes, etc.). The count may be
  written as a word ("two") or digit ("2").
- stem: the instruction text only (e.g. "Which two sentences describe malignant
  tumours?"). Remove the option sentences from it. Keep any lead-in context sentence.
- options[].text: copy each candidate statement VERBATIM. Do not paraphrase.
- options[].is_correct: use the mark-scheme answer text to decide which options are
  correct. If the answer text is missing or unclear, use your {subject} knowledge.
- select_count must equal the number of options marked is_correct=true, and must be
  the number the question asks the student to tick.
- For anything that is NOT a fixed-count multiple-response question (ordinary
  short-answer, calculation, extended writing, single-best-answer with one blank,
  etc.) return {{"question_id": <id>, "is_multiple_response": false}}.
- Return ONLY valid JSON, no markdown code fences or other text.

Questions:
{questions_json}"""
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_claude_service_multi_response.py`:

```python
"""Unit tests for detect_multiple_response_batch (Claude call mocked)."""
import json
import types
import backend.services.claude_service as cs


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_claude_service_multi_response.py -q`
Expected: FAIL — `detect_multiple_response_batch` does not exist.

- [ ] **Step 4: Implement the function and register settings**

In `backend/services/claude_service.py`:

(a) Add the import near the other prompt imports (after line 15):
```python
from backend.prompts.multiple_response_detection import MULTIPLE_RESPONSE_DETECTION_PROMPT
```

(b) Add a model constant near the other model constants (after `HANDWRITTEN_QA_MODEL`, ~line 25):
```python
MULTI_RESPONSE_MODEL   = "claude-haiku-4-5"    # Text-only — structuring tick-box questions
```

(c) In `AI_SETTING_DEFAULTS`, add a model entry (with the other models) and a prompt entry (with the other prompts):
```python
    "ai_model_multi_response":      MULTI_RESPONSE_MODEL,
```
```python
    "ai_prompt_multi_response":     MULTIPLE_RESPONSE_DETECTION_PROMPT,
```

(d) Add the function (place it just after `generate_mcq_distractors`):
```python
def detect_multiple_response_batch(questions: list[dict], subject: str) -> tuple[list, dict]:
    """Identify and structure 'tick N boxes' multiple-response questions in a batch.

    Returns (results, usage) where results is aligned 1:1 with `questions`:
    each element is None (ordinary question) or a dict
    {"select_count": int, "stem": str, "options": [{"text": str, "is_correct": bool}]}.
    Invalid/degenerate detections (fewer than 2 options, no correct option, or a
    select_count out of range) are normalised to None.
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_multi_response")

    questions_for_prompt = [
        {"id": q["id"], "question_text": q["question_text"], "answer_text": q.get("answer_text", "")}
        for q in questions
    ]
    prompt = _get_ai_setting("ai_prompt_multi_response").format(
        subject=subject,
        questions_json=json.dumps(questions_for_prompt, indent=2),
    )

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = json.loads(_strip_fences(message.content[0].text))
    by_id = {r.get("question_id"): r for r in raw.get("results", [])}

    results: list = []
    for q in questions:
        r = by_id.get(q["id"])
        results.append(_normalise_multi_response(r))
    return results, _calc_usage(message, model)


def _normalise_multi_response(r: dict | None) -> dict | None:
    """Validate one detector result; return a clean dict or None if not valid."""
    if not r or not r.get("is_multiple_response"):
        return None
    options = [
        {"text": str(o["text"]), "is_correct": bool(o.get("is_correct"))}
        for o in r.get("options", [])
        if isinstance(o, dict) and o.get("text")
    ]
    if len(options) < 2:
        return None
    n_correct = sum(1 for o in options if o["is_correct"])
    if n_correct < 1:
        return None
    select_count = r.get("select_count") or n_correct
    if not (1 <= select_count <= len(options)):
        return None
    stem = (r.get("stem") or "").strip()
    if not stem:
        return None
    return {"select_count": select_count, "stem": stem, "options": options}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_claude_service_multi_response.py -q`
Expected: PASS.

- [ ] **Step 6: Register admin metadata**

In `backend/routers/admin.py`, inside `_AI_SETTING_METADATA` (near the `ai_model_mcq` / `ai_prompt_mcq` entries, ~lines 371-377), add:
```python
    "ai_model_multi_response":         {"label": "Model",  "type": "model",  "group": "Multiple-Response Detection",            "group_key": "multi_response"},
    "ai_prompt_multi_response":        {"label": "Prompt", "type": "prompt", "group": "Multiple-Response Detection",            "group_key": "multi_response"},
```

- [ ] **Step 7: Run admin tests to confirm nothing broke**

Run: `pytest tests/test_admin.py -q`
Expected: PASS (all existing admin tests still pass; new settings are exposed).

- [ ] **Step 8: Commit**

```bash
git add backend/prompts/multiple_response_detection.py backend/services/claude_service.py backend/routers/admin.py tests/test_claude_service_multi_response.py
git commit -m "feat: detect_multiple_response_batch + admin-configurable prompt/model"
```

---

### Task 3: `multi_response_service` — detect and store

**Files:**
- Create: `backend/services/multi_response_service.py`
- Test: `tests/test_multi_response_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_multi_response_service.py`:

```python
"""Tests for the multi-response detect-and-store service."""
import json
import backend.services.multi_response_service as mrs


def _stub_detect(monkeypatch, results):
    """Stub detect_multiple_response_batch to return `results` aligned to input."""
    def _fn(questions, subject):
        return results, {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}
    monkeypatch.setattr(mrs, "detect_multiple_response_batch", _fn)


def test_detect_and_store_structures_past_paper_questions(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Which two? a b c", answer_text="a; b")
    db_conn.execute("UPDATE questions SET question_source = 'past_paper' WHERE id = ?", (q1,))
    db_conn.commit()

    _stub_detect(monkeypatch, [
        {"select_count": 2, "stem": "Which two?",
         "options": [{"text": "a", "is_correct": True},
                     {"text": "b", "is_correct": True},
                     {"text": "c", "is_correct": False}]},
    ])

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 1

    row = db_conn.execute("SELECT question_text, options_json FROM questions WHERE id = ?", (q1,)).fetchone()
    assert row["question_text"] == "Which two?"
    data = json.loads(row["options_json"])
    assert data["select_count"] == 2
    assert {o["text"] for o in data["options"] if o["is_correct"]} == {"a", "b"}


def test_detect_and_store_leaves_normal_questions_untouched(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Define osmosis.", answer_text="...")
    db_conn.execute("UPDATE questions SET question_source = 'past_paper' WHERE id = ?", (q1,))
    db_conn.commit()

    _stub_detect(monkeypatch, [None])

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 0
    row = db_conn.execute("SELECT question_text, options_json FROM questions WHERE id = ?", (q1,)).fetchone()
    assert row["question_text"] == "Define osmosis."
    assert row["options_json"] is None


def test_detect_and_store_ignores_ai_generated_questions(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, question_text="KO question", answer_text="...")  # default ai_generated

    called = {"n": 0}
    def _fn(questions, subject):
        called["n"] += 1
        return [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    monkeypatch.setattr(mrs, "detect_multiple_response_batch", _fn)

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 0
    assert called["n"] == 0  # no past-paper questions → no AI call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_multi_response_service.py -q`
Expected: FAIL — module `multi_response_service` does not exist.

- [ ] **Step 3: Implement the service**

Create `backend/services/multi_response_service.py`:

```python
"""Detect and store structured multiple-response ('tick N boxes') questions.

Operates on already-extracted PAST-PAPER questions for a batch. Used by:
- the upload post-processing step (automatic, after extraction + mark scheme), and
- the Past Papers 'Re-detect' endpoint (on demand for existing papers).
"""
import json
import sqlite3

from backend.services.claude_service import detect_multiple_response_batch


def detect_and_store_multi_response(
    batch_id: int, subject: str, user_id: int, db: sqlite3.Connection
) -> int:
    """Run detection over a batch's past-paper questions; persist structured ones.

    Returns the number of questions updated. Makes no AI call when the batch has
    no past-paper questions. Records cost in api_usage.
    """
    rows = db.execute(
        """SELECT id, question_text, answer_text FROM questions
           WHERE batch_id = ? AND user_id = ? AND question_source = 'past_paper'""",
        (batch_id, user_id),
    ).fetchall()
    questions = [dict(r) for r in rows]
    if not questions:
        return 0

    try:
        results, usage = detect_multiple_response_batch(questions, subject)
    except Exception as e:  # non-fatal: detection is an enhancement
        print(f"[multi_response] detection failed for batch {batch_id}: {e}")
        return 0

    db.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, 'multi_response_detection', ?, ?, ?)""",
        (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
    )

    updated = 0
    for q, result in zip(questions, results):
        if not result:
            continue
        db.execute(
            "UPDATE questions SET question_text = ?, options_json = ?, updated_at = datetime('now') WHERE id = ?",
            (result["stem"], json.dumps({
                "select_count": result["select_count"],
                "options": result["options"],
            }), q["id"]),
        )
        updated += 1
    db.commit()
    return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_multi_response_service.py -q`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add backend/services/multi_response_service.py tests/test_multi_response_service.py
git commit -m "feat: multi_response_service detect-and-store"
```

---

### Task 4: Wire detection into upload post-processing

**Files:**
- Modify: `backend/routers/upload.py` (past-paper post-processing block, ~lines 402-416)

- [ ] **Step 1: Add the import**

Near the top imports of `backend/routers/upload.py` (with the other service imports), add:
```python
from backend.services.multi_response_service import detect_and_store_multi_response
```

- [ ] **Step 2: Call detection after mark-scheme application**

In `process_batch`, in the `if batch_type == "past_paper":` post-processing block, AFTER the mark-scheme steps (after the `if ms_pdf_path ...` block, still inside the `past_paper` branch, ~line 416), add:
```python
            # 3) Structure multiple-response ("tick N boxes") questions now that
            #    mark-scheme answers (which mark correctness) have been applied.
            try:
                n = detect_and_store_multi_response(batch_id, subject_name, user_id, db)
                if n:
                    print(f"[multi_response] structured {n} question(s) for batch {batch_id}")
            except Exception as e:
                print(f"[multi_response] step failed (non-fatal): {e}")
```

- [ ] **Step 3: Verify the existing upload test suite still passes**

Run: `pytest tests/test_upload.py -q`
Expected: PASS (detection is guarded and only runs for past-paper batches; tests that don't process real past papers are unaffected). If `tests/test_upload.py` does not exist, run `pytest -q -k upload` instead and expect PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/upload.py
git commit -m "feat: run multiple-response detection after past-paper upload"
```

---

### Task 5: Re-detect endpoint

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_past_papers.py` (it already imports `backend.routers.past_papers as past_papers` and has `_make_past_paper` / `_set_source` helpers; if a needed helper is absent, insert a question with `question_source='past_paper'` directly via `db_conn`):

```python
def test_detect_multi_response_endpoint_updates_questions(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn, monkeypatch
):
    import backend.services.multi_response_service as mrs
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Which two? a b c", answer_text="a; b")
    db_conn.execute("UPDATE questions SET question_source='past_paper' WHERE id=?", (q1,))
    db_conn.commit()

    monkeypatch.setattr(
        mrs, "detect_multiple_response_batch",
        lambda questions, subject: (
            [{"select_count": 2, "stem": "Which two?",
              "options": [{"text": "a", "is_correct": True},
                          {"text": "b", "is_correct": True},
                          {"text": "c", "is_correct": False}]}],
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        ),
    )

    r = client.post(f"/api/past-papers/{bid}/detect-multi-response", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert body["scanned"] == 1


def test_detect_multi_response_rejects_other_users_batch(
    client, regular_user, second_user, make_subject, make_batch, make_question, db_conn
):
    owner_id, _ = second_user
    sid = make_subject()
    bid = make_batch(owner_id, sid)
    q1 = make_question(bid, owner_id, sid)
    db_conn.execute("UPDATE questions SET question_source='past_paper' WHERE id=?", (q1,))
    db_conn.commit()

    _, token1 = regular_user
    r = client.post(f"/api/past-papers/{bid}/detect-multi-response",
                    headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_past_papers.py -k detect_multi_response -q`
Expected: FAIL — endpoint returns 404 for the valid case (route not defined).

- [ ] **Step 3: Implement the endpoint**

In `backend/routers/past_papers.py`, add the import near the top:
```python
from backend.services.multi_response_service import detect_and_store_multi_response
```

Add the endpoint (place after the existing recrop/image endpoints):
```python
@router.post("/{batch_id}/detect-multi-response")
def detect_multi_response(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Re-scan an existing past-paper batch for 'tick N boxes' multiple-response
    questions and store their structured form. Returns counts."""
    batch = db.execute(
        "SELECT b.id, s.name as subject_name FROM upload_batches b "
        "LEFT JOIN subjects s ON s.id = b.subject_id "
        "WHERE b.id = ? AND b.user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Past paper not found")

    scanned = db.execute(
        "SELECT COUNT(*) AS c FROM questions "
        "WHERE batch_id = ? AND user_id = ? AND question_source = 'past_paper'",
        (batch_id, user["id"]),
    ).fetchone()["c"]

    updated = detect_and_store_multi_response(
        batch_id, batch["subject_name"] or "General", user["id"], db
    )
    return {"updated": updated, "scanned": scanned}
```

> Confirmed: `upload_batches` has a `subject_id` column, so the join above is correct.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_past_papers.py -k detect_multi_response -q`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full past-papers suite**

Run: `pytest tests/test_past_papers.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: POST /past-papers/{batch_id}/detect-multi-response re-detect endpoint"
```

---

### Task 6: Backend quiz marking + client strip

**Files:**
- Modify: `backend/routers/quiz.py`
- Test: `tests/test_quiz.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_quiz.py`:

```python
import json as _json


def _make_session_with_question(db_conn, uid, qid):
    cur = db_conn.execute(
        """INSERT INTO quiz_sessions
           (user_id, quiz_mode, total_questions, current_index)
           VALUES (?, 'mixed', 1, 0)""",
        (uid,),
    )
    db_conn.commit()
    return cur.lastrowid


def _add_multi_response(db_conn, qid, select_count, options):
    db_conn.execute(
        "UPDATE questions SET options_json = ? WHERE id = ?",
        (_json.dumps({"select_count": select_count, "options": options}), qid),
    )
    db_conn.commit()


def test_multi_response_exact_match_is_correct(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a", "b"]),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["is_correct"] is True
    assert sorted(body["correct_options"]) == ["a", "b"]


def test_multi_response_subset_is_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a"]),
    })
    assert r.json()["is_correct"] is False


def test_multi_response_superset_is_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a", "b", "c"]),
    })
    assert r.json()["is_correct"] is False


def test_quiz_start_strips_option_correctness(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": False},
    ])

    r = client.post("/api/quiz/start", json={"subject_id": sid}, headers=user_headers)
    assert r.status_code == 200
    q = next(x for x in r.json()["questions"] if x["id"] == qid)
    assert q["multi_response"]["select_count"] == 2
    assert all("is_correct" not in o for o in q["multi_response"]["options"])
    assert "options_json" not in q
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_quiz.py -k multi_response -q`
Expected: FAIL — marking branch and strip helper not implemented.

- [ ] **Step 3: Add the strip helper and apply it in start/resume**

In `backend/routers/quiz.py`, add a helper near the top (after imports):
```python
def _prepare_questions_for_client(questions: list[dict]) -> list[dict]:
    """Replace raw options_json with a client-safe `multi_response` payload that
    omits is_correct, so the browser never receives the correct set."""
    for q in questions:
        raw = q.pop("options_json", None)
        if raw:
            try:
                data = json.loads(raw)
                q["multi_response"] = {
                    "select_count": data.get("select_count"),
                    "options": [{"text": o["text"]} for o in data.get("options", [])],
                }
            except Exception:
                q["multi_response"] = None
        else:
            q["multi_response"] = None
    return questions
```

In `start_quiz`, after the MCQ-options loop builds `selected` and BEFORE the session is inserted/returned, add:
```python
    selected = _prepare_questions_for_client(selected)
```
(`selected` is then stored in `questions_json` and returned — both now stripped.)

In `resume_quiz`, after `questions = json.loads(...)`, add:
```python
    questions = _prepare_questions_for_client(questions)
```

- [ ] **Step 4: Add the marking branch**

In `submit_answer`, add a new branch alongside the existing `mcq` / `typed` branches. Also declare `correct_options = None` near the top of the function (with `is_correct = None` etc.), and include it in the final return dict.

```python
    elif req.quiz_format == "multi_response":
        raw = question["options_json"]
        correct_set: set[str] = set()
        if raw:
            try:
                data = json.loads(raw)
                correct_set = {o["text"] for o in data.get("options", []) if o.get("is_correct")}
            except Exception:
                correct_set = set()
        try:
            chosen = set(json.loads(req.student_answer or "[]"))
        except Exception:
            chosen = set()
        is_correct = 1 if (correct_set and chosen == correct_set) else 0
        quality = 4 if is_correct else 1
        correct_options = sorted(correct_set)
```

Update the final return statement of `submit_answer` to include the field:
```python
    return {
        "is_correct": bool(is_correct),
        "correct_answer": question["answer_text"],
        "feedback": ai_feedback,
        "quality": quality,
        "correct_options": correct_options,
    }
```

(Define `correct_options = None` where `is_correct`, `ai_feedback`, `quality` are initialised, so the non-multi_response paths still return the key.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_quiz.py -k multi_response -q`
Expected: PASS (all four tests).

- [ ] **Step 6: Run the full quiz suite**

Run: `pytest tests/test_quiz.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/quiz.py tests/test_quiz.py
git commit -m "feat: multi_response quiz marking + strip option correctness for client"
```

---

### Task 7: Frontend — multi_response rendering

**Files:**
- Modify: `frontend/pages/quiz.html`

> Frontend has no build step and no JS unit harness; verify with the Preview MCP (dev server proxied on port 8001). The dev server must be restarted to pick up the Task 6 backend change before verifying.

- [ ] **Step 1: Add `selectedOptions` state + reset**

In the `x-data` object, add to the quiz-state block (near `selectedOption: null,`):
```javascript
    selectedOptions: [],
```
In `resetCard()`, add:
```javascript
        this.selectedOptions = [];
```

- [ ] **Step 2: Add the `toggleOption` helper**

Add a method to the `x-data` object (e.g. after `submitTyped()`):
```javascript
    toggleOption(text) {
        const q = this.currentQuestion;
        const max = q?.multi_response?.select_count || 1;
        if (this.selectedOptions.includes(text)) {
            this.selectedOptions = this.selectedOptions.filter(t => t !== text);
        } else if (this.selectedOptions.length < max) {
            this.selectedOptions = [...this.selectedOptions, text];
        }
    },

    async submitMultiResponse() {
        if (!this.selectedOptions.length) return;
        await this.submitAnswer('multi_response', JSON.stringify(this.selectedOptions), null);
    },
```

- [ ] **Step 3: Guard `currentFormat`**

In the `get currentFormat()` getter, add at the very top (before the existing `pool` logic):
```javascript
        const cq = this.currentQuestion;
        if (cq && cq.multi_response) return 'multi_response';
```

- [ ] **Step 4: Add the multi_response UI block**

In the quiz card, after the TYPED FORMAT block and before the RESULT block, add:
```html
            <!-- MULTI-RESPONSE FORMAT -->
            <div x-show="currentFormat === 'multi_response' && !result" class="space-y-2">
                <p class="text-sm text-gray-500 mb-1">
                    Select <span class="font-semibold" x-text="currentQuestion?.multi_response?.select_count"></span>
                    <span x-text="(currentQuestion?.multi_response?.select_count === 1) ? 'answer' : 'answers'"></span>
                    <span class="text-gray-400"
                          x-text="'(' + selectedOptions.length + '/' + (currentQuestion?.multi_response?.select_count || 0) + ' selected)'"></span>
                </p>
                <template x-for="(opt, idx) in (currentQuestion?.multi_response?.options || [])" :key="idx">
                    <button @click="toggleOption(opt.text)"
                            :class="selectedOptions.includes(opt.text) ? 'border-indigo-600 bg-indigo-50' : 'border-gray-200 hover:border-gray-300'"
                            class="w-full text-left p-3 rounded-lg border-2 text-sm transition flex items-start gap-2">
                        <span class="mt-0.5 w-4 h-4 shrink-0 rounded border flex items-center justify-center"
                              :class="selectedOptions.includes(opt.text) ? 'bg-indigo-600 border-indigo-600' : 'border-gray-300'">
                            <svg x-show="selectedOptions.includes(opt.text)" class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
                            </svg>
                        </span>
                        <span x-text="opt.text"></span>
                    </button>
                </template>
                <div class="flex gap-2 mt-3">
                    <button @click="submitMultiResponse()" :disabled="!selectedOptions.length || submitting"
                            class="flex-1 bg-indigo-600 text-white py-2.5 rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 transition">
                        <span x-show="!submitting">Submit</span>
                        <span x-show="submitting">Checking...</span>
                    </button>
                    <button @click="skipQuestion()" :disabled="submitting"
                            class="px-4 py-2.5 rounded-lg text-sm font-medium text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition disabled:opacity-50">
                        Skip
                    </button>
                </div>
            </div>
```

- [ ] **Step 5: Show correct/incorrect options in the result**

The existing RESULT block has `x-show="result && currentFormat !== 'flashcard'"`. multi_response is not flashcard, so it already renders the Correct/Incorrect banner and Next button. Add, inside that RESULT block (after the banner div, before the Next button), a per-option recap:
```html
                <div x-show="currentFormat === 'multi_response'" class="space-y-1.5">
                    <template x-for="(opt, idx) in (currentQuestion?.multi_response?.options || [])" :key="idx">
                        <div class="flex items-start gap-2 text-sm p-2 rounded-lg"
                             :class="(result?.correct_options || []).includes(opt.text)
                                 ? 'bg-green-50 text-green-800'
                                 : (selectedOptions.includes(opt.text) ? 'bg-red-50 text-red-700' : 'text-gray-500')">
                            <span x-text="(result?.correct_options || []).includes(opt.text) ? '✓' : (selectedOptions.includes(opt.text) ? '✕' : '·')"></span>
                            <span x-text="opt.text"></span>
                        </div>
                    </template>
                </div>
```

- [ ] **Step 6: Verify in the browser (Preview MCP)**

Restart the dev server (`python run.py`) so the backend strip/marking is live. Then, using the Preview MCP on port 8001:
1. Ensure a past-paper batch has at least one structured multi_response question (run Task 9's Re-detect button, or temporarily set `options_json` on a known question via SQL).
2. Start a Past Paper quiz; navigate to the multi_response question.
3. Confirm: stem shows without the option sentences; checkboxes render; ticking is capped at `select_count`; Submit disabled at 0 ticks.
4. Submit a correct set → "Correct!" and green ticks; submit a wrong set → "Incorrect" with the correct options highlighted green and wrong picks red.
5. Confirm the network response for `/answer` includes `correct_options` and the question payload from `/quiz/start` has NO `is_correct` in its options.

- [ ] **Step 7: Commit**

```bash
git add frontend/pages/quiz.html
git commit -m "feat: render and mark multi_response questions in the quiz"
```

---

### Task 8: Frontend — hide Multiple Choice for Past Paper source

**Files:**
- Modify: `frontend/pages/quiz.html`

- [ ] **Step 1: Add `availableModes()` and purge logic**

Add a method to the `x-data` object:
```javascript
    availableModes() {
        const all = [
            {id: 'flashcard', label: 'Flashcard'},
            {id: 'mcq',       label: 'Multiple Choice'},
            {id: 'typed',     label: 'Type Answer'},
        ];
        // Past papers never have AI MCQ distractors — drop the MCQ mode when the
        // user has restricted the quiz to Past Paper questions only.
        const pastPaperOnly = this.questionSources.length === 1 && this.questionSources[0] === 'past_paper';
        return pastPaperOnly ? all.filter(m => m.id !== 'mcq') : all;
    },
```

In `init()`, alongside the existing `this.$watch('questionSources', ...)`, add a watcher that purges a stale MCQ selection:
```javascript
        this.$watch('questionSources', () => {
            if (this.questionSources.length === 1 && this.questionSources[0] === 'past_paper') {
                this.selectedModes = this.selectedModes.filter(m => m !== 'mcq');
            }
        });
```

- [ ] **Step 2: Point the Mode buttons at `availableModes()`**

Change the Mode buttons `x-for` (currently the inline literal `[{id:'flashcard',...},{id:'mcq',...},{id:'typed',...}]`) to:
```html
                        <template x-for="m in availableModes()" :key="m.id">
```
Leave the button markup inside unchanged. Also change the grid wrapper from `grid-cols-3` to a responsive class so 2 buttons still look right:
```html
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
```

- [ ] **Step 3: Verify in the browser (Preview MCP)**

On the Start a Quiz page (port 8001):
1. Select Question Source → **Past Paper** only. Confirm the Mode row shows only **Flashcard** and **Type Answer** (no Multiple Choice).
2. Select **Knowledge Organiser** only, or both → confirm all three modes reappear.
3. Select Multiple Choice, then switch source to Past Paper only → confirm MCQ is deselected (not lurking in `selectedModes`).

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/quiz.html
git commit -m "feat: hide Multiple Choice mode when Past Paper is the only quiz source"
```

---

### Task 9: Frontend — Re-detect button on Past Papers page

**Files:**
- Modify: `frontend/pages/past-papers.html`

> Reminder (from CLAUDE.md): `API` has `baseUrl: '/api'`, so call `API.post('/past-papers/...')` — NOT `/api/past-papers/...`.

- [ ] **Step 1: Add the action method**

In the page's `x-data` object, add a method (follow the existing method style in this file):
```javascript
    async redetectMultiResponse(paper) {
        if (this._redetecting) return;
        this._redetecting = true;
        try {
            const res = await API.post(`/past-papers/${paper.id}/detect-multi-response`, {});
            $store.app.showToast(`Re-detected: ${res.updated} of ${res.scanned} question(s) structured`, 'success');
            // Refresh the currently shown paper's questions
            if (this.subjectId) {
                this.papers = await API.get(`/past-papers?subject_id=${this.subjectId}`);
            }
        } catch (e) {
            $store.app.showToast(e.message || 'Re-detect failed', 'error');
        } finally {
            this._redetecting = false;
        }
    },
```

> Confirmed: the past-papers list endpoint returns each paper with an `id` field (`SELECT b.id, ...`), so use `paper.id` (NOT `paper.batch_id`). Match the existing toast/refresh patterns already present in the file.

- [ ] **Step 2: Add the button**

In the per-paper header/action area (next to the existing delete/tag controls for a paper), add:
```html
                        <button @click="redetectMultiResponse(paper)" :disabled="_redetecting"
                                class="px-2.5 py-1.5 rounded-lg text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 transition disabled:opacity-50"
                                title="Re-scan this paper for tick-box (multiple-response) questions">
                            Re-detect tick-box Qs
                        </button>
```
Place it consistently with the existing controls' layout (match indentation and the surrounding flex container).

- [ ] **Step 3: Verify in the browser (Preview MCP)**

On the Past Papers page (port 8001):
1. Click **Re-detect tick-box Qs** on a paper known to contain a "tick two boxes" question.
2. Confirm a success toast like "Re-detected: 1 of N question(s) structured".
3. Confirm via the network panel that `POST /api/past-papers/{id}/detect-multi-response` returns 200 with `{updated, scanned}` (and that it is a single `/api/...` path, not `/api/api/...`).
4. Open a quiz over that paper and confirm the question now renders as checkboxes (Task 7 behaviour).

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/past-papers.html
git commit -m "feat: Re-detect tick-box questions button on Past Papers page"
```

---

### Task 10: Full-suite verification

- [ ] **Step 1: Run the entire backend suite**

Run: `pytest tests/ --tb=short -q`
Expected: PASS except the 3 pre-existing `tests/test_costs.py` failures (`test_history_*` → 404) that fail on `main` and are unrelated to this work. No NEW failures.

- [ ] **Step 2: Manual end-to-end check (Preview MCP)**

With the dev server restarted:
1. Re-detect a past paper → structured count > 0.
2. Past-Paper-only quiz → Multiple Choice mode hidden.
3. Answer a multi_response question correctly and incorrectly → marking + highlighting correct.
4. Resume an in-progress quiz containing a multi_response question → still renders as checkboxes, options still carry no `is_correct`.

- [ ] **Step 3: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "chore: verification fixups for multiple-response questions"
```

---

## Self-Review Notes

- **Spec coverage:** storage (T1), detector (T2), apply/store service (T3), new-upload wiring (T4), re-detect endpoint+button (T5/T9), quiz rendering (T7), marking (T6), answer-leak strip (T6), mode-hiding (T8), tests (T2/T3/T5/T6) — all spec sections mapped.
- **Type consistency:** `detect_multiple_response_batch` returns per-question `None | {select_count, stem, options}`; `detect_and_store_multi_response` consumes that shape and writes `{select_count, options}` into `options_json`; client strip produces `multi_response = {select_count, options:[{text}]}`; marking reads `options_json` from the DB row (authoritative) and returns `correct_options`. Names consistent across tasks.
- **Pre-resolved facts:** `upload_batches.subject_id` exists (T5 join correct); the past-papers list returns each paper as `id` (T9 uses `paper.id`). Both confirmed against the codebase, not left to the implementer.
