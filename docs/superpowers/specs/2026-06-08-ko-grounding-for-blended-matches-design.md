# KO Grounding for Blended Matches — Design

**Date:** 2026-06-08
**Status:** Approved (design)

## Problem

When a Knowledge Organiser (KO) booklet is blended, the text matcher swaps in
real past-paper questions for KO points it judges equivalent. Two problems:

1. **Trust / opacity.** Some matched exam questions look unrelated to the KO, and
   there's no way to see *where* on the KO page the answer comes from. Even *good*
   matches are opaque — e.g. "What name is given to a group of tissues working
   together? → organ" genuinely appears on the KO page ("Organ: Structure made up
   of a group of tissues…"), but nothing surfaces that link.
2. **Quality.** The matcher is text-only (BM25 shortlist → text verification) and
   produces some genuine false positives.

## Goal

For every blended (matched) question:

- **Verify** the match is genuinely supported by the KO page, and **drop** matches
  that aren't (quality gate).
- Produce **written reasoning** ("this exam question maps to *X* in the KO") and a
  **close-up crop** of the KO region that contains the answer.
- Show the reasoning + crop in the **admin question card**, and in the **quiz
  "Show Source" panel** as a revision aid.

## Non-goals

- Replacing the BM25 + text matcher. Grounding runs *after* it, on survivors only.
- Pixel-perfect cropping. A generously padded, approximately-right crop is fine.
- Backfilling already-blended booklets automatically — grounding is produced on
  blend / Regenerate Blend going forward. (Existing blends gain it on next reblend.)

## Key product decisions

- **Quality gate drops ungrounded matches.** If grounding can't point to a KO
  region that supports the exam Q/A, the match is rejected during blend. If a KO
  point loses *all* its matches, it stays as its original AI question (the existing
  blend fallback — no change needed).
- **Grounding runs at blend time**, one vision call per matched KO point (its ≤3
  matches batched into a single call), ~70 calls for a 71-point batch. One-time
  cost per blend (~$0.30–0.50 on the stronger model, less on Haiku); results stored
  in the DB and reused for every quiz.
- **Quiz: crop + reasoning live in the expandable "Show Source" panel, visible at
  any time — including before the student answers.** The quiz is a revision tool,
  not an exam; seeing where the KO supports the answer aids retention when the
  student doesn't know it. (This intentionally reverses the prior
  "source screenshots contain the answer, keep hidden" stance for blended Source.)
- **Model is DB-overridable**, defaulting to the stronger model for verdict
  reliability. The user can switch to Haiku and compare cost/quality with no code
  change. Localization is made forgiving so a smaller model stays viable.

## Architecture

### 1. Data model

Two new columns on `questions`, added via the `_add_column_if_missing` list in
`init_db()` (`database.py`):

```sql
ALTER TABLE questions ADD COLUMN ko_grounding_reasoning TEXT DEFAULT NULL;
ALTER TABLE questions ADD COLUMN ko_crop_filename       TEXT DEFAULT NULL;
```

- `ko_grounding_reasoning` — the written explanation of where in the KO the answer
  is found.
- `ko_crop_filename` — relative path under the images root
  (`batch_<batch_id>/page_<n>_kocrop_<koid>_<ppid>.png`), served by the existing
  `/images` static mount. NULL when no crop (e.g. fallback failed) — UI then shows
  reasoning only.

Both are blend-generated metadata, so `_restore_blend` must NULL them.

### 2. Grounding service — `ground_matches_to_ko(...)`

New function in `claude_service.py`:

```
ground_matches_to_ko(
    ko_point: dict,                 # the KO question row (question_text, answer_text)
    ko_page_png: bytes,             # the KO page image (downscaled before send)
    candidates: list[dict],         # the ≤3 matched exam Q/A for this KO point
) -> tuple[list[dict], dict]        # (results, usage)
```

Each result: `{"past_paper_question_id": int, "supported": bool,
"reasoning": str, "bbox_pct": {x,y,w,h}|None, "snippet": str}`.

- Sends ONE vision message: the KO page image + a compact JSON of the KO point and
  its candidate exam Q/A (ids + text + answer).
- Prompt (`ai_prompt_grounding`, DB-overridable) instructs the model to, per
  candidate: decide if the KO page genuinely supports that exam Q/A; if so, quote
  the supporting KO **snippet** and give a coarse **bounding box** in percentage
  coordinates (0–100) of where that snippet sits on the page; if not, mark
  `supported: false`. Explicitly permits "not supported".
- Uses the robust JSON parsing already added (`_loads_json_response`).
- Reuses `_calc_usage`; records cost even on parse failure (existing pattern). A
  failed/garbled grounding call is **non-fatal**: treat its candidates as
  "supported, no crop" rather than dropping real matches on an infra hiccup —
  i.e. the gate only drops on an explicit `supported: false`, never on call/parse
  failure. (Avoids a model outage silently emptying a blend.)
- Image is downscaled to ~1100px max dimension before sending (cheaper; coarse box
  is enough). New helper in `pdf_processor.py` (e.g. `downscale_png(bytes, max_px)`).

Model/prompt config:
- `GROUNDING_MODEL` default constant in `claude_service.py` =
  `"claude-sonnet-4-6"` (vision-capable, reliable verdict); setting key
  `ai_model_grounding`, read via `_get_ai_setting`. Switchable to
  `claude-haiku-4-5` in Admin to compare cost/quality.
- `ai_prompt_grounding` setting; default prompt as a constant in
  `backend/prompts/grounding.py`.
- Both registered in `_AI_SETTING_METADATA` (`backend/routers/admin.py`) so they
  appear as labelled cards in Admin → AI Settings.

### 3. Blend pipeline integration (`upload.py`)

In `_match_and_replace_with_past_papers`, after `matches` is returned and grouped
by KO point, BEFORE the apply loop:

1. For each KO point with matches, load its KO page PNG from
   `data/images/batch_<batch_id>/page_<ko page_number>_full.png`, downscale, and
   call `ground_matches_to_ko`. Sum usage into the existing cost total.
2. Build a lookup `grounding[(ko_id, pp_id)] -> {reasoning, crop_filename}`:
   - If `supported is False` → exclude this (ko, pp) pair from `grouped` (drop).
   - Else → if a valid `bbox_pct`, pad it (e.g. +6% each side, clamped 0–100) and
     `crop_image_region` the **full-resolution** page to
     `page_<n>_kocrop_<koid>_<ppid>.png`; store its relative path. On missing/invalid
     bbox or crop error, leave `crop_filename = None` (reasoning still stored).
3. The apply loop writes `ko_grounding_reasoning` and `ko_crop_filename` in BOTH
   the in-place UPDATE (first match) and the INSERT (extra matches), keyed by
   `(ko_q_id, pp_q_id)`.
4. Existing gate logging extended: log how many matches were dropped by grounding
   (e.g. `blend[batch=%s]: grounding dropped %d of %d match(es) as unsupported`).

`_restore_blend`: add `ko_grounding_reasoning = NULL, ko_crop_filename = NULL` to
the in-place revert UPDATE. (Inserted rows are deleted as before; orphaned crop
PNGs on disk are harmless and left in place.)

### 4. Provenance (`quiz.py` `_attach_source_meta`)

For `past_paper` source rows, add to `prov`:
- `ko_reasoning = q.get("ko_grounding_reasoning")`
- `ko_crop_url = "/images/" + q["ko_crop_filename"]` when present.

The `/count`, `/start`, and session SELECTs that hydrate questions must include the
two new columns so they reach `_attach_source_meta`.

### 5. Admin card UI

In the pending-approval question card (`frontend/pages/review-category.html` —
already renders `source_context` + the page image; exact file confirmed in
planning), add, for blended/past-paper questions with grounding:
- The **KO crop** thumbnail (click → existing lightbox), and
- The **reasoning** text (e.g. a small "Where in the KO" block).

### 6. Quiz "Show Source" UI (`quiz.html`)

There is already a `showSource` toggle in component state. Add an expandable
**"Show Source"** control on the question view that, when expanded, renders (for
questions whose `provenance.ko_crop_url` / `provenance.ko_reasoning` are set):
- the KO crop image (click → lightbox), and
- the reasoning text.

Visible regardless of answer state (per product decision). The pre-existing
"Question image intentionally hidden" full-page image stays hidden — only the
targeted crop + reasoning surface here.

## Data flow

```
Regenerate Blend
  └─ match_ko_to_past_papers (BM25 + text)        → matches [{ko_id, pp_id}]
  └─ group by KO point
  └─ FOR each KO point with matches:
        load + downscale KO page PNG
        ground_matches_to_ko(ko, png, candidates) → [{pp_id, supported, reasoning, bbox, snippet}]
        drop unsupported; crop supported → save PNG
  └─ apply loop: UPDATE/INSERT rows incl. ko_grounding_reasoning, ko_crop_filename
        ↓
Quiz / Admin
  └─ SELECT incl. new columns → _attach_source_meta → provenance.ko_reasoning / ko_crop_url
  └─ rendered in Show Source (quiz) / question card (admin)
```

## Error handling

- Grounding call/parse failure → candidates kept (supported, no crop). Never drop a
  real match on infra failure; only an explicit `supported: false` drops.
- Missing/invalid bbox or crop failure → reasoning stored, `ko_crop_filename` NULL;
  UI shows reasoning only.
- Missing KO page PNG (legacy batch) → skip cropping for that KO point; reasoning
  still attempted from text in the message (image omitted), or skipped gracefully.
- Cost is always recorded (even on parse failure), per the existing matcher pattern.

## Testing (TDD)

`tests/test_claude_service_grounding.py` (new):
- supported result parsed; unsupported parsed; mixed batch.
- malformed/parse-failure → treated as supported-no-crop (non-fatal), cost recorded.
- bbox padding/clamping helper correctness; missing bbox → None.

`tests/test_upload.py` (extend):
- grounding drops an unsupported match (row not replaced/inserted).
- supported match persists `ko_grounding_reasoning` + `ko_crop_filename` on both
  the in-place and inserted rows.
- invalid bbox → reasoning stored, crop NULL (fallback).
- `_restore_blend` clears both columns.
- dropped-count gate logging emitted.

`tests/test_quiz.py` (extend):
- provenance carries `ko_reasoning` / `ko_crop_url` for a grounded blended question;
  absent for a plain past-paper question.

Mock all AI/vision calls and `crop_image_region` where appropriate; no network.

## Rollout

- Additive columns (default NULL) — safe migration via `_add_column_if_missing`.
- No behaviour change until a booklet is (re)blended. Existing blends are unaffected
  until the user hits Regenerate Blend.
- New AI settings appear with sane defaults; overridable in Admin.
