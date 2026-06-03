# Multiple-Response Past-Paper Questions — Design

**Date:** 2026-06-03
**Status:** Approved, ready for planning

## Problem

Some past-paper questions present a list of statements and ask the student to
tick a fixed number of boxes (e.g. *"Which two sentences describe malignant
tumours? Tick (✓) two boxes."* followed by five candidate sentences). Today the
extractor flattens the whole thing — stem **and** every option — into a single
`question_text` blob, and the quiz renders it as a wall of text with only a
"Show Answer" button. The student cannot actually select options, and there is
no proper marking.

Two fixes are needed:

1. **Detect** these multiple-response questions, structure them (stem +
   options + how many to tick + which are correct), and **present** them in the
   quiz as a proper checkbox question with objective marking.
2. In *Start a Quiz*, when **Past Paper** is the only selected source, stop
   offering **Multiple Choice** mode — MCQ distractors are never generated for
   past papers, so the button does nothing useful. Flashcard and Type Answer
   remain.

## Decisions (locked)

- **Marking:** auto-mark by exact set match — store which options are correct
  (from the mark scheme; inferred if absent), compare the ticked set to the
  correct set, no AI call at quiz time.
- **Detection scope:** new uploads are structured automatically; a per-paper
  **Re-detect** button lets existing already-processed papers be re-run on
  demand. No automatic whole-library backfill.
- **Past Paper modes:** hide Multiple Choice when Past Paper is the sole
  selected source. Mixed/all-source quizzes keep all three modes.

## Architecture

A single standalone detector is the source of truth for structuring, used by
both the new-upload path and the on-demand Re-detect button. The large
`PAST_PAPER_EXTRACTION_PROMPT` is left untouched (it stays fragile but stable);
detection runs as a cheap post-processing pass over already-extracted
questions.

### 1. Storage

One new nullable column on `questions`, added via `_add_column_if_missing()` in
`database.py`:

```
options_json TEXT DEFAULT NULL
```

When `null`, the question is a normal Q&A. When populated:

```json
{
  "select_count": 2,
  "options": [
    {"text": "Malignant tumours are only found in the reproductive system.", "is_correct": false},
    {"text": "Malignant tumours contain digestive enzymes.", "is_correct": false},
    {"text": "Malignant tumours do not change in size.", "is_correct": false},
    {"text": "Malignant tumours have cells that can spread to other parts of the body.", "is_correct": true},
    {"text": "Malignant tumours may form secondary tumours.", "is_correct": true}
  ]
}
```

When `options_json` is set, `question_text` is rewritten to the **stem only**
(e.g. *"Malignant tumours are cancers. Which two sentences describe malignant
tumours?"*) — the option sentences are removed from the blob. `answer_text` is
left intact (it still holds the mark-scheme answer for the View Source / record).

`select_count` is clamped to `1 <= select_count <= len(options)`. A result with
fewer than 2 options or fewer than 1 correct option is treated as **not** a
multiple-response question (stored as `null`).

### 2. Detector

New prompt module `backend/prompts/multiple_response_detection.py` and a
function in `backend/services/claude_service.py`:

```
detect_multiple_response_batch(questions, subject) -> list[dict | None]
```

- Input: a list of `{id, question_text, answer_text}` for one paper/page and the
  subject name.
- Output: a list aligned 1:1 with the input. Each element is either `None`
  (normal question) or `{"select_count": int, "stem": str,
  "options": [{"text": str, "is_correct": bool}, ...]}`.
- One Haiku call per paper/page (batched, like `generate_mcq_distractors`).
  Uses `_get_ai_setting` override convention; default model constant in
  `claude_service.py` (e.g. `MULTI_RESPONSE_MODEL`, defaulting to the Haiku
  model already used for MCQ).
- The prompt instructs: identify tick-N-boxes questions; extract the stem,
  the candidate options verbatim, the required count, and mark each option's
  correctness using the mark-scheme `answer_text` (infer from subject knowledge
  if the answer text is absent/unclear). Return `null` for anything that is not
  a fixed-count multiple-response question (ordinary short-answer, calculation,
  extended writing, single-best-answer, etc.).
- Records cost in `api_usage` (`call_type = 'multi_response_detection'`), same
  pattern as other Claude calls.

A thin service helper applies the detector results to the DB:
`apply_multi_response_results(question_rows, results, db)` — for each non-null
result, write `options_json` and overwrite `question_text` with the stem.

### 3. Wiring

**New uploads.** After past-paper extraction has written a page's questions, run
the detector over that page's questions and persist results. This runs as a
FastAPI background task (one batched call per page) so upload latency is
unaffected, mirroring how MCQ pre-generation is scheduled. Only past-paper
questions are passed to the detector.

**Re-detect (existing papers).** New endpoint in `backend/routers/past_papers.py`:

```
POST /api/past-papers/{batch_id}/detect-multi-response
```

Authenticated + user-scoped. Loads that batch's questions, runs
`detect_multiple_response_batch`, applies results, returns a summary
`{"updated": n, "scanned": m}`. The Past Papers page gets a **"Re-detect
tick-box Qs"** button per paper that calls it and toasts the result, then
refreshes the question list.

### 4. Quiz rendering (`frontend/pages/quiz.html`)

- `currentFormat` gains a guard at the top: if `currentQuestion.options_json`
  is present, return `'multi_response'` regardless of the selected-mode pool —
  the format is intrinsic to the question.
- New `multi_response` UI block:
  - Renders the stem (already in `question_text`) and a checkbox per option.
  - Caps ticks at `select_count`; a label shows e.g. *"Select 2"*.
  - Submit is enabled once at least one box is ticked.
  - On submit, the result block highlights each option as correct/incorrect and
    shows the standard Correct/Incorrect banner + Next button.
- Local state: `selectedOptions: []` (array of ticked option texts), reset in
  `resetCard()`. A `toggleOption(text)` helper enforces the `select_count` cap.

**Answer leakage:** `quiz/start` and `quiz/{id}/resume` strip `is_correct` from
each option before returning to the client (a helper in `quiz.py`), sending only
`{text}` plus `select_count`. The `/answer` response returns the authoritative
correct set so the UI can highlight after submission. (The full options with
correctness remain in the server-side `questions_json` and in `options_json`.)

### 5. Marking (`backend/routers/quiz.py`, `submit_answer`)

New branch for `quiz_format == "multi_response"`:

- `student_answer` is a JSON-encoded list of ticked option texts.
- Backend reads the question's `options_json`, builds the correct set
  (`{o.text for o where is_correct}`), and compares it to the ticked set.
- `is_correct = 1` iff the two sets are exactly equal; else `0`.
- `quality = 4` if correct else `1`; drives SRS via `sm2_update` exactly like
  MCQ.
- The `/answer` response includes the correct option texts (for UI highlight)
  and `correct_answer` (existing field) continues to carry `answer_text`.

The `AnswerRequest.quiz_format` accepts the new value; no schema migration
needed beyond that (it is a free-text field already).

### 6. Mode hiding (`frontend/pages/quiz.html` setup)

- A computed `availableModes()` returns the three mode descriptors, minus the
  `mcq` entry when `questionSources` is exactly `['past_paper']`.
- The Mode button `x-for` iterates `availableModes()` instead of the inline
  literal.
- When the source selection becomes past-paper-only, `mcq` is purged from
  `selectedModes` (in the existing source-toggle handler) so a stale MCQ
  selection cannot leak into the quiz.

## Testing

- **Detector** (`tests/`): mocked Haiku response — a tick-box question maps to a
  structured result; an ordinary question maps to `None`; `select_count`
  clamping; <2 options or 0 correct → treated as `None`.
- **apply_multi_response_results**: writes `options_json` and rewrites the stem;
  leaves normal questions untouched.
- **Re-detect endpoint**: auth required; other users' batches rejected; updates
  only past-paper questions; returns the summary counts.
- **`submit_answer` multi_response**: exact-set match → correct; subset,
  superset, and wrong-set → incorrect; SRS row created/updated with quality 4/1.
- **quiz/start + resume strip `is_correct`**: returned options contain `text` and
  `select_count` but never `is_correct`.
- **availableModes**: past-paper-only hides `mcq`; mixed/all keeps all three.

## Out of scope (YAGNI)

- Drag-to-order / ranking questions.
- "Match the pairs" questions.
- Partial-credit marking (all-or-nothing only).
- Automatic backfill of the entire existing library (Re-detect button covers it
  on demand).
- Changing the large extraction prompt's structure.
