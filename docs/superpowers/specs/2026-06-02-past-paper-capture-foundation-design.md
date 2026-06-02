# Sub-project 1 — Past-Paper Capture Foundation

**Date:** 2026-06-02
**Status:** Approved design, ready for implementation plan

## Context

The upload page accepts either Knowledge Organiser (KO) files or past papers. When
KO + "Blend with Past Papers" is selected, the app generates AI questions from the KO
and then replaces them with equivalent real past-paper questions where a match exists
(`_match_and_replace_with_past_papers` in `backend/routers/upload.py`). Past papers are
stored locally in the `questions` table with `question_source = 'past_paper'`, so a
local past-paper corpus already exists.

Two foundational gaps block the larger "combined mode" and "past-paper library" work:

1. **Figures are dropped on past-paper uploads.** `process_batch` skips image cropping
   for past papers (`if batch_type != "past_paper"` at `backend/routers/upload.py:321`),
   and the past-paper extraction prompt never returns image bounding boxes. Any exam
   question that depends on a diagram, table, or source figure is stored without its
   visual.
2. **The matcher only sees the 100 most-recent past-paper questions per subject**
   (`LIMIT 100` in the corpus query), so a growing corpus is not fully usable.

This sub-project fixes both. It is the first of three:

- **Sub-project 1 (this doc):** capture foundation — figures + lift the limit.
- **Sub-project 2:** past-paper library UI (browse/view, edit/delete, topic tagging,
  coverage view).
- **Sub-project 3:** combined mode — real-exam-first, KO-topic-scoped, with
  KO-constrained AI fallback for points with no matching exam question.

## Goal

Make past-paper uploads capture per-question figures into the existing `images` table
and link them via `questions.image_id`, and make the full past-paper corpus available to
the matcher. No display work and no new data model are required — both already exist.

## Approach (chosen)

**AI bounding-box crop, mirroring the KO path.** Extend the past-paper extraction prompt
to return an `images[]` array and a `related_image_index` per question — identical in
shape to the KO prompt (`backend/prompts/qa_extraction.py:22`). The existing crop +
insert + link machinery in `process_batch` then handles past papers with no new logic.

Rejected alternatives:

- **Store the whole page image** for diagram questions — shows the entire exam page
  (clutter, examiner boxes, possibly adjacent answers). Worse UX.
- **Defer cropping to a manual tool in the library (Sub-project 2)** — questions stay
  image-less until manually cropped; more total work, worse first version.

## Changes

### 1. Prompt — `backend/prompts/past_paper_extraction.py`

Mirror the KO image schema:

- Add an `images[]` array to the returned JSON, each entry with `description`,
  `bbox_x_pct`, `bbox_y_pct`, `bbox_w_pct`, `bbox_h_pct` (percentages of page
  dimensions), matching `backend/prompts/qa_extraction.py`.
- Add `related_image_index` to each question object: the 0-based index into `images[]`,
  or `null` when the question has no associated figure.
- Apply only to `questions` and `both` page types. For figures/tables/diagrams that a
  `diagram-based` question depends on. Decorative elements, headers, and examiner-use
  boxes are excluded.
- `cover` and `mark_scheme` pages return `images: []` (unchanged behaviour — they are
  filtered out before question insert anyway).
- Keep all existing rules (verbatim question text, `question_ref` normalisation, marks,
  difficulty, mark-scheme answers).

### 2. Processing — `backend/routers/upload.py`

- Remove the `if batch_type != "past_paper":` guard at `upload.py:321` that fences off
  the image crop+insert block, so the block runs for both KO and past-paper batches.
- The block already reads `result.get("images", [])`, crops each region via
  `crop_image_region`, inserts an `images` row, and builds `image_id_map`.
- The shared question-insert at `upload.py:352` already maps
  `related_image_index → image_id`. No insert changes needed.
- Verify the crop block's position: it runs after extraction and before the question
  insert for the page, for both branches. Confirm `result.get("images", [])` is empty
  (and thus a no-op) for cover/mark-scheme pages, which return before this point.

### 3. Matching — `backend/routers/upload.py`

- Remove `LIMIT 100` from the past-paper corpus query in
  `_match_and_replace_with_past_papers` (around `upload.py:154`) so the matcher considers
  the full subject corpus. Keep the `ORDER BY q.id DESC` ordering.

### 4. Display

None. `backend/routers/quiz.py` and `backend/routers/questions.py` already
`LEFT JOIN images i ON i.id = q.image_id` and expose `image_filename`. A past-paper
question with a populated `image_id` renders its figure in quiz and review with no
further work.

## Data flow

```
past-paper page (questions/both)
  -> Claude extraction returns questions[] + images[] (bbox)
  -> for each images[] entry: crop_image_region(...) -> save PNG to data/images/batch_N/
                              -> INSERT images row -> image_id_map[i]
  -> for each question: image_id = image_id_map[related_image_index]
                        -> INSERT questions row with image_id
  -> quiz/review JOIN images on image_id -> figure rendered
```

## Scope boundaries (YAGNI)

- **One image per question.** The `questions.image_id` column is 1:1; if an exam question
  references multiple figures, the model picks the most relevant one. Multi-image support
  is out of scope.
- **No backfill.** Past papers uploaded before this change have no figures; re-uploading
  captures them. A backfill/reprocess tool is out of scope.
- **No matching on images.** The KO-to-past-paper matcher still compares text
  (`question_text` + `answer_text`) only. Visual matching is explicitly deferred.
- **No new UI.** Browsing, editing, and tagging the corpus belong to Sub-project 2.

## Testing

- **Capture:** unit test feeding a mocked past-paper extraction result containing an
  `images[]` entry and a question with `related_image_index: 0` to the past-paper
  processing path; assert an `images` row is created and the inserted question's
  `image_id` references it.
- **No-figure case:** mocked extraction with `images: []` and `related_image_index: null`
  produces a question with `image_id = NULL` and no `images` rows.
- **Limit removal:** with >100 past-paper questions for a subject, assert the matcher's
  corpus query returns more than 100 rows (or assert the query has no `LIMIT`).
- **Regression:** existing KO image capture tests and the full suite stay green
  (`pytest tests/ --tb=short -q`).
