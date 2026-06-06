# Paper-level Subject + Category for Past Papers

**Date:** 2026-06-06
**Status:** Approved design

## Problem

Past papers are expensive to produce and can now be exported/imported between
installs. But after an import, papers land **uncategorised** and sometimes
detached from any existing subject. Separately, the current UI lets you tag a
**category per question**, which is pointless: every question in a single past
paper belongs to the same category (e.g. a Biology paper is all "Biology").

### Root cause

Category has only ever been written to `questions.category_id` (via the
`POST /past-papers/tag` endpoint). The batch-level column
`upload_batches.category_id` was **always NULL** for past papers. Because
`serialize_paper` reads the batch-level category, exports carried no category
and imports therefore landed uncategorised.

## Goal

Move the source of truth for a past paper's **Subject** and **Category** to the
paper (batch) level, editable directly from the Past Paper page, and surface the
current category in each paper's summary. Remove per-question category tagging.

## Decisions

- **Subcategory is dropped for past papers.** Paper-level classification is
  Subject + Category only. Subcategories remain untouched for
  knowledge-organiser content elsewhere.
- **Category picker = pick-existing OR type-new.** When assigning a category you
  can select one of the subject's existing categories or type a new name, which
  is created on save. This matters most right after an import, when the subject
  may have no categories yet.
- **Subject is pick-from-existing.** Subjects already exist (created on import
  by name); no inline subject creation.

## Data model & cascade

- A past paper's classification lives on `upload_batches`: `subject_id`,
  `category_id`. `subcategory_id` is not used for past papers.
- Quizzing and coverage filter on the **per-question** columns
  (`questions.subject_id`, `questions.category_id`, `questions.subcategory_id` —
  see `quiz.py::_cat_subcat_filter`). Therefore any paper-level change must
  **cascade** to every question in that batch:
  - set each `questions.subject_id` = the paper's new subject
  - set each `questions.category_id` = the paper's new category (or NULL)
  - set each `questions.subcategory_id` = NULL (subcategory dropped for past
    papers)
- **One-time backfill migration** in `init_db()` (via the existing
  `_add_column_if_missing` neighbourhood / a guarded one-shot UPDATE): for every
  `batch_type='past_paper'` batch with NULL `category_id`, set it to the most
  common non-NULL `category_id` among that batch's questions. This preserves the
  per-question tagging already done on the current install before the UI
  changes. Batches whose questions have no category stay NULL (uncategorised).
  The migration must be idempotent (only touches batches where the batch
  `category_id IS NULL`).

## Backend

### `GET /past-papers` (list)
Add `category_id` and `category_name` to the returned rows (LEFT JOIN
`categories`). Everything else unchanged.

### `PATCH /past-papers/{batch_id}` (new)
Reclassify a past paper.

Request body:
```
{
  "subject_id": int,
  "category_id": int | null,        # choose an existing category
  "new_category_name": str | null   # OR create a new category by name
}
```
Behaviour:
1. Validate the batch exists, is owned by the user, and is `batch_type='past_paper'` → else 404.
2. Validate `subject_id` exists → else 400. (Subjects are global in this schema.)
3. Resolve the category:
   - If `new_category_name` is a non-empty string: get-or-create a category with
     that name under `subject_id`, return its id.
   - Else if `category_id` is provided: verify it belongs to `subject_id` → else
     400 ("Category does not belong to the chosen subject").
   - Else: category is NULL (uncategorised).
   - If both `category_id` and `new_category_name` are provided,
     `new_category_name` wins (create/get-by-name).
4. Update the batch's `subject_id`, `category_id` (and clear `subcategory_id`).
5. Cascade to all questions in the batch (subject_id, category_id, subcategory_id=NULL).
6. Commit. Return `{ "subject_id", "category_id", "category_name" }`.

### Remove `POST /past-papers/tag`
Delete the endpoint, its `TagRequest` model, and its tests. It is superseded by
paper-level classification.

## Frontend (`frontend/pages/past-papers.html`)

- **Paper summary** shows the category as a pill: `Category: <name>` or
  `Uncategorised` when NULL. Add an **Edit** button next to it.
- **Edit control** (inline panel or small modal scoped to the paper):
  - Subject `<select>` listing existing subjects (default = paper's current
    subject).
  - Category combobox: an input with a datalist of the selected subject's
    existing categories, allowing free text for a new name. Changing the subject
    resets the category field and reloads that subject's categories.
  - Save → `PATCH /past-papers/{id}` with `subject_id` and either the chosen
    existing `category_id` (when the typed text exactly matches an existing
    category) or `new_category_name` (when it's new / typed). Cancel closes
    without saving.
  - After save, reload the list (`loadSubject()`); if the subject changed the
    paper leaves the current filtered view — expected.
- **Remove** the per-question category `<select>` and the bulk-tag toolbar
  (select-all checkbox row, category/subcategory dropdowns, "Apply tag" button)
  and the supporting state/methods (`selected`, `bulkCategoryId`,
  `bulkSubcategoryId`, `selectedIds`, `toggleAll`, `applyBulkTag`, `tagOne`,
  `loadSubcats`, `subcategories`). Keep question text/answer/ref editing,
  figures, recrop, detach, and delete.

## Export / import

No archive format change (stays `version: 1`). Once category lives on the batch,
the existing `serialize_paper` already emits `batch.category_name`, so
re-exported papers now carry their category and import restores it
(`import_paper` already resolves/creates the category by name under the
subject). `subcategory_name` simply stops being populated for past papers.

## Testing

- `tests/test_paper_archive.py`: remove the `/tag` endpoint tests; the
  round-trip test asserts that a paper with a batch-level category survives
  export → import with its category intact.
- New `tests/` coverage for `PATCH /past-papers/{batch_id}`:
  - assign an existing category
  - create a new category by name (verify it appears under the subject)
  - reassign to a different subject (category created/validated under the new
    subject; paper's questions' subject_id all updated)
  - cascade: every question in the batch gets the new subject_id + category_id
    and subcategory_id cleared
  - ownership / non-past-paper → 404
  - category_id that belongs to a different subject → 400
- `GET /past-papers` returns `category_name`.
- Backfill migration: a past paper with NULL batch category but tagged questions
  gets its batch `category_id` set to the most common question category;
  idempotent on a second run; untagged paper stays NULL.

## Out of scope

- No changes to knowledge-organiser category/subcategory behaviour.
- No bulk reclassification across multiple papers at once (per-paper edit only).
- No archive format/version bump.
