# Sub-project 2 — Past-Paper Library

**Date:** 2026-06-02
**Status:** Approved design, ready for implementation plan(s)

## Context

This is sub-project 2 of three (see
`docs/superpowers/specs/2026-06-02-past-paper-capture-foundation-design.md` for the
decomposition):

- **Sub-project 1 (done, merged):** capture foundation — figures captured on past-paper
  uploads, full corpus matchable.
- **Sub-project 2 (this doc):** a dedicated library UI to browse, edit, delete, and
  topic-tag the stored past-paper corpus.
- **Sub-project 3 (future):** combined mode — real-exam-first, KO-topic-scoped, with
  KO-constrained AI fallback; plus the coverage view (deferred from this sub-project
  because it depends on the matching logic built there).

Past papers are stored in the existing `questions` table (`question_source='past_paper'`)
and grouped by `upload_batches` rows (`batch_type='past_paper'`, with
`exam_board/exam_year/paper_number/tier`). Figures live in the `images` table, linked via
`questions.image_id`. There is currently no way to view or manage this corpus — only the
quiz/review flows surface individual questions.

### Validation against a real paper

The design was validated by running a real AQA GCSE Biology paper
(`8461/1F`, Jun 2023) through the Sub-project 1 pipeline. Findings that shaped this spec:

- Metadata extracted correctly (AQA / 2023 / Paper 1 / Foundation).
- **Tables are captured as image figures** (Table 1/2/3 came through as cropped images),
  not as text. The library treats tables and diagrams identically — both are figures.
- **Figures are shared across sub-questions** — one `images` row is linked to multiple
  questions (e.g. one table used by 02.5 and 02.6). The per-question `image_id` model
  supports this natively.
- **`question_ref` is not a reliable sort/group key.** Normalisation is inconsistent
  (`01.1`→`011` but `01.6`→`16`) and multi-page questions yield duplicate/fragment rows
  (ref `012` appears twice). Therefore the library orders questions by
  `(page_number, id)` — which reproduces correct exam reading order — and treats
  `question_ref` as a display label only.
- **Edit/delete is the cleanup tool** for the duplicate/fragment rows that multi-page
  questions produce. No auto-merge (YAGNI).

### Backlog notes (NOT in this sub-project's scope)

- Sub-project 1 backlog: `_normalise_ref` in `backend/routers/upload.py` normalises
  inconsistently (`011` vs `16`); worth a dedicated fix so refs become e.g. `1.1`.
- Sub-project 3 backlog: the mark scheme's `Spec Ref` codes (e.g. `4.2.1`, `RPA3`) and
  `AO` codes are a strong signal for AI-assisted topic tagging. We do not capture them
  today; consider capturing them during extraction.

## Goal

Give the user a dedicated page to manage their past-paper corpus: browse papers and their
questions, view answers and figures, fix mis-extracted text, delete bad questions or whole
papers, and tag questions by topic — building on existing endpoints wherever possible.

## Architecture (chosen approach)

A dedicated `past_papers` resource: new `backend/routers/past_papers.py` (matching the
one-file-per-resource convention), registered in `backend/app.py`; a new
`frontend/pages/past-papers.html` page; a `past-papers` route in `frontend/js/router.js`;
and a nav link. The library reuses existing question endpoints for text edits and single
deletes rather than duplicating them.

Rejected alternatives: spreading past-paper endpoints across existing routers and
overloading `review.html` (tangles concerns); building under the admin router (wrong home
— this is a user study tool, not an admin function).

No database schema changes. All required columns already exist.

## Build phases

This sub-project ships as two independent plans:

- **Phase 2a — Library + corpus management** (shippable on its own): the page, browse/view
  grouped by paper, edit text, delete question/paper, per-question + bulk topic tagging.
- **Phase 2b — Figure management**: interactive re-crop UI + endpoint, attach/detach
  figure.

---

## Phase 2a — Library + corpus management

### Backend

**New router `backend/routers/past_papers.py`** (prefix `/past-papers`, all endpoints
user-scoped via `get_current_user`):

- `GET /past-papers?subject_id=<id>` — list the user's past-paper batches for a subject.
  Mirrors the `upload/history` query, filtered to `batch_type='past_paper'`. Returns per
  paper: `id`, `exam_board`, `exam_year`, `paper_number`, `tier`, `filename`,
  `created_at`, `question_count`, and `figure_count` (count of that batch's questions with
  a non-null `image_id`).

- `DELETE /past-papers/{batch_id}` — delete one of the user's past-paper batches. Verifies
  ownership and `batch_type='past_paper'` (404 otherwise). Reuses
  `delete_batch_images(batch_id)` and `delete_batch_pdf(batch_id)` from
  `backend/services/image_service.py`, then `DELETE FROM upload_batches WHERE id=?`;
  `questions` and `images` rows cascade via existing FKs.

- `POST /past-papers/tag` — body `{question_ids: [int], category_id: int|null,
  subcategory_id: int|null}`. Sets `category_id`/`subcategory_id` on the listed questions
  that belong to the user. Serves both per-question (one id) and bulk (many ids).
  Validates that `category_id`, if given, belongs to the same subject as the questions
  (reject cross-subject with 400); `subcategory_id`, if given, must belong to
  `category_id`. `null` clears the field.

**Small additions to existing endpoints:**

- `list_questions` in `backend/routers/questions.py` — add an optional `question_source`
  query parameter; when provided, add `AND q.question_source = ?` to the WHERE clause. The
  library lists a paper's questions via
  `GET /questions?batch_id=<id>&question_source=past_paper`. Ordering stays
  `ORDER BY q.page_number, q.id` (correct reading order — see validation findings).

- `update_question` (`PUT /questions/{id}`) in `backend/routers/questions.py` — extend the
  `QuestionUpdate` model and handler to also accept `question_ref` (currently not
  editable; needed to fix refs like `1 (a)` → `1a`). Existing editable fields
  (`question_text`, `answer_text`, `question_type`, `difficulty`) are unchanged.
  `marks` is intentionally not editable — it is never persisted.

Reused as-is: `DELETE /questions/{id}` (single delete), `GET /categories?subject_id=`,
`GET /subcategories?category_id=`.

### Frontend (`frontend/pages/past-papers.html`)

Alpine.js page, same patterns as `review.html`:

1. **Subject selector** at top (papers are per subject). On change, fetch
   `GET /past-papers?subject_id=`.
2. **Paper list** — one card per paper showing `exam_board exam_year paper_number tier`
   (gracefully omitting nulls), `question_count`, `figure_count`, upload date, and a
   **Delete paper** button (confirm dialog → `DELETE /past-papers/{batch_id}` → refresh).
3. **Expand a paper** → fetch its questions
   (`GET /questions?batch_id=&question_source=past_paper`), rendered in
   `(page_number, id)` order. Each question row shows:
   - `question_ref` as a label (display only), question text, collapsible answer
     (tolerate short/empty answers).
   - Figure thumbnail if `image_id` (served from existing image route; read-only in 2a).
   - Category + subcategory dropdowns (populated from the subject's categories) that tag
     this single question via `POST /past-papers/tag` with one id.
   - **Edit** button → modal to edit `question_text`, `answer_text`, `question_type`,
     `difficulty`, `question_ref` (→ `PUT /questions/{id}`).
   - **Delete** button (confirm → `DELETE /questions/{id}`).
4. **Bulk tagging** — a checkbox per question plus "select all in paper"; a bulk bar
   appears when any are selected, offering category/subcategory selectors and an Apply
   button (→ `POST /past-papers/tag` with the selected ids), then refresh.

**Routing/nav:** add `'past-papers': '/static/pages/past-papers.html'` to `router.js`
`routes`, and a nav link to the library (alongside the existing menu links). Not an admin
route — available to any logged-in user.

### Error handling (2a)

- All endpoints verify ownership; return 404 for batches/questions not owned by the user.
- Delete-paper and delete-question require a front-end confirm dialog.
- `POST /past-papers/tag` rejects a `category_id` from a different subject (400) and a
  `subcategory_id` not under the given `category_id` (400).
- Empty paper list and empty question list render friendly empty states.

### Testing (2a) — `tests/test_past_papers.py`

- `GET /past-papers` returns only the requesting user's `past_paper` batches for the
  subject, with correct `question_count` and `figure_count`; excludes
  `knowledge_organiser` batches and other users' batches.
- `list_questions` `question_source` filter returns only past-paper questions for a batch.
- `DELETE /past-papers/{batch_id}` removes the batch, its questions, and its image rows
  (assert cascade), and is rejected (404) for another user's batch or a non-past_paper
  batch.
- `POST /past-papers/tag` sets category/subcategory on multiple owned questions; ignores
  ids not owned by the user; rejects a cross-subject category (400) and a mismatched
  subcategory (400); `null` clears the tag.
- `PUT /questions/{id}` accepts and persists `question_ref`.
- Reuse conftest fixtures (`client`, `regular_user`, `make_subject`, `make_batch`,
  `make_question`, `db_conn`); follow the patterns in `tests/test_questions.py`.

---

## Phase 2b — Figure management

Builds on 2a; the page already displays figure thumbnails read-only.

### Backend (added to `backend/routers/past_papers.py`)

- `POST /past-papers/questions/{question_id}/recrop` — body
  `{bbox_x_pct, bbox_y_pct, bbox_w_pct, bbox_h_pct}`. For an owned question, load the saved
  full-page image at `data/images/batch_{batch_id}/page_{page_number}_full.png`, crop the
  region (reuse the crop logic in `backend/services/pdf_processor.py` —
  `crop_image_region` operates on PNG bytes), write a new `images` row, and set the
  question's `image_id` to it. Return 404 if the full-page image is missing (older
  uploads predating Sub-project 1). **Note:** if the question's current `image_id` is
  shared with other questions, re-crop creates a *new* image and repoints only this
  question (it does not mutate the shared row), avoiding surprise changes to siblings.

- `PUT /past-papers/questions/{question_id}/image` — body `{image_id: int|null}`. Attach
  an existing figure (validate the image belongs to the same batch) or detach (`null`
  clears `image_id`). User-scoped; 404 if not owned; 400 if the image is from another
  batch.

### Frontend (additions to `past-papers.html`)

- **Re-crop modal** — opens on a figure (or a "Set figure" action for a question without
  one). Shows the full-page image (`page_{n}_full.png`) with a draggable/resizable
  rectangle drawn as a lightweight overlay (plain JS/canvas or absolutely-positioned div —
  no new library). On confirm, convert the rectangle to bbox percentages and call the
  recrop endpoint; refresh the thumbnail.
- **Attach/detach controls** — a detach (✕) on a thumbnail (→ `image` endpoint with
  `null`); attach offers the batch's existing figures to link (→ `image` endpoint with an
  id). Useful when extraction linked the wrong figure or none.

### Error handling (2b)

- Missing full-page image → 404 with a clear message ("original page image unavailable;
  re-upload the paper to enable re-cropping").
- Re-crop validates bbox percentages are within 0–100 and `w/h > 0` (400 otherwise).
- Attach rejects an image from a different batch (400).

### Testing (2b) — `tests/test_past_papers.py`

- Re-crop: given a question and a fixture full-page PNG on disk, the endpoint creates a new
  `images` row and links it via `image_id`; a shared source image is not mutated (siblings
  keep their original `image_id`); missing full-page image → 404; invalid bbox → 400.
- Attach/detach: setting `image_id` to a valid same-batch image links it; `null` detaches;
  a cross-batch image is rejected (400); not-owned question → 404.

---

## Scope boundaries (YAGNI)

- **No coverage view** — deferred to Sub-project 3 (depends on KO-to-topic matching).
- **No AI-assisted tagging** — deferred to Sub-project 3 (needs an AI endpoint + cost).
- **No auto-merge** of duplicate/fragment rows — manual edit/delete handles cleanup.
- **No `marks`/`spec_ref`/`AO` capture or editing** — not persisted today; capture is a
  Sub-project 1/3 concern.
- **No bulk delete of questions** — whole-paper delete plus single-question delete cover
  the need.
- **No schema changes.**
