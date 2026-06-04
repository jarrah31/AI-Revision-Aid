# Past-Paper Coverage View + Multi-Match Blend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show how much of each Knowledge Organiser upload is backed by real past-paper questions vs AI-generated ones, and let the Blend keep multiple genuinely-different exam questions per KO point (capped at 3) instead of discarding all but one.

**Architecture:** Two complementary parts on top of the existing Blend pipeline. **Part A (display)** adds a `past_paper_count` field to `/api/costs/history` and a new `GET /api/questions/coverage` endpoint, then surfaces both as a chip (previous-uploads list) and a per-paper breakdown banner (View Q&As page). **Part B (behaviour)** widens `match_ko_to_past_papers` to allow multiple matches per KO point and changes `_match_and_replace_with_past_papers` to replace the AI question with the first match and INSERT the extras (cap 3) as new past-paper rows in the same batch.

**Tech Stack:** FastAPI, SQLite (raw `sqlite3`), Alpine.js v3 (CDN, no build step), pytest.

---

## Conventions for this plan

- **Run tests with:** `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest <path> -q`
  (plain `pytest`/`python` are NOT on PATH — always use `venv/bin/pytest`).
- **API paths are prefixed `/api`** in tests and frontend (`client.get("/api/...")`).
  The pre-existing `tests/test_costs.py::test_history_*` failures are an unrelated WIP
  bug (those tests omit the `/api` prefix) — leave them alone.
- The frontend `API` helper has `baseUrl = '/api'`, so call `API.get('/questions/coverage?...')`
  (it becomes `/api/questions/coverage`), never `/api/...` directly.
- **No DB migration needed** — `questions.question_source`, `question_source_detail`, and
  `options_json` columns already exist.

---

## File Structure

- **Modify** `backend/routers/costs.py` — add `past_paper_count` subselect to `/history`.
- **Modify** `backend/routers/questions.py` — add `GET /coverage` endpoint (placed BEFORE
  the `/{question_id}` route so the static path wins).
- **Modify** `tests/conftest.py` — extend the `make_question` factory with optional
  `question_source` / `question_source_detail` kwargs (backward-compatible).
- **Modify** `backend/prompts/matching.py` — allow multiple matches per KO point.
- **Modify** `backend/routers/upload.py` — `_match_and_replace_with_past_papers`: group by
  KO question, cap 3, first=replace, rest=insert; widen both SELECTs.
- **Modify** `frontend/pages/upload-history.html` — coverage chip on KO rows.
- **Modify** `frontend/pages/review.html` — fetch coverage + render banner for KO batches.
- **Tests:** `tests/test_costs.py`, `tests/test_questions.py`, `tests/test_upload.py`.

---

## Task 1: `past_paper_count` in `/api/costs/history`

**Files:**
- Modify: `backend/routers/costs.py:85-89` (the history SELECT)
- Test: `tests/test_costs.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_costs.py` (reuses the `_make_subject` / `_make_batch` helpers already
in that file):

```python
def test_history_includes_past_paper_count(client, db_conn):
    """GET /api/costs/history returns past_paper_count per batch."""
    uid, token = _insert_user(db_conn, "covuser")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid, batch_type="knowledge_organiser")
    # 2 AI-generated + 1 past-paper question in this batch
    for src in ("ai_generated", "ai_generated", "past_paper"):
        db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved, question_source)
               VALUES (?, ?, ?, 1, 'q', 'a', 1, ?)""",
            (bid, uid, sid, src),
        )
    db_conn.commit()

    resp = client.get("/api/costs/history",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    batch = next(b for b in resp.json() if b["id"] == bid)
    assert batch["question_count"] == 3
    assert batch["past_paper_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_costs.py::test_history_includes_past_paper_count -q`
Expected: FAIL with `KeyError: 'past_paper_count'`.

- [ ] **Step 3: Add the subselect**

In `backend/routers/costs.py`, in the `get_cost_history` query, add a line directly after
the `approved_count` subselect (currently line 86):

```python
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as approved_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.question_source = 'past_paper') as past_paper_count,
```

(Only the `past_paper_count` line is new; the other two are shown for placement.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_costs.py::test_history_includes_past_paper_count -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/costs.py tests/test_costs.py
git commit -m "feat: past_paper_count per batch in /costs/history"
```

---

## Task 2: `GET /api/questions/coverage` endpoint

**Files:**
- Modify: `tests/conftest.py:201-217` (extend `make_question` factory)
- Modify: `backend/routers/questions.py` (add endpoint after the list endpoint, ~line 80,
  BEFORE the `/{question_id}` route at line 150)
- Test: `tests/test_questions.py`

- [ ] **Step 1: Extend the `make_question` fixture**

In `tests/conftest.py`, replace the `make_question` factory body with this
backward-compatible version (adds two optional kwargs + two columns to the INSERT):

```python
@pytest.fixture
def make_question(db_conn):
    """Returns a factory that inserts a question row."""
    def _fn(batch_id, user_id, subject_id,
            question_text="What is X?", answer_text="X is Y.",
            approved=1, page_number=1,
            question_source="ai_generated", question_source_detail=None):
        cur = db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved, question_source, question_source_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, user_id, subject_id, page_number,
             question_text, answer_text, approved,
             question_source, question_source_detail),
        )
        db_conn.commit()
        return cur.lastrowid
    return _fn
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_questions.py`:

```python
def test_coverage_counts_and_breakdown(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, question_source="ai_generated")
    make_question(bid, uid, sid, question_source="ai_generated")
    make_question(bid, uid, sid, question_source="past_paper",
                  question_source_detail="AQA 2023 Paper 1")
    make_question(bid, uid, sid, question_source="past_paper",
                  question_source_detail="AQA 2023 Paper 1")
    make_question(bid, uid, sid, question_source="past_paper",
                  question_source_detail="AQA 2022 Paper 2")

    r = client.get(f"/api/questions/coverage?batch_id={bid}", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 5
    assert data["past_paper"] == 3
    assert data["ai_generated"] == 2
    assert data["by_paper"] == [
        {"source": "AQA 2023 Paper 1", "count": 2},
        {"source": "AQA 2022 Paper 2", "count": 1},
    ]


def test_coverage_null_detail_bucketed_as_past_paper(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, question_source="past_paper",
                  question_source_detail=None)
    r = client.get(f"/api/questions/coverage?batch_id={bid}", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["by_paper"] == [{"source": "Past Paper", "count": 1}]


def test_coverage_empty_batch(
    client, user_headers, regular_user, make_subject, make_batch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)  # make_batch defaults batch_type='knowledge_organiser'
    r = client.get(f"/api/questions/coverage?batch_id={bid}", headers=user_headers)
    assert r.status_code == 200
    assert r.json() == {
        "batch_type": "knowledge_organiser",
        "total": 0, "past_paper": 0, "ai_generated": 0, "by_paper": [],
    }


def test_coverage_other_user_returns_404(
    client, regular_user, second_user, make_subject, make_batch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    _, other_token = second_user
    r = client.get(f"/api/questions/coverage?batch_id={bid}",
                   headers={"Authorization": f"Bearer {other_token}"})
    assert r.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_questions.py -k coverage -q`
Expected: FAIL — the coverage route returns 404/422 (no such endpoint yet), or matches
`/{question_id}` with `"coverage"`.

- [ ] **Step 4: Add the endpoint**

In `backend/routers/questions.py`, add this **immediately after** the `list_questions`
function (after its closing `return {...}` around line 80) and **before** the
`@router.post("/{question_id}/fact-check")` route. Placement matters: a static `/coverage`
path must be declared before the dynamic `/{question_id}` route or FastAPI will try to
parse `"coverage"` as `question_id`.

```python
@router.get("/coverage")
def question_coverage(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Past-paper vs AI-generated coverage for a single batch (user-scoped)."""
    batch = db.execute(
        "SELECT id, batch_type FROM upload_batches WHERE id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    rows = db.execute(
        "SELECT question_source, COUNT(*) AS c FROM questions "
        "WHERE batch_id = ? AND user_id = ? GROUP BY question_source",
        (batch_id, user["id"]),
    ).fetchall()
    counts = {r["question_source"]: r["c"] for r in rows}
    past_paper = counts.get("past_paper", 0)
    ai_generated = counts.get("ai_generated", 0)
    total = sum(counts.values())

    by_paper_rows = db.execute(
        "SELECT COALESCE(NULLIF(TRIM(question_source_detail), ''), 'Past Paper') AS source, "
        "       COUNT(*) AS count "
        "FROM questions "
        "WHERE batch_id = ? AND user_id = ? AND question_source = 'past_paper' "
        "GROUP BY source "
        "ORDER BY count DESC, source ASC",
        (batch_id, user["id"]),
    ).fetchall()

    return {
        "batch_type": batch["batch_type"],
        "total": total,
        "past_paper": past_paper,
        "ai_generated": ai_generated,
        "by_paper": [{"source": r["source"], "count": r["count"]} for r in by_paper_rows],
    }
```

`HTTPException` and `Depends`/`get_current_user`/`get_db`/`sqlite3` are already imported at
the top of this file — no new imports needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_questions.py -k coverage -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full questions + costs suites (regression for the fixture change)**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_questions.py tests/test_costs.py -q`
Expected: all pass except the 3 pre-existing `test_history_*` failures (unrelated `/api`
prefix bug).

- [ ] **Step 7: Commit**

```bash
git add backend/routers/questions.py tests/conftest.py tests/test_questions.py
git commit -m "feat: GET /api/questions/coverage endpoint with per-paper breakdown"
```

---

## Task 3: Allow multiple matches per KO point (matcher prompt)

**Files:**
- Modify: `backend/prompts/matching.py` (the whole `MATCHING_PROMPT` string)
- Test: `tests/test_upload.py`

`match_ko_to_past_papers` already returns `result.get("matches", [])` and does not care how
many rows share a `ko_question_id`, so only the prompt text changes here. The behavioural
cap is enforced in Task 4.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_upload.py` (top of file already does `from backend.routers import upload`;
this test imports the prompt directly):

```python
def test_matching_prompt_supports_multiple_and_formats():
    from backend.prompts.matching import MATCHING_PROMPT
    # Format-string integrity: both placeholders must survive and no stray braces.
    rendered = MATCHING_PROMPT.format(ko_list="[]", pp_list="[]")
    assert "[]" in rendered
    # Must instruct multiple matches per KO point, capped at 3.
    assert "up to 3" in MATCHING_PROMPT
    lowered = MATCHING_PROMPT.lower()
    assert "different way" in lowered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_upload.py::test_matching_prompt_supports_multiple_and_formats -q`
Expected: FAIL on `assert "up to 3" in MATCHING_PROMPT`.

- [ ] **Step 3: Replace the prompt**

Overwrite the entire contents of `backend/prompts/matching.py` with:

```python
MATCHING_PROMPT = """You are matching questions from a knowledge organiser (KO) to equivalent questions from GCSE/A-Level past exam papers.

KO Questions (AI-extracted summaries of knowledge organiser content):
{ko_list}

Past Paper Questions (verbatim from real exam papers):
{pp_list}

Task: For each KO question, find ALL past paper questions (up to 3) that test the SAME specific knowledge point.

Match criteria (ALL must be true for every match):
- The same specific fact, concept, or skill is being tested
- The past paper question is a genuine exam-quality equivalent
- The answers are consistent (they would be marked the same way)

Keeping multiple matches:
- Include more than one past paper question for the same KO point ONLY when they ask for that knowledge in a genuinely different way (different phrasing, context, or question style) — this gives the student useful reinforcement.
- Do NOT include verbatim or near-duplicate questions that merely repeat the same wording.
- Return at most 3 past paper questions per KO question.

Do NOT match based on superficial word similarity if the knowledge content differs.
Each past paper question can only be used for ONE KO question (no duplicates across KO questions).

Return ONLY valid JSON. List one object per (KO question, past paper question) pair; the same ko_question_id may appear multiple times:
{{"matches": [{{"ko_question_id": 123, "past_paper_question_id": 456}}, {{"ko_question_id": 123, "past_paper_question_id": 789}}]}}
Return an empty matches array if no genuine matches exist."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_upload.py::test_matching_prompt_supports_multiple_and_formats -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/prompts/matching.py tests/test_upload.py
git commit -m "feat: matching prompt allows up to 3 matches per KO point"
```

> **Note for the user (not a build step):** if you have overridden `ai_prompt_matching`
> in the Admin → AI Settings panel, the DB override takes precedence over this default —
> update it there too, or reset it to default, for the new behaviour to apply in prod.

---

## Task 4: Multi-match application (replace first, insert extras, cap 3)

**Files:**
- Modify: `backend/routers/upload.py:139-205` (`_match_and_replace_with_past_papers`)
- Test: `tests/test_upload.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_upload.py`. These call the function directly with a monkeypatched
matcher (mirroring the existing `test_matcher_uses_full_corpus_not_just_100`). Helper
inserts a past-paper batch with exam metadata and its questions.

```python
def _make_pp_batch_with_questions(db_conn, upload, user_id, subject_id, texts,
                                  exam_board="AQA", exam_year=2023, paper_number="Paper 1"):
    """Insert a past_paper batch (with exam metadata) + its questions. Returns [pp_ids]."""
    cur = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end, status,
            batch_type, exam_board, exam_year, paper_number)
           VALUES (?, ?, 'pp.pdf', 'pp.pdf', 1, 2, 'completed', 'past_paper', ?, ?, ?)""",
        (user_id, subject_id, exam_board, exam_year, paper_number),
    )
    pp_batch = cur.lastrowid
    pp_ids = []
    for t in texts:
        c = db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text, answer_text,
                approved, question_source)
               VALUES (?, ?, ?, 1, ?, 'ans', 1, 'past_paper')""",
            (pp_batch, user_id, subject_id, t),
        )
        pp_ids.append(c.lastrowid)
    db_conn.commit()
    return pp_ids


def _make_ko_question(db_conn, batch_id, user_id, subject_id, text="KO q"):
    c = db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source)
           VALUES (?, ?, ?, 1, ?, 'ko ans', 0, 'ai_generated')""",
        (batch_id, user_id, subject_id, text),
    )
    db_conn.commit()
    return c.lastrowid


def test_blend_keeps_multiple_matches(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(
        db_conn, upload, user_id, sid, ["pp A", "pp B", "pp C"])

    def fake_match(ko_list, pp_list):
        return [{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids]
    monkeypatch.setattr(upload, "match_ko_to_past_papers", fake_match)

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    rows = db_conn.execute(
        "SELECT question_text, question_source, question_source_detail "
        "FROM questions WHERE batch_id = ? ORDER BY id", (ko_batch,)
    ).fetchall()
    assert len(rows) == 3                       # 1 replaced + 2 inserted
    assert all(r["question_source"] == "past_paper" for r in rows)
    assert all(r["question_source_detail"] == "AQA 2023 Paper 1" for r in rows)
    assert {r["question_text"] for r in rows} == {"pp A", "pp B", "pp C"}


def test_blend_caps_at_three(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(
        db_conn, upload, user_id, sid, ["a", "b", "c", "d", "e"])

    monkeypatch.setattr(upload, "match_ko_to_past_papers",
        lambda k, p: [{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    n = db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id = ?", (ko_batch,)
    ).fetchone()["c"]
    assert n == 3   # capped


def test_blend_single_match_is_replace_only(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(db_conn, upload, user_id, sid, ["only one"])

    monkeypatch.setattr(upload, "match_ko_to_past_papers",
        lambda k, p: [{"ko_question_id": ko_q, "past_paper_question_id": pp_ids[0]}])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    rows = db_conn.execute(
        "SELECT id, question_text, question_source FROM questions WHERE batch_id = ?",
        (ko_batch,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == ko_q                 # same row, replaced in place
    assert rows[0]["question_source"] == "past_paper"
    assert rows[0]["question_text"] == "only one"


def test_blend_dedupes_pp_across_ko_questions(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_a = _make_ko_question(db_conn, ko_batch, user_id, sid, "KO A")
    ko_b = _make_ko_question(db_conn, ko_batch, user_id, sid, "KO B")
    pp_ids = _make_pp_batch_with_questions(db_conn, upload, user_id, sid, ["shared pp"])

    # Both KO questions claim the same single past-paper question.
    monkeypatch.setattr(upload, "match_ko_to_past_papers", lambda k, p: [
        {"ko_question_id": ko_a, "past_paper_question_id": pp_ids[0]},
        {"ko_question_id": ko_b, "past_paper_question_id": pp_ids[0]},
    ])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    pp_in_ko = db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id = ? AND question_source = 'past_paper'",
        (ko_batch,)
    ).fetchone()["c"]
    assert pp_in_ko == 1   # used once; the second KO question keeps its AI question
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_upload.py -k blend -q`
Expected: FAIL — current code does 1:1 replace, so `test_blend_keeps_multiple_matches`
finds 1 row not 3, and `test_blend_caps_at_three` finds 1 not 3.

- [ ] **Step 3: Widen the two SELECTs**

In `backend/routers/upload.py`, in `_match_and_replace_with_past_papers`:

Change the `ko_questions` query (currently selects `id, question_text, answer_text`) to also
load the fields needed to clone a row:

```python
    ko_questions = db.execute(
        "SELECT id, question_text, answer_text, category_id, subcategory_id, "
        "       approved, page_number "
        "FROM questions "
        "WHERE batch_id = ? AND question_source = 'ai_generated'",
        (batch_id,),
    ).fetchall()
```

Change the `past_paper_qs` query to also load `options_json`:

```python
    past_paper_qs = db.execute(
        """SELECT q.id, q.question_text, q.answer_text, q.options_json,
                  b.exam_board, b.exam_year, b.paper_number
           FROM questions q
           JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.subject_id = ? AND q.user_id = ? AND q.question_source = 'past_paper'
           ORDER BY q.id DESC""",
        (subject_id, user_id),
    ).fetchall()
```

- [ ] **Step 4: Replace the match-application loop**

Still in `_match_and_replace_with_past_papers`, replace the existing block that starts at
`used_pp_ids: set[int] = set()` and runs through the final
`print(f"[match_ko_to_past_papers] replaced ...")` with:

```python
    # Group matches by KO question, preserving the order the matcher returned them.
    grouped: dict[int, list[int]] = {}
    for m in matches:
        ko_q_id = m.get("ko_question_id")
        pp_q_id = m.get("past_paper_question_id")
        if not ko_q_id or not pp_q_id:
            continue
        grouped.setdefault(ko_q_id, []).append(pp_q_id)

    used_pp_ids: set[int] = set()
    replaced = 0
    inserted = 0
    for ko_q_id, pp_ids in grouped.items():
        ko_q = next((q for q in ko_questions if q["id"] == ko_q_id), None)
        if not ko_q:
            continue

        kept = 0
        for pp_q_id in pp_ids:
            if kept >= 3:
                break  # cap: at most 3 exam questions per KO point
            if pp_q_id in used_pp_ids:
                continue  # each past-paper question is used once overall
            pp_q = next((q for q in past_paper_qs if q["id"] == pp_q_id), None)
            if not pp_q:
                continue
            used_pp_ids.add(pp_q_id)

            parts = [pp_q["exam_board"] or "", str(pp_q["exam_year"] or ""), pp_q["paper_number"] or ""]
            source_detail = " ".join(p for p in parts if p).strip() or None

            if kept == 0:
                # First match: replace the AI-generated question in place.
                db.execute(
                    """UPDATE questions
                       SET question_text = ?,
                           answer_text = ?,
                           question_source = 'past_paper',
                           question_source_detail = ?,
                           options_json = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (pp_q["question_text"], pp_q["answer_text"], source_detail,
                     pp_q["options_json"], ko_q_id),
                )
                replaced += 1
            else:
                # Extra matches: insert new past-paper rows into the same KO batch,
                # inheriting the KO question's topic tags and approval state.
                db.execute(
                    """INSERT INTO questions
                       (batch_id, user_id, subject_id, category_id, subcategory_id,
                        page_number, question_text, answer_text, approved,
                        question_source, question_source_detail, options_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'past_paper', ?, ?)""",
                    (batch_id, user_id, subject_id,
                     ko_q["category_id"], ko_q["subcategory_id"],
                     ko_q["page_number"], pp_q["question_text"], pp_q["answer_text"],
                     ko_q["approved"], source_detail, pp_q["options_json"]),
                )
                inserted += 1
            kept += 1

    db.commit()
    print(f"[match_ko_to_past_papers] replaced {replaced}, inserted {inserted} extra past-paper questions")
```

- [ ] **Step 5: Run the blend tests + the existing matcher regression test**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_upload.py -q`
Expected: PASS — the 4 new blend tests, `test_matching_prompt_supports_multiple_and_formats`,
and the 3 pre-existing upload tests (`test_matcher_uses_full_corpus_not_just_100`,
`test_past_paper_upload_captures_figure`, `test_past_paper_question_without_figure_has_no_image`).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/upload.py tests/test_upload.py
git commit -m "feat: blend keeps up to 3 matched exam questions per KO point"
```

---

## Task 5: Coverage chip on the previous-uploads list

**Files:**
- Modify: `frontend/pages/upload-history.html:111-170` (inside the `x-for="b in batches"`)

This is presentational; verified in the preview rather than by automated test (the page
is a static HTML fragment loaded by the hash router).

- [ ] **Step 1: Add the chip markup**

In `frontend/pages/upload-history.html`, find the line that renders the questions count
(currently around line 134):

```html
                            &middot; <span x-text="b.question_count"></span> questions
                            (<span x-text="b.approved_count"></span> approved)
```

Immediately after that closing line, add a coverage indicator shown only for
knowledge-organiser batches:

```html
                            <template x-if="b.batch_type === 'knowledge_organiser'">
                                <span>
                                    <span x-show="b.past_paper_count > 0"
                                          class="ml-2 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 whitespace-nowrap"
                                          x-text="'📄 ' + b.past_paper_count + '/' + b.question_count + ' from past papers'"></span>
                                    <span x-show="!b.past_paper_count"
                                          class="ml-2 text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 whitespace-nowrap">All AI-generated</span>
                                </span>
                            </template>
```

- [ ] **Step 2: Verify in preview**

Open the preview (port 8001), log in, go to the upload history / previous uploads page.
Expected: each Knowledge Organiser row shows either a green `📄 N/M from past papers`
chip or a grey `All AI-generated` chip. Past-paper batches show no chip.

- [ ] **Step 3: Commit**

```bash
git add frontend/pages/upload-history.html
git commit -m "feat: past-paper coverage chip on previous-uploads list"
```

---

## Task 6: Coverage banner on the View Q&As (review) page

**Files:**
- Modify: `frontend/pages/review.html` (Alpine component `init`/data near top, lines ~1-60;
  and the markup above the questions list)

The review component currently loads questions via `API.get('/questions?batch_id=...')`.
Add a parallel coverage fetch and a banner. The coverage endpoint (Task 2) returns
`batch_type`, so the banner is gated authoritatively on that — no client-side guessing.

- [ ] **Step 1: Add coverage state + fetch to the Alpine component**

In `frontend/pages/review.html`, locate the component data object (starts around line 3
with `batchId: null,`). Add a state field near the top of the data object:

```javascript
    coverage: null,
```

Then, in the same method that loads questions (around line 44 where
`const res = await API.get('/questions?batch_id=' + this.batchId + '&limit=200');`
appears), add a coverage fetch right after the questions are assigned
(`this.questions = res.questions;`):

```javascript
            try {
                this.coverage = await API.get('/questions/coverage?batch_id=' + this.batchId);
            } catch (e) {
                this.coverage = null;
            }
```

- [ ] **Step 2: Add the banner markup**

In `frontend/pages/review.html`, find where the questions list begins (the
`<template x-for=` over questions, near line 430-440 where the first
`q.question_source === 'past_paper'` reference is). Immediately **before** that list's
container, insert:

```html
    <div x-show="coverage && coverage.batch_type === 'knowledge_organiser' && coverage.total > 0"
         class="mb-4 p-3 rounded-lg bg-indigo-50 border border-indigo-100">
        <div class="text-sm text-indigo-900">
            <span class="font-semibold"
                  x-text="coverage.past_paper + ' of ' + coverage.total + ' questions (' + Math.round((coverage.past_paper / coverage.total) * 100) + '%) matched to real past papers'"></span>
            <span class="text-indigo-700"
                  x-text="' · ' + coverage.ai_generated + ' AI-generated'"></span>
        </div>
        <div x-show="coverage.by_paper && coverage.by_paper.length > 0"
             class="mt-1 text-xs text-indigo-700 flex flex-wrap gap-x-3 gap-y-0.5">
            <template x-for="bp in coverage.by_paper" :key="bp.source">
                <span x-text="bp.source + ': ' + bp.count"></span>
            </template>
        </div>
    </div>
```

- [ ] **Step 3: Verify in preview**

Open the preview, navigate to a Knowledge Organiser's **View Q&As** page
(`#review/<batch_id>`).
Expected:
- For a KO uploaded with Blend: a banner like *"12 of 30 questions (40%) matched to real
  past papers · 18 AI-generated"* with a per-paper breakdown line.
- For a KO with no matches: *"0 of N questions (0%) matched … · N AI-generated"*, no
  breakdown line.
- For a **past-paper** batch's review page: **no banner**.
- The per-question badges still render unchanged.

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/review.html
git commit -m "feat: past-paper coverage banner on View Q&As page"
```

---

## Final verification

- [ ] **Run the full backend suite**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/ --tb=short -q`
Expected: all pass except the 3 pre-existing `tests/test_costs.py::test_history_*`
failures (unrelated `/api`-prefix WIP bug). No other failures.

- [ ] **Manual smoke (preview):** upload-history chip + review banner render as described;
  past-paper batches show neither.

---

## Self-review notes (author)

- **Spec Part A1** → Task 1. **A2 (endpoint)** → Task 2. **A2 (chip)** → Task 5.
  **A2 (banner + by_paper)** → Task 6.
- **Spec Part B1 (prompt)** → Task 3. **B2 (cap 3, replace+insert, clone fields,
  options_json, dedupe)** → Task 4. **B3 (1:1 preserved; figures not carried)** → covered
  by `test_blend_single_match_is_replace_only` and by the INSERT deliberately omitting
  `image_id`.
- **Edge cases:** NULL `question_source_detail` bucket (Task 2 test), `total=0` guard
  (endpoint returns zeros; banner gated on `coverage.total > 0`), other-user 404 (Task 2),
  pp-dedupe across KO questions (Task 4).
- **Naming consistency:** response keys `batch_type / total / past_paper / ai_generated /
  by_paper` with each breakdown row `{source, count}` — used identically in the endpoint
  (Task 2) and the review banner (Task 6, which gates on `coverage.batch_type`). List field
  `past_paper_count` used in Task 1 and Task 5.
- **Banner gating fix:** the review banner uses the endpoint's authoritative `batch_type`
  rather than inferring it from counts, so a fully-covered KO (0 AI questions) still shows
  the banner and a real past-paper batch never does.
