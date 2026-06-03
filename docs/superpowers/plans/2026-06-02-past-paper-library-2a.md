# Past-Paper Library — Phase 2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dedicated past-paper library page to browse papers and their questions, edit question text, delete questions/papers, and topic-tag questions (per-question + bulk), reusing existing endpoints where possible.

**Architecture:** New `backend/routers/past_papers.py` (mounted at `/api/past-papers`), two small additions to `backend/routers/questions.py` (a `question_source` filter and an editable `question_ref`), and a new Alpine.js page `frontend/pages/past-papers.html` with a route and nav link. No schema changes.

**Tech Stack:** FastAPI, SQLite, Pydantic, Alpine.js v3 (CDN), Tailwind (CDN), pytest. Spec: `docs/superpowers/specs/2026-06-02-past-paper-library-design.md`.

---

## File Structure

- `backend/models.py` — add `question_ref` to `QuestionUpdate` (line ~46).
- `backend/routers/questions.py` — add `question_source` filter to `list_questions` (line ~14), persist `question_ref` in `update_question` (line ~168).
- `backend/routers/past_papers.py` — **new**: `GET /past-papers`, `DELETE /past-papers/{batch_id}`, `POST /past-papers/tag`.
- `backend/app.py` — register the new router (imports ~line 30, includes ~line 42).
- `frontend/pages/past-papers.html` — **new** library page.
- `frontend/js/router.js` — add `past-papers` route (line ~21).
- `frontend/index.html` — add nav links (desktop ~line 43, mobile ~line 114).
- `tests/test_past_papers.py` — **new** backend tests.

### Conventions (read before writing tests)

- The API mounts under `/api` (e.g. `/api/past-papers`). Static images are served at
  `/images/<filename>` (e.g. `/images/batch_5/page_2_img_0.png`).
- Tests use the `client` TestClient fixture + a bearer header. The `regular_user` fixture
  returns `(user_id, token)`; the `user_headers` fixture provides
  `{"Authorization": "Bearer <token>"}` for that **same** user. Other factories:
  `make_subject() -> subject_id`, `make_batch(user_id, subject_id) -> batch_id`
  (defaults `batch_type='knowledge_organiser'`, `status='completed'`),
  `make_question(batch_id, user_id, subject_id, ...) -> question_id`, and `db_conn`.
- `get_db` sets `PRAGMA foreign_keys=ON`, so deleting an `upload_batches` row cascades to
  its `questions` and `images` rows.

A shared test helper used across tasks (define once at the top of `tests/test_past_papers.py`):

```python
def _make_past_paper(db_conn, batch_id, board="AQA", year=2023, paper="Paper 1", tier="Foundation"):
    db_conn.execute(
        """UPDATE upload_batches
           SET batch_type='past_paper', exam_board=?, exam_year=?, paper_number=?, tier=?
           WHERE id=?""",
        (board, year, paper, tier, batch_id),
    )
    db_conn.commit()


def _set_source(db_conn, question_id, source="past_paper"):
    db_conn.execute(
        "UPDATE questions SET question_source=? WHERE id=?", (source, question_id)
    )
    db_conn.commit()


def _add_image(db_conn, batch_id, question_id, filename="batch_x/page_1_img_0.png"):
    cur = db_conn.execute(
        "INSERT INTO images (batch_id, page_number, filename) VALUES (?, 1, ?)",
        (batch_id, filename),
    )
    image_id = cur.lastrowid
    db_conn.execute("UPDATE questions SET image_id=? WHERE id=?", (image_id, question_id))
    db_conn.commit()
    return image_id
```

---

## Task 1: Add `question_source` filter to `list_questions`

**Files:**
- Test: `tests/test_past_papers.py` (create)
- Modify: `backend/routers/questions.py` (`list_questions`, ~line 14-43)

- [ ] **Step 1: Write the failing test**

Create `tests/test_past_papers.py` with the shared helpers above, then:

```python
def test_list_questions_filters_by_source(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    pp_q = make_question(batch_id, user_id, subject_id, question_text="PP q")
    ko_q = make_question(batch_id, user_id, subject_id, question_text="KO q")
    _set_source(db_conn, pp_q, "past_paper")
    _set_source(db_conn, ko_q, "ai_generated")

    r = client.get(
        f"/api/questions?batch_id={batch_id}&question_source=past_paper",
        headers=user_headers,
    )
    assert r.status_code == 200
    ids = [q["id"] for q in r.json()["questions"]]
    assert ids == [pp_q]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_past_papers.py::test_list_questions_filters_by_source -v`
Expected: FAIL — both questions returned (no source filter yet), so `ids == [pp_q, ko_q]`.

- [ ] **Step 3: Add the filter**

In `backend/routers/questions.py`, in `list_questions`, add a parameter and condition.

Add to the signature (after `approved: int | None = None,`):

```python
    question_source: str | None = None,
```

Add to the conditions block (after the `approved` condition):

```python
    if question_source is not None:
        conditions.append("q.question_source = ?")
        params.append(question_source)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_past_papers.py::test_list_questions_filters_by_source -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_past_papers.py backend/routers/questions.py
git commit -m "feat: add question_source filter to list_questions"
```

---

## Task 2: Make `question_ref` editable

**Files:**
- Test: `tests/test_past_papers.py`
- Modify: `backend/models.py` (`QuestionUpdate`, ~line 46), `backend/routers/questions.py` (`update_question`, ~line 168-205)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_past_papers.py`:

```python
def test_update_question_persists_question_ref(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q_id = make_question(batch_id, user_id, subject_id)

    r = client.put(
        f"/api/questions/{q_id}",
        headers=user_headers,
        json={"question_ref": "1a"},
    )
    assert r.status_code == 200
    row = db_conn.execute(
        "SELECT question_ref FROM questions WHERE id=?", (q_id,)
    ).fetchone()
    assert row["question_ref"] == "1a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_past_papers.py::test_update_question_persists_question_ref -v`
Expected: FAIL — `question_ref` is not in `QuestionUpdate`, so it is ignored; the value stays `NULL` (or the request 400s with "No fields to update").

- [ ] **Step 3: Add the field to the model**

In `backend/models.py`, in `class QuestionUpdate`, add after `difficulty`:

```python
    question_ref: str | None = None
```

- [ ] **Step 4: Persist it in the handler**

In `backend/routers/questions.py`, in `update_question`, add after the `difficulty` block:

```python
    if req.question_ref is not None:
        updates.append("question_ref = ?")
        params.append(req.question_ref)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_past_papers.py::test_update_question_persists_question_ref -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_past_papers.py backend/models.py backend/routers/questions.py
git commit -m "feat: allow editing question_ref via PUT /questions/{id}"
```

---

## Task 3: `GET /past-papers` + register router

**Files:**
- Create: `backend/routers/past_papers.py`
- Modify: `backend/app.py` (imports ~line 30, includes ~line 42)
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_past_papers.py`:

```python
def test_list_past_papers(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()

    pp_batch = make_batch(user_id, subject_id)
    _make_past_paper(db_conn, pp_batch)
    q1 = make_question(pp_batch, user_id, subject_id)
    q2 = make_question(pp_batch, user_id, subject_id)
    _set_source(db_conn, q1, "past_paper")
    _set_source(db_conn, q2, "past_paper")
    _add_image(db_conn, pp_batch, q1)  # one figure

    ko_batch = make_batch(user_id, subject_id)  # knowledge_organiser — must be excluded

    r = client.get(f"/api/past-papers?subject_id={subject_id}", headers=user_headers)
    assert r.status_code == 200
    papers = r.json()
    assert len(papers) == 1
    p = papers[0]
    assert p["id"] == pp_batch
    assert p["exam_board"] == "AQA"
    assert p["question_count"] == 2
    assert p["figure_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_past_papers.py::test_list_past_papers -v`
Expected: FAIL — `404` (route does not exist yet).

- [ ] **Step 3: Create the router**

Create `backend/routers/past_papers.py`:

```python
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.database import get_db
from backend.services.image_service import delete_batch_images, delete_batch_pdf

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()


@router.get("")
def list_past_papers(
    subject_id: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """List the user's past-paper batches for a subject, with question/figure counts."""
    rows = db.execute(
        """SELECT b.id, b.filename, b.exam_board, b.exam_year, b.paper_number,
                  b.tier, b.created_at,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) AS question_count,
                  (SELECT COUNT(*) FROM questions q
                     WHERE q.batch_id = b.id AND q.image_id IS NOT NULL) AS figure_count
           FROM upload_batches b
           WHERE b.user_id = ? AND b.subject_id = ? AND b.batch_type = 'past_paper'
           ORDER BY b.exam_year DESC, b.created_at DESC""",
        (user["id"], subject_id),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Register the router in `backend/app.py`**

Add the import alongside the other router imports (~line 30):

```python
from backend.routers.past_papers import router as past_papers_router
```

Add the include alongside the others (~line 42):

```python
app.include_router(past_papers_router, prefix="/api/past-papers", tags=["past-papers"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_past_papers.py::test_list_past_papers -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/past_papers.py backend/app.py tests/test_past_papers.py
git commit -m "feat: add GET /past-papers listing endpoint"
```

---

## Task 4: `DELETE /past-papers/{batch_id}`

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_past_papers.py`:

```python
def test_delete_past_paper_cascades(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _make_past_paper(db_conn, batch_id)
    q = make_question(batch_id, user_id, subject_id)
    _set_source(db_conn, q, "past_paper")
    _add_image(db_conn, batch_id, q)

    r = client.delete(f"/api/past-papers/{batch_id}", headers=user_headers)
    assert r.status_code == 200

    assert db_conn.execute(
        "SELECT COUNT(*) c FROM upload_batches WHERE id=?", (batch_id,)
    ).fetchone()["c"] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM images WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"] == 0


def test_delete_past_paper_rejects_ko_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch
):
    user_id, _ = regular_user
    subject_id = make_subject()
    ko_batch = make_batch(user_id, subject_id)  # knowledge_organiser

    r = client.delete(f"/api/past-papers/{ko_batch}", headers=user_headers)
    assert r.status_code == 404
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM upload_batches WHERE id=?", (ko_batch,)
    ).fetchone()["c"] == 1


def test_delete_past_paper_rejects_other_user(
    client, db_conn, regular_user, second_user, make_subject, make_batch
):
    owner_id, _ = regular_user
    _, other_token = second_user
    subject_id = make_subject()
    batch_id = make_batch(owner_id, subject_id)
    _make_past_paper(db_conn, batch_id)

    r = client.delete(
        f"/api/past-papers/{batch_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_past_papers.py -k delete_past_paper -v`
Expected: FAIL — `405`/`404` (route does not exist).

- [ ] **Step 3: Add the endpoint**

Append to `backend/routers/past_papers.py`:

```python
@router.delete("/{batch_id}")
def delete_past_paper(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Delete one of the user's past-paper batches and all its questions/figures."""
    batch = db.execute(
        "SELECT id FROM upload_batches WHERE id = ? AND user_id = ? AND batch_type = 'past_paper'",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Past paper not found")

    delete_batch_images(batch_id)
    delete_batch_pdf(batch_id)
    # Mark scheme PDF, if any (delete_batch_pdf only removes the question paper)
    ms_pdf = DATA_DIR / "pdfs" / f"batch_{batch_id}_ms.pdf"
    if ms_pdf.exists():
        ms_pdf.unlink()

    db.execute("DELETE FROM upload_batches WHERE id = ?", (batch_id,))
    db.commit()
    return {"message": "Past paper deleted"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_past_papers.py -k delete_past_paper -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: add DELETE /past-papers/{batch_id} with cascade"
```

---

## Task 5: `POST /past-papers/tag` (per-question + bulk)

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_past_papers.py`:

```python
def _make_category(db_conn, subject_id, name="Cells"):
    cur = db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    )
    db_conn.commit()
    return cur.lastrowid


def test_tag_questions_bulk(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id)
    q2 = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q1, q2], "category_id": cat, "subcategory_id": None},
    )
    assert r.status_code == 200
    for q in (q1, q2):
        row = db_conn.execute(
            "SELECT category_id FROM questions WHERE id=?", (q,)
        ).fetchone()
        assert row["category_id"] == cat


def test_tag_clears_with_null(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)
    db_conn.execute("UPDATE questions SET category_id=? WHERE id=?", (cat, q))
    db_conn.commit()

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": None, "subcategory_id": None},
    )
    assert r.status_code == 200
    row = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["category_id"] is None


def test_tag_rejects_cross_subject_category(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_a = make_subject("Biology")
    subject_b = make_subject("Chemistry")
    batch_id = make_batch(user_id, subject_a)
    q = make_question(batch_id, user_id, subject_a)
    foreign_cat = _make_category(db_conn, subject_b)  # belongs to a different subject

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": foreign_cat, "subcategory_id": None},
    )
    assert r.status_code == 400
    row = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["category_id"] is None


def test_tag_ignores_questions_not_owned(
    client, db_conn, regular_user, second_user, user_headers,
    make_subject, make_batch, make_question
):
    owner_id, _ = regular_user
    other_id, _ = second_user
    subject_id = make_subject()
    batch_id = make_batch(other_id, subject_id)
    foreign_q = make_question(batch_id, other_id, subject_id)  # owned by second_user
    cat = _make_category(db_conn, subject_id)

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [foreign_q], "category_id": cat, "subcategory_id": None},
    )
    assert r.status_code == 200  # no error, but nothing changes
    row = db_conn.execute(
        "SELECT category_id FROM questions WHERE id=?", (foreign_q,)
    ).fetchone()
    assert row["category_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_past_papers.py -k tag -v`
Expected: FAIL — `404`/`405` (route does not exist).

- [ ] **Step 3: Add the endpoint**

Append to `backend/routers/past_papers.py` (add `BaseModel` import is already present):

```python
class TagRequest(BaseModel):
    question_ids: list[int]
    category_id: int | None = None
    subcategory_id: int | None = None


@router.post("/tag")
def tag_questions(
    req: TagRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Set (or clear) category/subcategory on the user's questions. Per-question or bulk."""
    if not req.question_ids:
        return {"message": "No questions to tag", "updated": 0}

    # Resolve the owned questions and their subject(s)
    placeholders = ",".join("?" for _ in req.question_ids)
    owned = db.execute(
        f"""SELECT id, subject_id FROM questions
            WHERE id IN ({placeholders}) AND user_id = ?""",
        (*req.question_ids, user["id"]),
    ).fetchall()
    if not owned:
        return {"message": "No matching questions", "updated": 0}

    # Validate category belongs to the questions' subject
    if req.category_id is not None:
        subject_ids = {row["subject_id"] for row in owned}
        cat = db.execute(
            "SELECT subject_id FROM categories WHERE id = ?", (req.category_id,)
        ).fetchone()
        if not cat or cat["subject_id"] not in subject_ids:
            raise HTTPException(
                status_code=400, detail="Category does not belong to the question's subject"
            )
    # Validate subcategory belongs to the category
    if req.subcategory_id is not None:
        sub = db.execute(
            "SELECT category_id FROM subcategories WHERE id = ?", (req.subcategory_id,)
        ).fetchone()
        if not sub or sub["category_id"] != req.category_id:
            raise HTTPException(
                status_code=400, detail="Subcategory does not belong to the category"
            )

    owned_ids = [row["id"] for row in owned]
    ph = ",".join("?" for _ in owned_ids)
    db.execute(
        f"UPDATE questions SET category_id = ?, subcategory_id = ? WHERE id IN ({ph})",
        (req.category_id, req.subcategory_id, *owned_ids),
    )
    db.commit()
    return {"message": "Tagged", "updated": len(owned_ids)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_past_papers.py -k tag -v`
Expected: all PASS.

- [ ] **Step 5: Run the full new test file + full suite**

Run: `venv/bin/pytest tests/test_past_papers.py -v`
Expected: all PASS.
Run: `venv/bin/pytest tests/ -q`
Expected: no NEW failures (the 3 pre-existing `tests/test_costs.py` `/costs/history` 404 failures are unrelated — they fail on `main` too).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: add POST /past-papers/tag for per-question and bulk tagging"
```

---

## Task 6: Library page + route + nav

**Files:**
- Create: `frontend/pages/past-papers.html`
- Modify: `frontend/js/router.js` (~line 21), `frontend/index.html` (desktop nav ~line 43, mobile nav ~line 114)

This task is UI; verify manually in the browser (no automated test). Follow the
`frontend/pages/review.html` pattern: a `<script>Alpine.data('name', () => ({...}))</script>`
block followed by `<div x-data="name">` markup, using the global `API` helper
(`API.get/post/put/del`, paths under `/api`).

- [ ] **Step 1: Add the route**

In `frontend/js/router.js`, add to the `routes` object (after `'multi-processing'`):

```javascript
        'past-papers': '/static/pages/past-papers.html',
```

- [ ] **Step 2: Add nav links**

In `frontend/index.html`, in the desktop nav (next to the `#upload` link, ~line 43), add:

```html
                    <a href="#past-papers"
                       class="px-3 py-2 rounded-md text-sm font-medium hover:bg-indigo-500 transition-colors">Past Papers</a>
```

In the mobile nav (next to the mobile `#upload` link, ~line 114), add:

```html
                <a href="#past-papers" @click="mobileOpen=false"
                   class="block px-3 py-2 rounded-md text-base font-medium hover:bg-indigo-500">Past Papers</a>
```

- [ ] **Step 3: Create the page**

Create `frontend/pages/past-papers.html`:

```html
<script>
Alpine.data('pastPapersPage', () => ({
    subjects: [],
    subjectId: null,
    papers: [],
    loading: false,
    expanded: {},            // { [batchId]: true }
    questions: {},           // { [batchId]: [question...] }
    categories: [],          // for the selected subject
    subcategories: {},       // { [categoryId]: [subcat...] }
    selected: {},            // { [questionId]: true }
    bulkCategoryId: '',
    bulkSubcategoryId: '',
    editing: null,           // question object being edited (or null)
    editForm: {},

    async init() {
        this.subjects = await API.get('/api/subjects');
        if (this.subjects.length) {
            this.subjectId = this.subjects[0].id;
            await this.loadSubject();
        }
    },

    async loadSubject() {
        this.loading = true;
        this.expanded = {}; this.questions = {}; this.selected = {};
        this.papers = await API.get(`/api/past-papers?subject_id=${this.subjectId}`);
        this.categories = await API.get(`/api/categories?subject_id=${this.subjectId}`);
        this.loading = false;
    },

    paperLabel(p) {
        return [p.exam_board, p.exam_year, p.paper_number, p.tier].filter(Boolean).join(' ') || p.filename;
    },

    async toggle(batchId) {
        this.expanded[batchId] = !this.expanded[batchId];
        if (this.expanded[batchId] && !this.questions[batchId]) {
            const res = await API.get(`/api/questions?batch_id=${batchId}&question_source=past_paper&limit=200`);
            this.questions[batchId] = res.questions;
        }
    },

    async deletePaper(p) {
        if (!confirm(`Delete "${this.paperLabel(p)}" and all its questions? This cannot be undone.`)) return;
        await API.del(`/api/past-papers/${p.id}`);
        await this.loadSubject();
    },

    async deleteQuestion(batchId, q) {
        if (!confirm('Delete this question?')) return;
        await API.del(`/api/questions/${q.id}`);
        this.questions[batchId] = this.questions[batchId].filter(x => x.id !== q.id);
    },

    startEdit(q) {
        this.editing = q.id;
        this.editForm = {
            question_text: q.question_text, answer_text: q.answer_text,
            question_type: q.question_type, difficulty: q.difficulty,
            question_ref: q.question_ref || '',
        };
    },
    async saveEdit(batchId, q) {
        await API.put(`/api/questions/${q.id}`, this.editForm);
        Object.assign(q, this.editForm);
        this.editing = null;
    },

    async loadSubcats(categoryId) {
        if (categoryId && !this.subcategories[categoryId]) {
            this.subcategories[categoryId] = await API.get(`/api/subcategories?category_id=${categoryId}`);
        }
    },

    async tagOne(q, categoryId, subcategoryId) {
        await API.post('/api/past-papers/tag', {
            question_ids: [q.id],
            category_id: categoryId || null,
            subcategory_id: subcategoryId || null,
        });
        q.category_id = categoryId || null;
        q.subcategory_id = subcategoryId || null;
    },

    selectedIds(batchId) {
        return (this.questions[batchId] || []).filter(q => this.selected[q.id]).map(q => q.id);
    },
    toggleAll(batchId) {
        const qs = this.questions[batchId] || [];
        const allSel = qs.every(q => this.selected[q.id]);
        qs.forEach(q => this.selected[q.id] = !allSel);
    },
    async applyBulkTag(batchId) {
        const ids = this.selectedIds(batchId);
        if (!ids.length) return;
        await API.post('/api/past-papers/tag', {
            question_ids: ids,
            category_id: this.bulkCategoryId || null,
            subcategory_id: this.bulkSubcategoryId || null,
        });
        const qs = this.questions[batchId];
        qs.forEach(q => { if (this.selected[q.id]) {
            q.category_id = this.bulkCategoryId || null;
            q.subcategory_id = this.bulkSubcategoryId || null;
        }});
        ids.forEach(id => this.selected[id] = false);
    },
}));
</script>

<div x-data="pastPapersPage" class="max-w-5xl mx-auto p-4">
    <h1 class="text-2xl font-bold mb-4">Past Paper Library</h1>

    <div class="mb-4 flex items-center gap-2">
        <label class="text-sm font-medium">Subject:</label>
        <select x-model="subjectId" @change="loadSubject()" class="border rounded px-2 py-1">
            <template x-for="s in subjects" :key="s.id">
                <option :value="s.id" x-text="s.name"></option>
            </template>
        </select>
    </div>

    <div x-show="loading" class="text-gray-500">Loading…</div>
    <div x-show="!loading && papers.length === 0" class="text-gray-500">
        No past papers for this subject yet. Upload one from the Upload page.
    </div>

    <template x-for="p in papers" :key="p.id">
        <div class="border rounded-lg mb-3 bg-white shadow-sm">
            <div class="flex items-center justify-between p-3">
                <button @click="toggle(p.id)" class="flex items-center gap-2 text-left">
                    <span x-text="expanded[p.id] ? '▼' : '▶'"></span>
                    <span class="font-semibold" x-text="paperLabel(p)"></span>
                    <span class="text-sm text-gray-500"
                          x-text="`${p.question_count} questions · ${p.figure_count} figures`"></span>
                </button>
                <button @click="deletePaper(p)"
                        class="text-red-600 text-sm hover:underline">Delete paper</button>
            </div>

            <div x-show="expanded[p.id]" class="border-t p-3 space-y-3">
                <!-- Bulk tag bar -->
                <div class="flex flex-wrap items-center gap-2 bg-gray-50 p-2 rounded"
                     x-show="selectedIds(p.id).length">
                    <span class="text-sm" x-text="`${selectedIds(p.id).length} selected`"></span>
                    <select x-model="bulkCategoryId" @change="loadSubcats(bulkCategoryId)"
                            class="border rounded px-2 py-1 text-sm">
                        <option value="">— category —</option>
                        <template x-for="c in categories" :key="c.id">
                            <option :value="c.id" x-text="c.name"></option>
                        </template>
                    </select>
                    <select x-model="bulkSubcategoryId" class="border rounded px-2 py-1 text-sm"
                            x-show="bulkCategoryId">
                        <option value="">— subcategory —</option>
                        <template x-for="sc in (subcategories[bulkCategoryId] || [])" :key="sc.id">
                            <option :value="sc.id" x-text="sc.name"></option>
                        </template>
                    </select>
                    <button @click="applyBulkTag(p.id)"
                            class="bg-indigo-600 text-white px-3 py-1 rounded text-sm">Apply tag</button>
                </div>

                <button @click="toggleAll(p.id)" class="text-sm text-indigo-600">Select all</button>

                <template x-for="q in (questions[p.id] || [])" :key="q.id">
                    <div class="border rounded p-2">
                        <div class="flex items-start gap-2">
                            <input type="checkbox" x-model="selected[q.id]" class="mt-1">
                            <div class="flex-1">
                                <div class="text-xs text-gray-500" x-text="q.question_ref || ''"></div>

                                <!-- View mode -->
                                <div x-show="editing !== q.id">
                                    <div class="whitespace-pre-wrap" x-text="q.question_text"></div>
                                    <details class="mt-1">
                                        <summary class="text-sm text-indigo-600 cursor-pointer">Answer</summary>
                                        <div class="whitespace-pre-wrap text-sm text-gray-700"
                                             x-text="q.answer_text || '(no answer)'"></div>
                                    </details>
                                    <img x-show="q.image_filename" :src="`/images/${q.image_filename}`"
                                         class="mt-2 max-h-40 rounded border" alt="figure">
                                    <div class="mt-2 flex flex-wrap items-center gap-2">
                                        <select class="border rounded px-1 py-0.5 text-xs"
                                                @change="loadSubcats($event.target.value); tagOne(q, $event.target.value, '')">
                                            <option value="">— category —</option>
                                            <template x-for="c in categories" :key="c.id">
                                                <option :value="c.id" :selected="q.category_id === c.id"
                                                        x-text="c.name"></option>
                                            </template>
                                        </select>
                                        <button @click="startEdit(q)" class="text-xs text-indigo-600">Edit</button>
                                        <button @click="deleteQuestion(p.id, q)" class="text-xs text-red-600">Delete</button>
                                    </div>
                                </div>

                                <!-- Edit mode -->
                                <div x-show="editing === q.id" class="space-y-1">
                                    <input x-model="editForm.question_ref" placeholder="ref (e.g. 1a)"
                                           class="border rounded px-2 py-1 text-sm w-32">
                                    <textarea x-model="editForm.question_text" rows="2"
                                              class="border rounded px-2 py-1 text-sm w-full"></textarea>
                                    <textarea x-model="editForm.answer_text" rows="2"
                                              class="border rounded px-2 py-1 text-sm w-full"></textarea>
                                    <div class="flex gap-2">
                                        <button @click="saveEdit(p.id, q)"
                                                class="bg-green-600 text-white px-3 py-1 rounded text-sm">Save</button>
                                        <button @click="editing = null"
                                                class="px-3 py-1 rounded text-sm border">Cancel</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </template>
            </div>
        </div>
    </template>
</div>
```

- [ ] **Step 4: Verify manually**

Start the server (`python run.py`) or use the configured preview. Log in, open `#past-papers`. Confirm:
- Subject selector lists subjects; switching reloads papers.
- Past-paper cards show label + counts; KO batches do not appear.
- Expanding a paper lists its questions in reading order with answers and figure thumbnails (tables and diagrams both show).
- Edit a question's text/ref and save (reload to confirm persistence).
- Delete a question; delete a paper (with confirm).
- Tag one question via its dropdown; select several + bulk-tag.

If `q.image_filename` is not present on the question payload, confirm `list_questions`
already returns `image_filename` (it does — `LEFT JOIN images`), so thumbnails work.

- [ ] **Step 5: Commit**

```bash
git add frontend/pages/past-papers.html frontend/js/router.js frontend/index.html
git commit -m "feat: add past-paper library page with browse, edit, delete, tagging"
```

---

## Self-Review

**Spec coverage (Phase 2a):**
- `GET /past-papers` with counts → Task 3. ✓
- `question_source` filter on `list_questions` → Task 1. ✓
- `DELETE /past-papers/{batch_id}` with cascade + ownership/type checks → Task 4. ✓
- `POST /past-papers/tag` (per-question + bulk, validation, null-clear) → Task 5. ✓
- `question_ref` editable via PUT → Task 2. ✓
- Library page: subject selector, paper cards, expand→questions in reading order,
  figure thumbnails, edit modal, delete question/paper, per-question + bulk tag,
  route + nav → Task 6. ✓
- Ordering by `(page_number, id)` → inherited from `list_questions` (unchanged ORDER BY). ✓
- Empty states → Task 6 markup. ✓

**Placeholder scan:** No TBD/TODO; every backend step has complete code + exact commands;
the page is provided in full. ✓

**Type consistency:** `TagRequest` fields match the JSON the page posts
(`question_ids`/`category_id`/`subcategory_id`); `QuestionUpdate.question_ref` matches the
edit form; `list_past_papers` returns `figure_count`/`question_count` consumed by
`paperLabel`/card markup; `image_filename` (from `list_questions`) matches the
`<img :src>`; API paths are all under `/api`. ✓
