# Paper-level Subject + Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a past paper's Subject + Category editable at the paper (batch) level from the Past Paper page, cascade the change to the paper's questions, show the category in each paper summary, and remove per-question category tagging.

**Architecture:** Category becomes a batch-level property (`upload_batches.category_id`) that cascades down to `questions.subject_id`/`category_id` (and clears `questions.subcategory_id`) so quiz/coverage filtering — which reads the per-question columns — keeps working. A new `PATCH /past-papers/{id}` endpoint does the reclassify+cascade; the old `POST /past-papers/tag` endpoint and the per-question/bulk-tag UI are removed. A one-time backfill seeds batch categories from existing question tags. Export/import needs no change — `serialize_paper` already emits the batch category, so it round-trips once the column is populated.

**Tech Stack:** FastAPI, SQLite (raw `sqlite3`), Alpine.js v3 (no build step), pytest.

---

## File Structure

- `backend/routers/past_papers.py` — add `category_id`/`category_name` to the list query; add `PATCH /{batch_id}`; remove `POST /tag` + `TagRequest`.
- `backend/database.py` — add `backfill_past_paper_categories(db)` helper and call it from `init_db()` after the column-migration loop.
- `frontend/pages/past-papers.html` — category pill + edit control on each paper summary; remove per-question category `<select>` and the bulk-tag toolbar and their Alpine state/methods.
- `tests/test_past_papers.py` — remove `/tag` tests; add list-returns-category test and `PATCH` tests.
- `tests/test_database.py` (new) — backfill migration tests.
- `tests/test_paper_archive.py` — assert category survives an export → import round-trip.

---

## Task 1: List endpoint returns the paper's category

**Files:**
- Modify: `backend/routers/past_papers.py` (the `list_past_papers` query, ~lines 28-39)
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_past_papers.py` (the file already has `_make_past_paper`, `_make_category`, and uses the `client`/`db_conn`/`make_subject` fixtures; mirror the existing `test_list_past_papers` setup for how a subject + batch is created):

```python
def test_list_past_papers_includes_category(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "catlist")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'batch_1.pdf', 1, 2, 'completed', 'past_paper')""",
        (uid, subject_id, cat),
    ).lastrowid
    db_conn.commit()

    resp = client.get(
        f"/api/past-papers?subject_id={subject_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["id"] == bid
    assert row["category_id"] == cat
    assert row["category_name"] == "Cells"
```

Note: confirm `_insert_user` is imported at the top of the file (the other tests use it). If not, add `from tests.conftest import _insert_user`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_past_papers.py::test_list_past_papers_includes_category -v`
Expected: FAIL — `KeyError: 'category_name'` (the response row has no such key).

- [ ] **Step 3: Add category to the list query**

In `backend/routers/past_papers.py`, replace the `list_past_papers` SELECT so it LEFT JOINs categories and selects the two new fields. The full statement becomes:

```python
    rows = db.execute(
        """SELECT b.id, b.subject_id, b.filename, b.exam_board, b.exam_year,
                  b.paper_number, b.tier, b.created_at, b.category_id,
                  c.name AS category_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) AS question_count,
                  (SELECT COUNT(*) FROM questions q
                     WHERE q.batch_id = b.id AND q.image_id IS NOT NULL) AS figure_count
           FROM upload_batches b
           LEFT JOIN categories c ON c.id = b.category_id
           WHERE b.user_id = ? AND b.subject_id = ? AND b.batch_type = 'past_paper'
           ORDER BY b.exam_year DESC, b.created_at DESC""",
        (user["id"], subject_id),
    ).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_past_papers.py::test_list_past_papers_includes_category -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: include category on past-paper list rows"
```

---

## Task 2: PATCH endpoint to reclassify a paper (with cascade), remove /tag

**Files:**
- Modify: `backend/routers/past_papers.py` (remove `TagRequest` + `tag_questions` ~lines 125-175; add `ReclassifyRequest` + `PATCH /{batch_id}`)
- Test: `tests/test_past_papers.py` (remove the six `/tag` tests; add PATCH tests)

- [ ] **Step 1: Write the failing tests**

In `tests/test_past_papers.py`, delete these six tests and only these:
`test_tag_questions_bulk`, `test_tag_clears_with_null`,
`test_tag_rejects_cross_subject_category`,
`test_tag_rejects_subcategory_not_under_category`,
`test_tag_with_valid_subcategory`, `test_tag_ignores_questions_not_owned`.
Keep the `_make_category` helper (reused below). Remove `_make_subcategory` only if no remaining test references it (grep first: `grep -n _make_subcategory tests/test_past_papers.py`).

Then add these tests. Helper to build a past paper with two questions:

```python
def _paper_with_questions(db_conn, uid, subject_id, category_id=None, n=2):
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'batch_1.pdf', 1, 2, 'completed', 'past_paper')""",
        (uid, subject_id, category_id),
    ).lastrowid
    qids = []
    for i in range(n):
        qids.append(db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved, question_source)
               VALUES (?, ?, ?, 1, 'q', 'a', 1, 'past_paper')""",
            (bid, uid, subject_id),
        ).lastrowid)
    db_conn.commit()
    return bid, qids


def test_patch_assigns_existing_category_and_cascades(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch1")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id, "category_id": cat},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_id"] == cat
    assert body["category_name"] == "Cells"
    # batch updated
    b = db_conn.execute("SELECT category_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert b["category_id"] == cat
    # cascade to every question
    for qid in qids:
        q = db_conn.execute(
            "SELECT subject_id, category_id, subcategory_id FROM questions WHERE id=?", (qid,)
        ).fetchone()
        assert q["subject_id"] == subject_id
        assert q["category_id"] == cat
        assert q["subcategory_id"] is None


def test_patch_creates_new_category_by_name(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch2")
    subject_id = make_subject(name="Biology")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id, "new_category_name": "  Genetics  "},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_name"] == "Genetics"  # trimmed
    new_cat = db_conn.execute(
        "SELECT id FROM categories WHERE subject_id=? AND name='Genetics'", (subject_id,)
    ).fetchone()
    assert new_cat is not None
    assert body["category_id"] == new_cat["id"]


def test_patch_reassigns_subject_and_cascades(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch3")
    subj_a = make_subject(name="Biology")
    subj_b = make_subject(name="Chemistry")
    bid, qids = _paper_with_questions(db_conn, uid, subj_a)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subj_b, "new_category_name": "Bonding"},
    )
    assert resp.status_code == 200
    b = db_conn.execute("SELECT subject_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert b["subject_id"] == subj_b
    # new category lives under the NEW subject
    cat = db_conn.execute(
        "SELECT subject_id FROM categories WHERE id=?", (resp.json()["category_id"],)
    ).fetchone()
    assert cat["subject_id"] == subj_b
    # questions moved subject too
    for qid in qids:
        q = db_conn.execute("SELECT subject_id FROM questions WHERE id=?", (qid,)).fetchone()
        assert q["subject_id"] == subj_b


def test_patch_clears_category_when_none_given(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch4")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id, category_id=cat)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 200
    assert resp.json()["category_id"] is None
    q = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (qids[0],)).fetchone()
    assert q["category_id"] is None


def test_patch_rejects_category_from_other_subject(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch5")
    subj_a = make_subject(name="Biology")
    subj_b = make_subject(name="Chemistry")
    foreign_cat = _make_category(db_conn, subj_b, name="Bonding")
    bid, _ = _paper_with_questions(db_conn, uid, subj_a)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subj_a, "category_id": foreign_cat},
    )
    assert resp.status_code == 400


def test_patch_404_for_other_users_paper(client, db_conn, make_subject):
    owner, _ = _insert_user(db_conn, "patchowner")
    _, other_token = _insert_user(db_conn, "patchother")
    subject_id = make_subject(name="Biology")
    bid, _ = _paper_with_questions(db_conn, owner, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 404


def test_patch_404_for_ko_batch(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patchko")
    subject_id = make_subject(name="Biology")
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end,
            status, batch_type)
           VALUES (?, ?, 'ko.pdf', 'batch_1.pdf', 1, 2, 'completed', 'knowledge_organiser')""",
        (uid, subject_id),
    ).lastrowid
    db_conn.commit()

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_past_papers.py -k "patch" -v`
Expected: FAIL — 405 Method Not Allowed / 404 (no PATCH route yet).

- [ ] **Step 3: Remove the /tag endpoint and add the PATCH endpoint**

In `backend/routers/past_papers.py`, delete the `TagRequest` class and the entire `tag_questions` function (the `@router.post("/tag")` block). Then add this new endpoint. Place it after `delete_past_paper` (it shares the `/{batch_id}` path family; method differs so there's no routing conflict with `/export`/`/import`):

```python
class ReclassifyRequest(BaseModel):
    subject_id: int
    category_id: int | None = None
    new_category_name: str | None = None


@router.patch("/{batch_id}")
def reclassify_past_paper(
    batch_id: int,
    req: ReclassifyRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Set a past paper's subject + category at the paper level and cascade the
    classification down to all of its questions (clearing subcategory). Either
    pick an existing category_id under the subject, or create one by name via
    new_category_name (which takes precedence)."""
    batch = db.execute(
        "SELECT id FROM upload_batches WHERE id = ? AND user_id = ? AND batch_type = 'past_paper'",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Past paper not found")

    subject = db.execute(
        "SELECT id FROM subjects WHERE id = ?", (req.subject_id,)
    ).fetchone()
    if not subject:
        raise HTTPException(status_code=400, detail="Subject not found")

    # Resolve category: new_category_name (get-or-create) wins over category_id.
    category_id = None
    name = (req.new_category_name or "").strip()
    if name:
        existing = db.execute(
            "SELECT id FROM categories WHERE subject_id = ? AND name = ?",
            (req.subject_id, name),
        ).fetchone()
        if existing:
            category_id = existing["id"]
        else:
            cur = db.execute(
                "INSERT INTO categories (subject_id, name) VALUES (?, ?)",
                (req.subject_id, name),
            )
            category_id = cur.lastrowid
    elif req.category_id is not None:
        cat = db.execute(
            "SELECT subject_id FROM categories WHERE id = ?", (req.category_id,)
        ).fetchone()
        if not cat or cat["subject_id"] != req.subject_id:
            raise HTTPException(
                status_code=400, detail="Category does not belong to the chosen subject"
            )
        category_id = req.category_id

    # Update the batch, then cascade to its questions.
    db.execute(
        "UPDATE upload_batches SET subject_id = ?, category_id = ?, subcategory_id = NULL WHERE id = ?",
        (req.subject_id, category_id, batch_id),
    )
    db.execute(
        "UPDATE questions SET subject_id = ?, category_id = ?, subcategory_id = NULL WHERE batch_id = ?",
        (req.subject_id, category_id, batch_id),
    )
    db.commit()

    category_name = None
    if category_id is not None:
        row = db.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        category_name = row["name"] if row else None

    return {
        "subject_id": req.subject_id,
        "category_id": category_id,
        "category_name": category_name,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_past_papers.py -k "patch" -v`
Expected: PASS (all seven patch tests).

- [ ] **Step 5: Run the full past-papers + costs suites for regressions**

Run: `pytest tests/test_past_papers.py tests/test_costs.py -q`
Expected: PASS, no remaining references to `/tag`.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: PATCH /past-papers/{id} reclassify + cascade; remove /tag"
```

---

## Task 3: Backfill batch categories from existing question tags

**Files:**
- Modify: `backend/database.py` (add `backfill_past_paper_categories(db)`, call from `init_db()`)
- Test: `tests/test_database.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_database.py`:

```python
from backend import database
from tests.conftest import _insert_user


def _subject(db, name="Biology"):
    return db.execute(
        "INSERT INTO subjects (name, icon, color) VALUES (?, '🧬', '#000')", (name,)
    ).lastrowid


def _category(db, subject_id, name):
    return db.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    ).lastrowid


def _paper(db, user_id, subject_id, category_id=None):
    return db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'b.pdf', 1, 2, 'completed', 'past_paper')""",
        (user_id, subject_id, category_id),
    ).lastrowid


def _q(db, batch_id, user_id, subject_id, category_id):
    db.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, category_id, page_number, question_text,
            answer_text, approved, question_source)
           VALUES (?, ?, ?, ?, 1, 'q', 'a', 1, 'past_paper')""",
        (batch_id, user_id, subject_id, category_id),
    )


def test_backfill_sets_batch_category_to_most_common(db_conn):
    # db_conn fixture already ran init_db via isolated_db; build a NULL-category
    # paper whose questions are mostly tagged "Cells".
    uid, _ = _insert_user(db_conn, "bf")
    sid = _subject(db_conn)
    cells = _category(db_conn, sid, "Cells")
    genes = _category(db_conn, sid, "Genetics")
    bid = _paper(db_conn, uid, sid, category_id=None)
    _q(db_conn, bid, uid, sid, cells)
    _q(db_conn, bid, uid, sid, cells)
    _q(db_conn, bid, uid, sid, genes)
    _q(db_conn, bid, uid, sid, None)
    db_conn.commit()

    database.backfill_past_paper_categories(db_conn)
    db_conn.commit()

    row = db_conn.execute("SELECT category_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert row["category_id"] == cells


def test_backfill_idempotent_and_skips_untagged(db_conn):
    uid, _ = _insert_user(db_conn, "bf2")
    sid = _subject(db_conn, "Physics")
    cat = _category(db_conn, sid, "Forces")
    # already-categorised paper must not change
    set_bid = _paper(db_conn, uid, sid, category_id=cat)
    other = _category(db_conn, sid, "Energy")
    _q(db_conn, set_bid, uid, sid, other)
    # untagged paper (no question categories) stays NULL
    null_bid = _paper(db_conn, uid, sid, category_id=None)
    _q(db_conn, null_bid, uid, sid, None)
    db_conn.commit()

    database.backfill_past_paper_categories(db_conn)
    database.backfill_past_paper_categories(db_conn)  # idempotent
    db_conn.commit()

    assert db_conn.execute(
        "SELECT category_id FROM upload_batches WHERE id=?", (set_bid,)
    ).fetchone()["category_id"] == cat
    assert db_conn.execute(
        "SELECT category_id FROM upload_batches WHERE id=?", (null_bid,)
    ).fetchone()["category_id"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`
Expected: FAIL — `AttributeError: module 'backend.database' has no attribute 'backfill_past_paper_categories'`.

- [ ] **Step 3: Add the backfill function and call it from init_db**

In `backend/database.py`, add this module-level function (place it near the bottom, after `init_db`):

```python
def backfill_past_paper_categories(db):
    """One-time data migration: for past-paper batches with no batch-level
    category, set it to the most common non-NULL category among the batch's
    own questions. Idempotent — only touches batches where category_id IS NULL.
    Batches whose questions are all untagged stay NULL."""
    db.execute(
        """
        UPDATE upload_batches
           SET category_id = (
               SELECT q.category_id
                 FROM questions q
                WHERE q.batch_id = upload_batches.id
                  AND q.category_id IS NOT NULL
                GROUP BY q.category_id
                ORDER BY COUNT(*) DESC, q.category_id ASC
                LIMIT 1
           )
         WHERE batch_type = 'past_paper'
           AND category_id IS NULL
           AND EXISTS (
               SELECT 1 FROM questions q2
                WHERE q2.batch_id = upload_batches.id
                  AND q2.category_id IS NOT NULL
           )
        """
    )
```

Then, inside `init_db()`, immediately before the final `db.close()`, call it:

```python
    backfill_past_paper_categories(db)
    db.commit()

    db.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_database.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_database.py
git commit -m "feat: backfill past-paper batch categories from question tags"
```

---

## Task 4: Assert category survives export → import round-trip

**Files:**
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Find the existing round-trip test**

Run: `grep -n "def test_.*round\|import_paper\|build_archive\|_make_past_paper\|_make_category" tests/test_paper_archive.py`
Identify the end-to-end export→import test (it builds an archive then imports it). Read its setup helpers.

- [ ] **Step 2: Add a category-preserving round-trip test**

Append to `tests/test_paper_archive.py`. Use the file's existing helpers (`_make_past_paper`, `_add_image`, `_zip_from`, etc. — match their real signatures observed in Step 1). The shape of the test:

```python
def test_category_survives_export_import(db_conn, make_subject):
    # Source paper WITH a batch-level category.
    uid, _ = _insert_user(db_conn, "rtcat")
    subject_id = make_subject(name="Biology")
    cat = db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, 'Cells')", (subject_id,)
    ).lastrowid
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type, exam_board, exam_year, paper_number, tier)
           VALUES (?, ?, ?, 'bio.pdf', 'b.pdf', 1, 2, 'completed', 'past_paper',
                   'AQA', 2023, 'Paper 1', 'Foundation')""",
        (uid, subject_id, cat),
    ).lastrowid
    db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, category_id, page_number, question_text,
            answer_text, approved, question_source)
           VALUES (?, ?, ?, ?, 1, 'q', 'a', 1, 'past_paper')""",
        (bid, uid, subject_id, cat),
    )
    db_conn.commit()

    blob, _name = paper_archive.build_archive([bid], uid, db_conn)
    parsed = paper_archive.read_archive(blob)

    # Import as a DIFFERENT user so the duplicate guard doesn't trip.
    uid2, _ = _insert_user(db_conn, "rtcat2")
    result = paper_archive.import_paper(parsed["papers"][0], uid2, db_conn)
    assert result["status"] == "imported"

    new_bid = result["batch_id"]
    row = db_conn.execute(
        """SELECT c.name AS category_name
             FROM upload_batches b JOIN categories c ON c.id = b.category_id
            WHERE b.id = ?""",
        (new_bid,),
    ).fetchone()
    assert row["category_name"] == "Cells"
```

Confirm `paper_archive` and `_insert_user` are imported at the top of the file (other tests in it already use them; if not, add the imports).

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_paper_archive.py::test_category_survives_export_import -v`
Expected: PASS (the serialize/import code already carries `category_name`; this locks the behaviour in).

- [ ] **Step 4: Run the whole archive suite**

Run: `pytest tests/test_paper_archive.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_paper_archive.py
git commit -m "test: category survives past-paper export/import round-trip"
```

---

## Task 5: Frontend — category pill + edit control; remove per-question/bulk tagging

**Files:**
- Modify: `frontend/pages/past-papers.html`

This task has no automated test (no JS test harness in the repo). Verify in the running app / preview after implementing.

- [ ] **Step 1: Remove dead tagging state and methods**

In the `Alpine.data('pastPapersPage', ...)` object, delete these properties:
`categories` stays (still needed for the edit control), but remove
`subcategories: {}`, `selected: {}`, `bulkCategoryId: ''`, `bulkSubcategoryId: ''`.
Delete these methods entirely: `loadSubcats`, `tagOne`, `selectedIds`,
`toggleAll`, `applyBulkTag`. In `loadSubject()` remove the `this.selected = {}`
reset (the property no longer exists).

- [ ] **Step 2: Add edit state + save method**

Add to the component state (near `editing: null, editForm: {}`):

```javascript
    editingPaper: null,      // paper id whose classification is being edited
    paperForm: { subject_id: null, categoryText: '' },
    catNames: [],            // category names for the chosen subject (datalist)
```

Add these methods to the component:

```javascript
    async startPaperEdit(p) {
        this.editingPaper = p.id;
        this.paperForm = {
            subject_id: p.subject_id || this.subjectId,
            categoryText: p.category_name || '',
        };
        await this.loadCatNames(this.paperForm.subject_id);
    },
    async loadCatNames(subjectId) {
        const cats = await API.get(`/categories?subject_id=${subjectId}`);
        this.catNames = cats.map(c => c.name);
    },
    onPaperSubjectChange() {
        // Switching subject invalidates the typed category.
        this.paperForm.categoryText = '';
        this.loadCatNames(this.paperForm.subject_id);
    },
    async savePaperEdit(p) {
        const name = (this.paperForm.categoryText || '').trim();
        const body = { subject_id: Number(this.paperForm.subject_id) };
        if (name) body.new_category_name = name;   // backend get-or-creates by name
        await API.patch(`/past-papers/${p.id}`, body);
        this.editingPaper = null;
        await this.loadSubject();
    },
```

Note: `API.patch` — confirm the helper exists in `frontend/js/` (the page already
uses `API.get/post/put/del`). If there is no `patch`, add one mirroring `put` in
the API helper module, or use `API.put` and change the backend route to
`@router.put("/{batch_id}")` for consistency. Pick one and keep verb + helper in
sync. (Check: `grep -n "patch\|put:" frontend/js/api.js` or wherever `API` is defined.)

- [ ] **Step 3: Add the category pill + Edit button to the paper summary**

In the summary header `<div class="flex items-center gap-2">` (the one holding
the re-detect and Delete buttons, ~line 294), add a category pill + edit toggle
before the re-detect button:

```html
                    <template x-if="editingPaper !== p.id">
                        <div class="flex items-center gap-1.5">
                            <span class="text-xs px-2 py-0.5 rounded-full"
                                  :class="p.category_name ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'"
                                  x-text="p.category_name ? ('Category: ' + p.category_name) : 'Uncategorised'"></span>
                            <button @click="startPaperEdit(p)" class="text-xs text-indigo-600 hover:underline">Edit</button>
                        </div>
                    </template>
```

- [ ] **Step 4: Add the inline edit panel**

Immediately after the summary header row (after the closing `</div>` of the
`flex items-center justify-between p-3` block, before the `x-show="expanded[p.id]"`
questions block, ~line 310), add:

```html
            <div x-show="editingPaper === p.id" class="border-t p-3 bg-gray-50 flex flex-wrap items-end gap-3">
                <label class="text-sm">
                    <span class="block text-xs text-gray-500 mb-1">Subject</span>
                    <select x-model="paperForm.subject_id" @change="onPaperSubjectChange()"
                            class="border rounded px-2 py-1 text-sm">
                        <template x-for="s in subjects" :key="s.id">
                            <option :value="s.id" :selected="s.id === paperForm.subject_id" x-text="s.name"></option>
                        </template>
                    </select>
                </label>
                <label class="text-sm">
                    <span class="block text-xs text-gray-500 mb-1">Category (pick or type new)</span>
                    <input x-model="paperForm.categoryText" list="catnames-list"
                           placeholder="e.g. Cells" class="border rounded px-2 py-1 text-sm">
                    <datalist id="catnames-list">
                        <template x-for="n in catNames" :key="n">
                            <option :value="n"></option>
                        </template>
                    </datalist>
                </label>
                <div class="flex gap-2">
                    <button @click="savePaperEdit(p)"
                            class="bg-green-600 text-white px-3 py-1 rounded text-sm">Save</button>
                    <button @click="editingPaper = null"
                            class="px-3 py-1 rounded text-sm border">Cancel</button>
                </div>
            </div>
```

Note: the Subject `<select>` defaults to `paperForm.subject_id`, which
`startPaperEdit` seeds from `p.subject_id`. That field is already returned by the
list endpoint (added to the `list_past_papers` SELECT in Task 1).

- [ ] **Step 5: Remove the bulk-tag toolbar and per-question category select**

Delete the bulk-tag toolbar block (the `<div class="flex flex-wrap items-center gap-2 bg-gray-50 p-2 rounded" x-show="selectedIds(p.id).length">` … through its closing `</div>`, ~lines 312-331), the standalone `<button @click="toggleAll(p.id)">Select all</button>` (~line 333), and the per-question checkbox `<input type="checkbox" x-model="selected[q.id]">` (~line 338) plus the per-question category `<select>` block inside the question's action row (~lines 360-367). Keep the Edit/Delete buttons and everything else in the question row.

- [ ] **Step 6: Verify in the preview app**

Start/refresh the preview. Then:
- Confirm each past paper shows a category pill (or "Uncategorised") and an Edit button.
- Click Edit → choose an existing category from the datalist → Save → pill updates.
- Click Edit → type a brand-new category name → Save → pill shows it; reload page → still there.
- Click Edit → change Subject → the paper leaves the current subject's list after save.
- Confirm the per-question category dropdown and the bulk "Apply tag" toolbar are gone, and question Edit/Delete/figure/recrop still work.

Use `mcp__Claude_Preview__preview_eval` to read `Alpine.$data` on the `[x-data]` element to confirm `editingPaper`, `paperForm`, and `catNames` behave, rather than relying on screenshots alone.

- [ ] **Step 7: Commit**

```bash
git add frontend/pages/past-papers.html backend/routers/past_papers.py
git commit -m "feat: paper-level category pill + subject/category edit; drop per-question tagging UI"
```

---

## Task 6: Full-suite regression + wrap-up

- [ ] **Step 1: Run the whole test suite**

Run: `pytest tests/ --tb=short -q`
Expected: PASS, with the six `/tag` tests gone and the new past-papers, database, and archive tests present.

- [ ] **Step 2: Grep for leftover references**

Run: `grep -rn "past-papers/tag\|applyBulkTag\|tagOne\|bulkCategoryId\|TagRequest\|_make_subcategory" backend/ frontend/ tests/`
Expected: no results (or only `_make_subcategory` if still used by a surviving test — otherwise remove it).

- [ ] **Step 3: Final commit if anything was cleaned up**

```bash
git add -A
git commit -m "chore: remove leftover per-question tagging references"
```

---

## Notes for the implementer

- **Verb/helper sync (Task 5 Step 2):** the backend route in Task 2 is `PATCH`. The frontend must call the same verb. If `API.patch` doesn't exist and you don't want to add it, switch the backend decorator to `@router.put("/{batch_id}")` and the test client calls to `client.put(...)`. Keep all three (route, test, frontend) on the same verb.
- **Cascade is the whole point:** quiz/coverage read `questions.subject_id`/`questions.category_id` (see `backend/routers/quiz.py::_cat_subcat_filter`). The PATCH endpoint must update the questions, not just the batch — the tests in Task 2 enforce this.
- **Subjects are global** in this schema (no `user_id` column); only validate existence.
- **Don't commit `data/revisionaid.db`.**
