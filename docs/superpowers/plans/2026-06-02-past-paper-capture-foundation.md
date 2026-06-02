# Past-Paper Capture Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture per-question figures (diagrams/tables) on past-paper uploads into the existing `images` table and link them via `questions.image_id`, and make the full past-paper corpus available to the KO-to-past-paper matcher.

**Architecture:** Mirror the existing KO image-capture path. Extend the past-paper extraction prompt to return an `images[]` array + `related_image_index` per question, then remove the `batch_type != "past_paper"` guard so the existing crop/insert/link machinery in `process_batch` runs for past papers too. Separately, drop the `LIMIT 100` from the matcher's corpus query. No display work — quiz/review already JOIN `images` on `image_id`.

**Tech Stack:** Python, FastAPI, SQLite, Pillow (image crop), pytest. Spec: `docs/superpowers/specs/2026-06-02-past-paper-capture-foundation-design.md`.

---

## File Structure

- `backend/prompts/past_paper_extraction.py` — extraction prompt; add `images[]` + `related_image_index` to its JSON contract.
- `backend/routers/upload.py` — `process_batch` (remove past-paper image guard at ~line 321) and `_match_and_replace_with_past_papers` (remove `LIMIT 100` at ~line 154).
- `tests/test_upload.py` — **new** test file covering the matcher corpus query and the past-paper capture pipeline.

No new tables, columns, or display code. `questions.image_id` and the `images` table already exist; `quiz.py`/`questions.py` already `LEFT JOIN images i ON i.id = q.image_id`.

### Testing notes (read before writing tests)

`backend/routers/upload.py` binds `DB_PATH` (line 12) and `DATA_DIR` (line 30) **at import time**, so the autouse `isolated_db` fixture in `tests/conftest.py` (which patches `backend.database.DB_PATH`/`DATA_DIR`) does **not** redirect them. Tests that call `process_batch` MUST monkeypatch `backend.routers.upload.DB_PATH` to the per-test DB path. This mirrors the `mcq_service.DB_PATH` stale-reference handling already documented in `conftest.py`.

`process_batch` calls Claude and does real PDF/image I/O. Tests stub these by monkeypatching the names imported into the `upload` module: `extract_qa_from_past_paper`, `render_page_to_png`, `save_full_page_image`, `crop_image_region`.

---

## Task 1: Lift the `LIMIT 100` on the matcher corpus

**Files:**
- Test: `tests/test_upload.py` (create)
- Modify: `backend/routers/upload.py` (`_match_and_replace_with_past_papers`, the corpus `SELECT` around line 147-156)

- [ ] **Step 1: Write the failing test**

Create `tests/test_upload.py` with (uses the existing conftest fixtures `isolated_db`, `db_conn`, `regular_user`, `make_subject`, `make_batch`):

```python
"""Tests for past-paper upload processing: matcher corpus + figure capture."""
import sqlite3

import backend.routers.upload as upload


def _set_past_paper(db_conn, batch_id):
    """Flip a batch's type to past_paper (make_batch defaults to knowledge_organiser)."""
    db_conn.execute(
        "UPDATE upload_batches SET batch_type = 'past_paper' WHERE id = ?", (batch_id,)
    )
    db_conn.commit()


def test_matcher_uses_full_corpus_not_just_100(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """The matcher must consider every past-paper question, not just the newest 100."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()

    ko_batch = make_batch(user_id, subject_id)   # default batch_type = knowledge_organiser
    pp_batch = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, pp_batch)

    # One AI-generated KO question
    db_conn.execute(
        """INSERT INTO questions (batch_id, user_id, subject_id, page_number,
           question_text, answer_text, question_source)
           VALUES (?, ?, ?, 1, 'KO q', 'KO a', 'ai_generated')""",
        (ko_batch, user_id, subject_id),
    )
    # 150 past-paper questions
    for i in range(150):
        db_conn.execute(
            """INSERT INTO questions (batch_id, user_id, subject_id, page_number,
               question_text, answer_text, question_source)
               VALUES (?, ?, ?, 1, ?, ?, 'past_paper')""",
            (pp_batch, user_id, subject_id, f"PP q{i}", f"PP a{i}"),
        )
    db_conn.commit()

    captured = {}

    def fake_match(ko_list, pp_list):
        captured["pp_count"] = len(pp_list)
        return []

    monkeypatch.setattr(upload, "match_ko_to_past_papers", fake_match)

    upload._match_and_replace_with_past_papers(ko_batch, user_id, subject_id, db_conn)

    assert captured["pp_count"] == 150
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upload.py::test_matcher_uses_full_corpus_not_just_100 -v`
Expected: FAIL — `assert 100 == 150` (the current `LIMIT 100` caps the corpus).

- [ ] **Step 3: Remove the `LIMIT 100`**

In `backend/routers/upload.py`, in `_match_and_replace_with_past_papers`, change the corpus query from:

```python
    past_paper_qs = db.execute(
        """SELECT q.id, q.question_text, q.answer_text,
                  b.exam_board, b.exam_year, b.paper_number
           FROM questions q
           JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.subject_id = ? AND q.user_id = ? AND q.question_source = 'past_paper'
           ORDER BY q.id DESC
           LIMIT 100""",
        (subject_id, user_id),
    ).fetchall()
```

to (drop only the `LIMIT 100` line):

```python
    past_paper_qs = db.execute(
        """SELECT q.id, q.question_text, q.answer_text,
                  b.exam_board, b.exam_year, b.paper_number
           FROM questions q
           JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.subject_id = ? AND q.user_id = ? AND q.question_source = 'past_paper'
           ORDER BY q.id DESC""",
        (subject_id, user_id),
    ).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_upload.py::test_matcher_uses_full_corpus_not_just_100 -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_upload.py backend/routers/upload.py
git commit -m "feat: matcher considers full past-paper corpus (drop LIMIT 100)"
```

---

## Task 2: Capture figures on past-paper uploads

**Files:**
- Modify: `backend/prompts/past_paper_extraction.py` (add `images[]` + `related_image_index` to the JSON contract)
- Modify: `backend/routers/upload.py` (`process_batch`, remove the `if batch_type != "past_paper":` guard at ~line 319-321)
- Test: `tests/test_upload.py` (add a capture test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_upload.py`:

```python
def test_past_paper_upload_captures_figure(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """A past-paper diagram question should get its figure cropped and linked via image_id."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, batch_id)

    # Stub the Claude extraction: one diagram-based question + one figure region
    extraction_result = {
        "page_type": "questions",
        "questions": [
            {
                "question_ref": "1a",
                "question": "Label structure X in the diagram.",
                "answer": "Nucleus",
                "marks": 1,
                "type": "diagram-based",
                "difficulty": 1,
                "related_image_index": 0,
            }
        ],
        "answers": [],
        "images": [
            {
                "description": "Cell diagram",
                "bbox_x_pct": 10.0,
                "bbox_y_pct": 20.0,
                "bbox_w_pct": 40.0,
                "bbox_h_pct": 30.0,
            }
        ],
    }
    usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0}

    monkeypatch.setattr(
        upload, "extract_qa_from_past_paper", lambda b64, subj: (extraction_result, usage)
    )
    monkeypatch.setattr(upload, "render_page_to_png", lambda path, n: b"fakepng")
    monkeypatch.setattr(upload, "save_full_page_image", lambda *a, **kw: "full.png")
    monkeypatch.setattr(
        upload,
        "crop_image_region",
        lambda *a, **kw: ("batch_x/page_1_img_0.png", 120, 90),
    )

    upload.process_batch(
        batch_id=batch_id,
        pdf_path="ignored.pdf",
        subject_name="Biology",
        subject_id=subject_id,
        user_id=user_id,
        page_start=1,
        page_end=1,
        batch_type="past_paper",
    )

    # process_batch wrote via its own connection; read back with a fresh one
    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    images = conn.execute(
        "SELECT * FROM images WHERE batch_id = ?", (batch_id,)
    ).fetchall()
    question = conn.execute(
        "SELECT * FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert len(images) == 1
    assert images[0]["filename"] == "batch_x/page_1_img_0.png"
    assert question["image_id"] == images[0]["id"]
    assert question["question_source"] == "past_paper"


def test_past_paper_question_without_figure_has_no_image(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """A non-diagram past-paper question yields no images row and a NULL image_id."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, batch_id)

    extraction_result = {
        "page_type": "questions",
        "questions": [
            {
                "question_ref": "2a",
                "question": "Define osmosis.",
                "answer": "Net movement of water...",
                "marks": 2,
                "type": "definition",
                "difficulty": 1,
                "related_image_index": None,
            }
        ],
        "answers": [],
        "images": [],
    }
    usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0}

    monkeypatch.setattr(
        upload, "extract_qa_from_past_paper", lambda b64, subj: (extraction_result, usage)
    )
    monkeypatch.setattr(upload, "render_page_to_png", lambda path, n: b"fakepng")
    monkeypatch.setattr(upload, "save_full_page_image", lambda *a, **kw: "full.png")
    monkeypatch.setattr(
        upload, "crop_image_region", lambda *a, **kw: ("never.png", 1, 1)
    )

    upload.process_batch(
        batch_id=batch_id,
        pdf_path="ignored.pdf",
        subject_name="Biology",
        subject_id=subject_id,
        user_id=user_id,
        page_start=1,
        page_end=1,
        batch_type="past_paper",
    )

    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    images = conn.execute(
        "SELECT * FROM images WHERE batch_id = ?", (batch_id,)
    ).fetchall()
    question = conn.execute(
        "SELECT * FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert len(images) == 0
    assert question["image_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_upload.py::test_past_paper_upload_captures_figure tests/test_upload.py::test_past_paper_question_without_figure_has_no_image -v`
Expected: `test_past_paper_upload_captures_figure` FAILS — `assert 0 == 1` (no images row) because the `if batch_type != "past_paper":` guard skips cropping, so `image_id` is `NULL`. The no-figure test should already PASS (empty `images[]` is a no-op regardless of the guard); that's fine.

- [ ] **Step 3: Remove the past-paper image guard in `process_batch`**

In `backend/routers/upload.py`, change this block (around line 319-350):

```python
                # Process image regions (KO batches only — past papers rarely need image crops)
                image_id_map = {}  # index -> db image id
                if batch_type != "past_paper":
                    for i, img_data in enumerate(result.get("images", [])):
                        filename, width, height = crop_image_region(
                            batch_id,
                            display_page,
                            i,
                            png_bytes,
                            img_data.get("bbox_x_pct", 0),
                            img_data.get("bbox_y_pct", 0),
                            img_data.get("bbox_w_pct", 100),
                            img_data.get("bbox_h_pct", 100),
                        )
                        cursor = db.execute(
                            """INSERT INTO images (batch_id, page_number, filename, description,
                               crop_x, crop_y, crop_w, crop_h, width, height)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                batch_id,
                                display_page,
                                filename,
                                img_data.get("description", ""),
                                img_data.get("bbox_x_pct"),
                                img_data.get("bbox_y_pct"),
                                img_data.get("bbox_w_pct"),
                                img_data.get("bbox_h_pct"),
                                width,
                                height,
                            ),
                        )
                        image_id_map[i] = cursor.lastrowid
```

to (drop the `if` and de-indent its body one level):

```python
                # Process image regions — crop figures/diagrams for both KO and past-paper pages
                image_id_map = {}  # index -> db image id
                for i, img_data in enumerate(result.get("images", [])):
                    filename, width, height = crop_image_region(
                        batch_id,
                        display_page,
                        i,
                        png_bytes,
                        img_data.get("bbox_x_pct", 0),
                        img_data.get("bbox_y_pct", 0),
                        img_data.get("bbox_w_pct", 100),
                        img_data.get("bbox_h_pct", 100),
                    )
                    cursor = db.execute(
                        """INSERT INTO images (batch_id, page_number, filename, description,
                           crop_x, crop_y, crop_w, crop_h, width, height)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id,
                            display_page,
                            filename,
                            img_data.get("description", ""),
                            img_data.get("bbox_x_pct"),
                            img_data.get("bbox_y_pct"),
                            img_data.get("bbox_w_pct"),
                            img_data.get("bbox_h_pct"),
                            width,
                            height,
                        ),
                    )
                    image_id_map[i] = cursor.lastrowid
```

- [ ] **Step 4: Add `images[]` + `related_image_index` to the past-paper prompt**

In `backend/prompts/past_paper_extraction.py`, update the JSON contract so the model returns figure regions. Change the question object to include `related_image_index`, add an `images` array to the top-level JSON, and add rules. Replace the JSON structure block:

```
Return JSON with this exact structure (always include both "questions" and "answers" arrays):
{{
  "page_type": "questions|mark_scheme|both|cover",
  "questions": [
    {{
      "question_ref": "1a",
      "question": "Exact question text as written on the paper",
      "answer": "Mark scheme answer if visible; otherwise AI-inferred answer",
      "marks": 2,
      "type": "factual|definition|calculation|extended_writing|diagram-based",
      "difficulty": 2
    }}
  ],
  "answers": [
    {{
      "question_ref": "1a",
      "answer": "Full mark scheme answer with all creditworthy points"
    }}
  ]
}}
```

with:

```
Return JSON with this exact structure (always include "questions", "answers", and "images" arrays):
{{
  "page_type": "questions|mark_scheme|both|cover",
  "questions": [
    {{
      "question_ref": "1a",
      "question": "Exact question text as written on the paper",
      "answer": "Mark scheme answer if visible; otherwise AI-inferred answer",
      "marks": 2,
      "type": "factual|definition|calculation|extended_writing|diagram-based",
      "difficulty": 2,
      "related_image_index": null
    }}
  ],
  "answers": [
    {{
      "question_ref": "1a",
      "answer": "Full mark scheme answer with all creditworthy points"
    }}
  ],
  "images": [
    {{
      "description": "Diagram showing...",
      "bbox_x_pct": 10.0,
      "bbox_y_pct": 30.0,
      "bbox_w_pct": 45.0,
      "bbox_h_pct": 40.0
    }}
  ]
}}
```

Then, in the same file, add these lines to the `QUESTION PAGES (page_type "questions" or "both")` rules block, immediately after the `difficulty:` bullet:

```
  - related_image_index: if the question depends on a figure, diagram, table, or source image, set this to the 0-based index of that figure in the "images" array; otherwise null

FIGURES (images[] — question pages only):
- Identify any diagram, table, chart, graph, or source figure that a question depends on to be answerable.
- For each, add an entry to images[] with a short description and approximate bounding-box coordinates as percentages of the page (bbox_x_pct, bbox_y_pct, bbox_w_pct, bbox_h_pct).
- Only include meaningful figures a question needs — skip decorative elements, headers, page numbers, and examiner-use boxes.
- Leave images[] empty ([]) for mark_scheme and cover pages.
```

- [ ] **Step 5: Run the capture tests to verify they pass**

Run: `pytest tests/test_upload.py::test_past_paper_upload_captures_figure tests/test_upload.py::test_past_paper_question_without_figure_has_no_image -v`
Expected: both PASS.

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `pytest tests/ --tb=short -q`
Expected: all tests pass (existing KO capture behaviour unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/prompts/past_paper_extraction.py backend/routers/upload.py tests/test_upload.py
git commit -m "feat: capture figures on past-paper uploads and link via image_id"
```

---

## Self-Review

**Spec coverage:**
- Spec change 1 (prompt `images[]` + `related_image_index`) → Task 2, Step 4. ✓
- Spec change 2 (remove past-paper guard so crop block runs) → Task 2, Step 3. ✓
- Spec change 3 (remove `LIMIT 100`) → Task 1, Step 3. ✓
- Spec change 4 (display: none) → no task needed; verified existing JOINs. ✓
- Spec testing: capture test (figure present), no-figure case, limit-removal test, full-suite regression → Tasks 1 & 2 cover all four. ✓
- Scope boundaries (one image/question, no backfill, no image matching, no UI) → respected; no tasks add these. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full before/after content and exact commands with expected output. ✓

**Type consistency:** Tests reuse existing conftest fixtures (`isolated_db`, `db_conn`, `regular_user`, `make_subject`, `make_batch`) with their real signatures — `regular_user` returns `(user_id, token)`, `make_subject()` returns `subject_id`, `make_batch(user_id, subject_id)` returns `batch_id` defaulting to `batch_type='knowledge_organiser'` (flipped to `past_paper` via the local `_set_past_paper` helper, which matches the `batch_type` column added in `database.py`). Monkeypatched symbols (`upload.DB_PATH`, `upload.extract_qa_from_past_paper`, `upload.render_page_to_png`, `upload.save_full_page_image`, `upload.crop_image_region`, `upload.match_ko_to_past_papers`), and `process_batch` keyword args match the real signature in `backend/routers/upload.py:205`. The `usage` dict keys (`input_tokens`, `output_tokens`, `cost_usd`) match the `api_usage` inserts. ✓
