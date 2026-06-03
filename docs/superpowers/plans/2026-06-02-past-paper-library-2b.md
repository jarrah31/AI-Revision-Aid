# Past-Paper Library — Phase 2b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** Phase 2a (`docs/superpowers/plans/2026-06-02-past-paper-library-2a.md`) must be merged first — this plan extends `backend/routers/past_papers.py`, `tests/test_past_papers.py`, and `frontend/pages/past-papers.html` created there.

**Goal:** Let the user fix figures on past-paper questions — re-crop a figure by drawing a new bounding box on the original page image, and attach/detach an existing figure.

**Architecture:** Two endpoints added to the existing `past_papers` router: `POST /past-papers/questions/{id}/recrop` (loads the saved full-page PNG, crops a new region, writes a new `images` row, repoints `image_id`) and `PUT /past-papers/questions/{id}/image` (attach/detach). A re-crop modal with a draggable/resizable rectangle is added to the library page (no new JS library). No schema changes.

**Tech Stack:** FastAPI, SQLite, Pillow, Alpine.js v3, pytest. Spec: `docs/superpowers/specs/2026-06-02-past-paper-library-design.md`.

---

## File Structure

- `backend/routers/past_papers.py` — add `recrop_figure` and `set_question_image`. Reuse
  `crop_section_to_bytes` from `backend/services/pdf_processor.py` (crops PNG bytes →
  PNG bytes; no disk side effects).
- `frontend/pages/past-papers.html` — add a re-crop modal + attach/detach controls to the
  Alpine component built in 2a.
- `tests/test_past_papers.py` — add re-crop and attach/detach tests.

### Testing notes (read before writing tests)

`backend/routers/past_papers.py` binds `DATA_DIR` at import (module-level
`DATA_DIR = Path(__file__).parent.parent.parent / "data"`). The autouse `isolated_db`
fixture redirects `backend.database.DATA_DIR`, **not** `past_papers.DATA_DIR`. So re-crop
tests MUST `monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")` and place the
fake full-page PNG under it. `tmp_path` is the same directory `isolated_db` used (it
created `tmp_path/data/images`). Build a real PNG with Pillow so the crop succeeds.

Add this helper near the top of `tests/test_past_papers.py`:

```python
def _write_full_page(data_dir, batch_id, page_number=1, size=(400, 300)):
    """Create a real full-page PNG on disk where the recrop endpoint expects it."""
    from PIL import Image
    d = data_dir / "images" / f"batch_{batch_id}"
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(d / f"page_{page_number}_full.png")
```

---

## Task 1: `POST /past-papers/questions/{id}/recrop`

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_past_papers.py` (ensure `import backend.routers.past_papers as past_papers` is present at the top of the file):

```python
def test_recrop_creates_new_image_and_links(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 40, "bbox_h_pct": 30},
    )
    assert r.status_code == 200
    new_image_id = r.json()["image_id"]
    row = db_conn.execute("SELECT image_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["image_id"] == new_image_id
    img = db_conn.execute("SELECT batch_id FROM images WHERE id=?", (new_image_id,)).fetchone()
    assert img["batch_id"] == batch_id


def test_recrop_does_not_mutate_shared_image(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    """Re-cropping one question must not change the figure of a sibling sharing the image."""
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id, page_number=1)
    q2 = make_question(batch_id, user_id, subject_id, page_number=1)
    shared_image = _add_image(db_conn, batch_id, q1)            # q1 -> shared_image
    db_conn.execute("UPDATE questions SET image_id=? WHERE id=?", (shared_image, q2))
    db_conn.commit()                                            # q2 -> shared_image too
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q1}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 0, "bbox_y_pct": 0, "bbox_w_pct": 50, "bbox_h_pct": 50},
    )
    assert r.status_code == 200
    new_id = r.json()["image_id"]
    assert new_id != shared_image
    # q2 still points at the original shared image
    assert db_conn.execute("SELECT image_id FROM questions WHERE id=?", (q2,)).fetchone()["image_id"] == shared_image


def test_recrop_404_when_full_page_missing(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)
    # no _write_full_page → file is missing

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 40, "bbox_h_pct": 30},
    )
    assert r.status_code == 404


def test_recrop_400_on_invalid_bbox(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 0, "bbox_h_pct": 30},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_past_papers.py -k recrop -v`
Expected: FAIL — `404`/`405` (route does not exist).

- [ ] **Step 3: Add the endpoint**

In `backend/routers/past_papers.py`, add the import and endpoint. Update the
`pdf_processor` import (add `crop_section_to_bytes`) and add Pillow + io:

```python
import io
from PIL import Image
from backend.services.pdf_processor import crop_section_to_bytes
```

Then add:

```python
class RecropRequest(BaseModel):
    bbox_x_pct: float
    bbox_y_pct: float
    bbox_w_pct: float
    bbox_h_pct: float


@router.post("/questions/{question_id}/recrop")
def recrop_figure(
    question_id: int,
    req: RecropRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Re-crop a question's figure from the saved full-page image. Creates a NEW image row
    (never mutates a shared one) and repoints this question's image_id at it."""
    q = db.execute(
        "SELECT id, batch_id, page_number FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    # Validate bbox
    if not (0 <= req.bbox_x_pct <= 100 and 0 <= req.bbox_y_pct <= 100
            and req.bbox_w_pct > 0 and req.bbox_h_pct > 0
            and req.bbox_x_pct + req.bbox_w_pct <= 100.5
            and req.bbox_y_pct + req.bbox_h_pct <= 100.5):
        raise HTTPException(status_code=400, detail="Invalid bounding box")

    full_page = DATA_DIR / "images" / f"batch_{q['batch_id']}" / f"page_{q['page_number']}_full.png"
    if not full_page.exists():
        raise HTTPException(
            status_code=404,
            detail="Original page image unavailable; re-upload the paper to enable re-cropping",
        )

    png_bytes = full_page.read_bytes()
    cropped = crop_section_to_bytes(
        png_bytes, req.bbox_x_pct, req.bbox_y_pct, req.bbox_w_pct, req.bbox_h_pct
    )
    width, height = Image.open(io.BytesIO(cropped)).size

    rel_name = f"batch_{q['batch_id']}/page_{q['page_number']}_recrop_q{question_id}.png"
    out_path = DATA_DIR / "images" / rel_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(cropped)

    cur = db.execute(
        """INSERT INTO images (batch_id, page_number, filename, description,
           crop_x, crop_y, crop_w, crop_h, width, height)
           VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?)""",
        (q["batch_id"], q["page_number"], rel_name,
         req.bbox_x_pct, req.bbox_y_pct, req.bbox_w_pct, req.bbox_h_pct, width, height),
    )
    new_image_id = cur.lastrowid
    db.execute("UPDATE questions SET image_id = ? WHERE id = ?", (new_image_id, question_id))
    db.commit()
    return {"image_id": new_image_id, "filename": rel_name}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_past_papers.py -k recrop -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: add POST /past-papers/questions/{id}/recrop"
```

---

## Task 2: `PUT /past-papers/questions/{id}/image` (attach/detach)

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_past_papers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_past_papers.py`:

```python
def test_detach_image(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    _add_image(db_conn, batch_id, q)

    r = client.put(
        f"/api/past-papers/questions/{q}/image",
        headers=user_headers, json={"image_id": None},
    )
    assert r.status_code == 200
    assert db_conn.execute(
        "SELECT image_id FROM questions WHERE id=?", (q,)
    ).fetchone()["image_id"] is None


def test_attach_image_same_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id)
    q2 = make_question(batch_id, user_id, subject_id)
    image_id = _add_image(db_conn, batch_id, q1)  # an existing figure on this batch

    r = client.put(
        f"/api/past-papers/questions/{q2}/image",
        headers=user_headers, json={"image_id": image_id},
    )
    assert r.status_code == 200
    assert db_conn.execute(
        "SELECT image_id FROM questions WHERE id=?", (q2,)
    ).fetchone()["image_id"] == image_id


def test_attach_image_rejects_cross_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_a = make_batch(user_id, subject_id)
    batch_b = make_batch(user_id, subject_id)
    qa = make_question(batch_a, user_id, subject_id)
    qb = make_question(batch_b, user_id, subject_id)
    foreign_image = _add_image(db_conn, batch_b, qb)  # image on a DIFFERENT batch

    r = client.put(
        f"/api/past-papers/questions/{qa}/image",
        headers=user_headers, json={"image_id": foreign_image},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_past_papers.py -k image -v`
Expected: FAIL — `404`/`405` (route does not exist).

- [ ] **Step 3: Add the endpoint**

Append to `backend/routers/past_papers.py`:

```python
class SetImageRequest(BaseModel):
    image_id: int | None = None


@router.put("/questions/{question_id}/image")
def set_question_image(
    question_id: int,
    req: SetImageRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Attach an existing same-batch figure to a question, or detach (image_id=null)."""
    q = db.execute(
        "SELECT id, batch_id FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    if req.image_id is not None:
        img = db.execute(
            "SELECT batch_id FROM images WHERE id = ?", (req.image_id,)
        ).fetchone()
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        if img["batch_id"] != q["batch_id"]:
            raise HTTPException(status_code=400, detail="Image belongs to a different paper")

    db.execute(
        "UPDATE questions SET image_id = ? WHERE id = ?", (req.image_id, question_id)
    )
    db.commit()
    return {"message": "Image updated", "image_id": req.image_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_past_papers.py -k image -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test file + suite**

Run: `venv/bin/pytest tests/test_past_papers.py -v`
Expected: all PASS.
Run: `venv/bin/pytest tests/ -q`
Expected: no NEW failures (3 pre-existing `tests/test_costs.py` failures excepted).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/past_papers.py tests/test_past_papers.py
git commit -m "feat: add attach/detach figure endpoint for past-paper questions"
```

---

## Task 3: Re-crop modal + attach/detach controls (frontend)

**Files:**
- Modify: `frontend/pages/past-papers.html`

UI task — verify manually. Extend the `pastPapersPage` Alpine component and markup from 2a.

- [ ] **Step 1: Add state + methods to the Alpine component**

In the `Alpine.data('pastPapersPage', ...)` object in `frontend/pages/past-papers.html`,
add to the returned object (alongside existing state):

```javascript
    // Re-crop modal
    recrop: { open: false, q: null, batchId: null, pageImg: '', rect: null, dragging: false, resizing: false, start: null },

    openRecrop(batchId, q) {
        this.recrop = {
            open: true, q, batchId,
            pageImg: `/images/batch_${q.batch_id}/page_${q.page_number}_full.png`,
            rect: { x: 20, y: 20, w: 40, h: 30 },   // percentages
            dragging: false, resizing: false, start: null,
        };
    },
    // Pointer math: convert event to % within the image element
    _pct(e, el) {
        const r = el.getBoundingClientRect();
        return { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 };
    },
    startMove(e, el) { this.recrop.dragging = true; this.recrop.start = this._pct(e, el); },
    startResize(e, el) { e.stopPropagation(); this.recrop.resizing = true; this.recrop.start = this._pct(e, el); },
    onMove(e, el) {
        if (!this.recrop.dragging && !this.recrop.resizing) return;
        const p = this._pct(e, el), s = this.recrop.start, R = this.recrop.rect;
        const dx = p.x - s.x, dy = p.y - s.y;
        if (this.recrop.dragging) {
            R.x = Math.max(0, Math.min(100 - R.w, R.x + dx));
            R.y = Math.max(0, Math.min(100 - R.h, R.y + dy));
        } else {
            R.w = Math.max(5, Math.min(100 - R.x, R.w + dx));
            R.h = Math.max(5, Math.min(100 - R.y, R.h + dy));
        }
        this.recrop.start = p;
    },
    endMove() { this.recrop.dragging = false; this.recrop.resizing = false; },
    async saveRecrop() {
        const R = this.recrop.rect, q = this.recrop.q;
        const res = await API.post(`/api/past-papers/questions/${q.id}/recrop`, {
            bbox_x_pct: R.x, bbox_y_pct: R.y, bbox_w_pct: R.w, bbox_h_pct: R.h,
        });
        q.image_filename = res.filename;   // refresh thumbnail
        q.image_id = res.image_id;
        this.recrop.open = false;
    },
    async detachImage(q) {
        await API.put(`/api/past-papers/questions/${q.id}/image`, { image_id: null });
        q.image_filename = null; q.image_id = null;
    },
```

- [ ] **Step 2: Add the controls to each question (view mode)**

In the question view-mode block (where the `<img>` thumbnail and Edit/Delete buttons are),
add a detach button on the thumbnail and a re-crop/set-figure button:

```html
                                    <div x-show="q.image_filename" class="relative inline-block mt-2">
                                        <img :src="`/images/${q.image_filename}`" class="max-h-40 rounded border" alt="figure">
                                        <button @click="detachImage(q)"
                                                class="absolute top-1 right-1 bg-white/90 text-red-600 rounded-full w-6 h-6 text-xs"
                                                title="Detach figure">✕</button>
                                    </div>
                                    <button @click="openRecrop(p.id, q)" class="text-xs text-indigo-600 block mt-1"
                                            x-text="q.image_filename ? 'Re-crop figure' : 'Set figure'"></button>
```

- [ ] **Step 3: Add the modal markup**

Add once, just inside the root `<div x-data="pastPapersPage" ...>` (e.g. before the closing `</div>`):

```html
    <!-- Re-crop modal -->
    <div x-show="recrop.open" x-cloak
         class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div class="bg-white rounded-lg p-4 max-w-3xl w-full">
            <h3 class="font-semibold mb-2">Re-crop figure — drag the box, drag the corner to resize</h3>
            <div class="relative inline-block select-none"
                 @pointermove="onMove($event, $refs.pageImgEl)" @pointerup="endMove()" @pointerleave="endMove()">
                <img x-ref="pageImgEl" :src="recrop.pageImg" class="max-h-[70vh] block" alt="page"
                     @error="recrop.open = false; alert('Original page image unavailable.')">
                <div class="absolute border-2 border-indigo-500 bg-indigo-500/20 cursor-move"
                     :style="`left:${recrop.rect.x}%;top:${recrop.rect.y}%;width:${recrop.rect.w}%;height:${recrop.rect.h}%`"
                     @pointerdown="startMove($event, $refs.pageImgEl)">
                    <div class="absolute bottom-0 right-0 w-3 h-3 bg-indigo-600 cursor-se-resize"
                         @pointerdown="startResize($event, $refs.pageImgEl)"></div>
                </div>
            </div>
            <div class="mt-3 flex gap-2 justify-end">
                <button @click="recrop.open = false" class="px-3 py-1 rounded border">Cancel</button>
                <button @click="saveRecrop()" class="px-3 py-1 rounded bg-indigo-600 text-white">Save crop</button>
            </div>
        </div>
    </div>
```

Ensure `[x-cloak]{display:none}` exists in the app CSS (it is standard in this project's
Tailwind setup; if missing, add `<style>[x-cloak]{display:none}</style>` to the page).

- [ ] **Step 4: Verify manually**

Start the server / preview, open `#past-papers`, expand a paper with figures. Confirm:
- "Re-crop figure" opens the modal showing the full page; the box drags and resizes; Save
  updates the thumbnail to the new crop.
- A question with a shared figure: re-cropping it does NOT change the sibling's figure.
- "Set figure" appears for a question with no figure and lets you crop one.
- Detach (✕) removes the thumbnail; "Set figure" reappears.
- A question whose paper predates Sub-project 1 (no full-page image) shows the "unavailable"
  alert rather than a broken modal.

- [ ] **Step 5: Commit**

```bash
git add frontend/pages/past-papers.html
git commit -m "feat: add figure re-crop modal and attach/detach controls to library"
```

---

## Self-Review

**Spec coverage (Phase 2b):**
- `POST /recrop` loads full-page image, new image row, repoints `image_id`, 404 on missing
  page, shared image not mutated, bbox validation → Task 1. ✓
- `PUT /questions/{id}/image` attach (same-batch validated) / detach (null) → Task 2. ✓
- Re-crop modal with draggable/resizable rectangle (no new library); attach/detach
  controls; missing-page handling → Task 3. ✓

**Placeholder scan:** No TBD/TODO; backend steps have full code + commands; the frontend
component additions and modal markup are provided in full. ✓

**Type consistency:** `RecropRequest`/`SetImageRequest` fields match the JSON the page
posts (`bbox_*_pct`; `image_id`); the recrop response (`{image_id, filename}`) matches the
`saveRecrop` consumer (`q.image_filename = res.filename`); `crop_section_to_bytes` is
called with the signature defined in `backend/services/pdf_processor.py`
(`png_bytes, x, y, w, h`); `DATA_DIR` is the module-level name monkeypatched in tests. ✓
