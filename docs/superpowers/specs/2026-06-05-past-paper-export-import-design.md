# Past Paper Export / Import — Design

**Date:** 2026-06-05
**Status:** Approved (design), pending implementation plan

## Problem

Past-paper uploads are expensive to produce: each one runs AI extraction over the
question paper and mark scheme to generate questions, answers, multiple-choice
options, and cropped figures. There is currently no way to back these up or move
them between RevisionAid instances. If the database is lost or the user wants to
seed a new instance, the only recovery path is re-running the costly AI pipeline.

## Goal

Let a user **export** selected past papers (and all their associated AI-generated
data and figures) to a portable file, and **import** that file into any RevisionAid
instance to recreate the papers exactly — without re-running the AI.

## Scope

**In scope:** standalone past-paper batches (`upload_batches.batch_type='past_paper'`)
and their `questions`, `mcq_options`, and `images` rows, plus the on-disk figure
PNGs.

**Out of scope:**
- Knowledge-organiser / blended batches (blended exam matches are reconstructed via
  the existing "regenerate blend" feature, so they are not exported here).
- Per-user revision state: `srs_cards`, `quiz_answers`, `quiz_sessions`, `api_usage`.
  A backup restores *the paper and its questions*, not a user's revision history.
- Original PDFs (see decision below).

## Key Decisions

### Exclude the original PDFs
Verified against the codebase:
- **Quizzing and figure display** use the stored PNGs, not the PDF.
- **Re-cropping a figure** (`POST /api/past-papers/questions/{id}/recrop`) reads the
  saved full-page PNG `data/images/batch_N/page_N_full.png`, *not* the PDF. It works
  without the PDF **provided the full-page PNGs are included in the backup**.
- The **only** consumer of the original PDF is the admin "reprocess from scratch"
  action (`admin.py`), which re-runs the expensive AI anyway — defeating the purpose
  of a backup.

Therefore PDFs are excluded to keep archives small. On import, `pdf_path` is set to
the sentinel `'imported'` so the deliberate absence is explicit.

### Include the entire `data/images/batch_N/` folder
The `images` table only tracks the **cropped figures** (e.g. 13 rows for batch_12),
but the folder also holds the **full-page PNGs** (e.g. 40 `page_N_full.png`) that are
not referenced by any table yet are required for recrop. The backup copies the whole
folder, not only DB-referenced files.

### Combined zip with per-paper subfolders
Multi-select export produces a single `.zip` containing one subfolder per paper, so
the whole archive imports at once while each paper remains individually described in
the manifest.

### Duplicate handling: warn and skip
On import, a paper matching an existing one by
`exam_board + exam_year + paper_number + tier` (for the importing user) is skipped and
reported. The user can delete the existing paper first if they want to replace it.

## Archive Format

```
RevisionAid-PastPapers-2026-06-05.zip
├── manifest.json                         # format version, export date, paper list
└── papers/
    └── Biology-AQA-84611F-QP-JUN23/       # slug from exam filename
        ├── paper.json                     # batch row + questions + mcq_options + images rows
        └── images/                        # entire data/images/batch_<id>/ folder (crops + full pages)
```

- **Single-paper export** default filename: `<exam-filename>.revaid.zip`
  (e.g. `Biology-AQA-84611F-QP-JUN23.revaid.zip`).
- **`manifest.json`**: `{ "format": "revisionaid-pastpapers", "version": 1,
  "exported_at": "...", "papers": [ { "slug": ..., "filename": ..., "exam_board": ...,
  "exam_year": ..., "paper_number": ..., "tier": ..., "question_count": ... } ] }`.
- **`paper.json`** per paper:
  - `batch`: the `upload_batches` row fields that are portable — `filename`,
    `batch_type`, `exam_board`, `exam_year`, `paper_number`, `tier`, `page_start`,
    `page_end`, `source_type`, plus **subject / category / subcategory by name**
    (not by ID).
  - `questions`: each question's portable fields — `page_number`, `question_text`,
    `answer_text`, `question_type`, `difficulty`, `approved`, `question_source`,
    `question_source_detail`, `question_ref`, `source_context`, `options_json`, and a
    **local image reference** (the image's slot index within this paper, or null).
  - `mcq_options`: per question, `[{option_text, is_correct}]`.
  - `images`: each image's portable fields — `page_number`, `filename` (relative path
    inside the folder, e.g. `page_25_img_0.png`), `description`, crop coords
    (`crop_x/y/w/h`), `width`, `height`, and a stable local index used by questions to
    reference their figure.

IDs are never exported as cross-instance identifiers; all linkage inside `paper.json`
uses local indices so the importer can remap to fresh DB IDs.

## Endpoints

### `GET /api/past-papers/export?ids=12,14`
- Auth required. Filters to the caller's own `batch_type='past_paper'` batches; any id
  not owned or not a past paper is skipped (export proceeds with the valid ones; if
  none are valid → 400).
- Builds the zip in memory / temp and streams it via `StreamingResponse` /
  `FileResponse` with a `Content-Disposition` filename derived from the selection
  (single paper → exam filename; multiple → `RevisionAid-PastPapers-<date>.zip`).

### `POST /api/past-papers/import`
- Auth required. Multipart upload of one `.zip`.
- Validates `manifest.json` format/version; rejects malformed archives (400).
- For each paper in the archive:
  1. **Duplicate check** by `exam_board + exam_year + paper_number + tier` for this
     user → if present, skip and record `{paper, reason: "duplicate"}`.
  2. Resolve **subject** by name (create if missing); resolve **category** and
     **subcategory** by name within that subject (create if missing); set `user_id` to
     the importer.
  3. Insert new `upload_batches` row (`pdf_path='imported'`, `status='completed'`) →
     capture `new_batch_id`.
  4. Extract the paper's `images/` folder to `data/images/batch_<new_batch_id>/`.
  5. Insert `images` rows (new batch id) → build `local_index → new_image_id` map.
  6. Insert `questions` rows (remap `batch_id`, and `image_id` via the map).
  7. Insert `mcq_options` rows (remap `question_id`).
- Return a summary: `{ imported: [...], skipped: [{filename, reason}], errors: [...] }`.
- All DB writes for a paper happen in one transaction; a failure on one paper rolls
  that paper back and is reported in `errors` without aborting the others.

## UI (Past Papers page)

`frontend/pages/past-papers.html` + `backend/routers/past_papers.py`:
- A **checkbox per paper** in the existing list, plus a select-all control.
- An **Export selected** button → calls the export endpoint and triggers a download.
- An **Import** button → file picker (`.zip`) → posts to the import endpoint → shows
  an imported / skipped / error summary using the page's existing themed modal style.
- After a successful import, refresh the paper list.

## Module Boundaries

A focused export/import module keeps `past_papers.py` from growing unwieldy:
- `backend/services/paper_archive.py` (new):
  - `build_archive(batch_ids, user_id, db) -> (bytes, filename)` — gather rows + files,
    produce the zip.
  - `read_archive(zip_bytes) -> ParsedArchive` — validate + parse manifest/paper.json.
  - `import_paper(parsed_paper, user_id, db) -> ImportResult` — duplicate check, name
    resolution, insert + remap, file extraction.
- The router endpoints stay thin: auth, ownership filtering, call the service, shape
  the HTTP response.

## Error Handling

- Export: unknown/foreign ids skipped; zero valid ids → 400; IO/zip errors → 500 with
  a clear message.
- Import: non-zip or missing/malformed `manifest.json` → 400; unsupported
  `version` → 400; per-paper failures captured in the response `errors` list, never a
  silent partial success.
- Path-traversal safety: when extracting `images/`, reject any entry whose normalised
  path escapes the target batch folder.

## Testing

All against the in-memory SQLite DB (conftest fixtures):
- **Round-trip**: export a paper → import into a fresh state → assert batch, questions,
  mcq_options, images rows and figure files match, and that `image_id` / `batch_id` /
  `question_id` FKs are correctly remapped.
- **Combined multi-paper** export → import recreates all papers.
- **Duplicate skip**: importing a paper that already exists (same board/year/number/
  tier) is skipped and reported.
- **Subject/category remap by name**: importing into an instance whose subject has a
  different ID resolves by name (and creates the subject when missing).
- **Ownership**: export ignores another user's batch; non-past-paper ids excluded.
- **Malformed archive**: bad/missing manifest → 400.
- **Path-traversal**: a crafted zip entry escaping the folder is rejected.
- **PDF-less recrop sanity**: an imported paper has its full-page PNGs present so
  recrop preconditions hold.
