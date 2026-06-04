# Upload History Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show upload options (category, subcategory, source type, batch type, tier) as chip tags on the history page, and add a "See previous uploads →" link on the upload form.

**Architecture:** Three-file change — extend the `/costs/history` SQL query to join categories/subcategories and return new fields, update the history card template to render option chips, and add a single link to the upload form. No new DB migrations needed; all columns already exist.

**Tech Stack:** FastAPI (Python), SQLite, Alpine.js, Tailwind CSS

---

## File Map

| File | Change |
|------|--------|
| `backend/routers/costs.py` | Extend `GET /costs/history` SELECT + add LEFT JOINs |
| `frontend/pages/upload-history.html` | Add chips row inside the `x-for` card loop |
| `frontend/pages/upload.html` | Add link below the submit button |
| `tests/test_costs.py` | New file — tests for the updated history endpoint |

---

## Task 1: Extend `/costs/history` backend query

**Files:**
- Modify: `backend/routers/costs.py:71-93`
- Create: `tests/test_costs.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_costs.py`:

```python
import sqlite3
import pytest
from tests.conftest import _insert_user


def _make_subject(db, name="Biology"):
    cur = db.execute("INSERT INTO subjects (name, icon, color) VALUES (?, '🧬', '#000')", (name,))
    db.commit()
    return cur.lastrowid


def _make_category(db, subject_id, name="Cells"):
    cur = db.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    )
    db.commit()
    return cur.lastrowid


def _make_subcategory(db, category_id, name="Mitosis"):
    cur = db.execute(
        "INSERT INTO subcategories (category_id, name) VALUES (?, ?)", (category_id, name)
    )
    db.commit()
    return cur.lastrowid


def _make_batch(db, user_id, subject_id, category_id=None, subcategory_id=None,
                batch_type="knowledge_organiser", source_type="pdf",
                is_handwritten=0, tier=None):
    cur = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, subcategory_id, filename, pdf_path,
            page_start, page_end, status, batch_type, source_type, is_handwritten, tier)
           VALUES (?, ?, ?, ?, 'test.pdf', 'batch_1.pdf', 1, 5, 'completed', ?, ?, ?, ?)""",
        (user_id, subject_id, category_id, subcategory_id,
         batch_type, source_type, is_handwritten, tier),
    )
    db.commit()
    return cur.lastrowid


def test_history_returns_new_fields(client, db_conn):
    """GET /costs/history returns batch_type, source_type, is_handwritten, tier,
    category_name, and subcategory_name."""
    uid, token = _insert_user(db_conn, "histuser")
    sid = _make_subject(db_conn)
    cat_id = _make_category(db_conn, sid)
    sub_id = _make_subcategory(db_conn, cat_id)
    _make_batch(
        db_conn, uid, sid,
        category_id=cat_id, subcategory_id=sub_id,
        batch_type="past_paper", source_type="pdf",
        is_handwritten=0, tier="Foundation",
    )

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    b = data[0]
    assert b["batch_type"] == "past_paper"
    assert b["source_type"] == "pdf"
    assert b["is_handwritten"] == 0
    assert b["tier"] == "Foundation"
    assert b["category_name"] == "Cells"
    assert b["subcategory_name"] == "Mitosis"


def test_history_nulls_when_no_category(client, db_conn):
    """category_name and subcategory_name are None when not set."""
    uid, token = _insert_user(db_conn, "histuser2")
    sid = _make_subject(db_conn, "Physics")
    _make_batch(db_conn, uid, sid)

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    b = resp.json()[0]
    assert b["category_name"] is None
    assert b["subcategory_name"] is None
    assert b["batch_type"] == "knowledge_organiser"
    assert b["source_type"] == "pdf"
    assert b["tier"] is None


def test_history_handwritten_flag(client, db_conn):
    """is_handwritten=1 is returned correctly."""
    uid, token = _insert_user(db_conn, "histuser3")
    sid = _make_subject(db_conn, "Art")
    _make_batch(db_conn, uid, sid, source_type="images", is_handwritten=1)

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    b = resp.json()[0]
    assert b["source_type"] == "images"
    assert b["is_handwritten"] == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/paul/claude-code/RevisionAid
pytest tests/test_costs.py -v
```

Expected: 3 failures — `KeyError` or assertion errors on missing fields.

- [ ] **Step 3: Update the query in `costs.py`**

Replace the `get_cost_history` function body in `backend/routers/costs.py` (lines 77–93):

```python
    batches = db.execute(
        """SELECT b.id, b.filename, b.page_start, b.page_end, b.total_pages,
                  b.processed_pages, b.status, b.is_shared, b.cost_usd,
                  b.created_at, b.completed_at, b.error_message,
                  b.batch_type, b.source_type, b.is_handwritten, b.tier,
                  s.name as subject_name,
                  c.name as category_name,
                  sc.name as subcategory_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as approved_count,
                  (SELECT SUM(au.input_tokens) FROM api_usage au WHERE au.batch_id = b.id) as input_tokens,
                  (SELECT SUM(au.output_tokens) FROM api_usage au WHERE au.batch_id = b.id) as output_tokens,
                  (SELECT COUNT(*) FROM api_usage au WHERE au.batch_id = b.id) as api_calls
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           LEFT JOIN categories c ON c.id = b.category_id
           LEFT JOIN subcategories sc ON sc.id = b.subcategory_id
           WHERE b.user_id = ?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
pytest tests/test_costs.py -v
```

Expected output:
```
tests/test_costs.py::test_history_returns_new_fields PASSED
tests/test_costs.py::test_history_nulls_when_no_category PASSED
tests/test_costs.py::test_history_handwritten_flag PASSED
3 passed
```

- [ ] **Step 5: Run full suite to check for regressions**

```bash
pytest tests/ --tb=short -q
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/costs.py tests/test_costs.py
git commit -m "feat: extend /costs/history to include batch options and category names"
```

---

## Task 2: Add option chips row to upload-history cards

**Files:**
- Modify: `frontend/pages/upload-history.html:85-141`

- [ ] **Step 1: Add a `optionChips` helper method to the Alpine component**

In `upload-history.html`, inside the `Alpine.data('uploadHistoryPage', () => ({` block, add this method after `statusClass`:

```javascript
optionChips(b) {
    const chips = [];
    // Source type
    if (b.is_handwritten) {
        chips.push('Handwritten');
    } else if (b.source_type === 'images') {
        chips.push('Images');
    } else {
        chips.push('PDF');
    }
    // Upload type — only label KOs; past papers already have their own badge
    if (b.batch_type === 'knowledge_organiser') {
        chips.push('Knowledge Organiser');
    }
    // Category / Subcategory
    if (b.category_name && b.subcategory_name) {
        chips.push(b.category_name + ' › ' + b.subcategory_name);
    } else if (b.category_name) {
        chips.push(b.category_name);
    } else if (b.subcategory_name) {
        chips.push(b.subcategory_name);
    }
    // Tier (past papers only)
    if (b.tier) {
        chips.push(b.tier);
    }
    return chips;
},
```

- [ ] **Step 2: Add the chips row inside the card template**

In `upload-history.html`, inside the `<template x-for="b in batches">` card, add this **after** the `<p class="text-sm text-gray-500 mt-0.5">` block (after line ~113, before the error message `<p>`):

```html
<!-- Options chips -->
<div class="flex flex-wrap gap-1 mt-1">
    <template x-for="chip in optionChips(b)" :key="chip">
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600"
              x-text="chip"></span>
    </template>
</div>
```

- [ ] **Step 3: Verify in browser**

Start the dev server if not already running:
```bash
python run.py
```

Navigate to `http://localhost:8000/#upload-history` (or the preview URL). Check that:
- Each card shows the chips row
- A PDF knowledge-organiser upload shows `PDF` and `Knowledge Organiser`
- A past-paper upload does NOT show `Knowledge Organiser`
- Category/subcategory chips appear when set
- Tier chip appears for past papers with a tier set
- Empty values produce no chip (no blank pills)

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/upload-history.html
git commit -m "feat: show upload option chips on history cards"
```

---

## Task 3: Add "See previous uploads →" link to upload form

**Files:**
- Modify: `frontend/pages/upload.html:619-620`

- [ ] **Step 1: Add the link after the submit button**

In `frontend/pages/upload.html`, after the closing `</button>` tag of the submit button (line 619) and before `</form>` (line 620), add:

```html
<p class="text-center mt-3">
    <a href="#upload-history" class="text-sm text-indigo-600 hover:underline">
        See previous uploads →
    </a>
</p>
```

The result should look like:

```html
                </button>
                <p class="text-center mt-3">
                    <a href="#upload-history" class="text-sm text-indigo-600 hover:underline">
                        See previous uploads →
                    </a>
                </p>
            </form>
```

- [ ] **Step 2: Verify in browser**

Navigate to `http://localhost:8000/#upload` (knowledge-organiser tab). Confirm:
- The link appears below the "Upload & Process" button
- Clicking it navigates to `#upload-history`
- The link is also visible when the images tab is selected

- [ ] **Step 3: Commit**

```bash
git add frontend/pages/upload.html
git commit -m "feat: add 'See previous uploads' link to upload form"
```
