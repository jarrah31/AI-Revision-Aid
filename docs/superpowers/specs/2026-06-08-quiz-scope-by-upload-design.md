# Quiz Scope by Upload — Design

**Date:** 2026-06-08
**Status:** Approved (design)

## Problem

The quiz setup lets you filter by subject, category, subcategory, question source,
and blended mode — but **not by which upload the questions came from**. When a user
re-uploads a Knowledge Organiser (e.g. an old and a freshly re-blended copy of the
same booklet both exist), or has several past-paper PDFs, the quiz pool mixes them
all. The user often wants only the *latest* KO booklet's questions (now more
reliable after grounding), but has no way to restrict to it.

The "Questions come from" panel already lists the contributing uploads — but it is
read-only, and each filename is truncated.

## Goal

- Let the user **scope a quiz to one or more specific uploads** by selecting them in
  the "Questions come from" list.
- Show the **full upload filename** (currently truncated).

## Non-goals

- No persistence of the upload filter onto saved/resumed quiz sessions. The started
  quiz already freezes its exact questions in `questions_json`, so resume works
  regardless; persisting `batch_ids` would only add a cosmetic session-card label and
  is out of scope (YAGNI).
- No change to `/quiz/sources` semantics (it remains the stable list you pick from).
- No new DB columns or migrations.

## Key decisions

- **Multi-select.** Toggle any number of uploads; the quiz draws only from the
  selected ones. Selecting none = all uploads (today's behaviour). Mirrors the
  existing Category multi-select.
- **Live setup only.** The filter narrows the quiz being configured; it is not stored
  on the session.
- **Mechanism mirrors `_cat_subcat_filter`.** A new `batch_ids` filter, AND-composed
  with the existing WHERE clauses.

## Architecture

### 1. Backend filter — `backend/routers/quiz.py`

New helper, alongside `_cat_subcat_filter` / `_source_filter`:

```python
def _batch_filter(batch_ids: list[int] | None):
    """Return (sql_fragment, params) restricting to specific upload batches.
    Empty/None → (None, []) (no filter, all uploads)."""
    if not batch_ids:
        return None, []
    ph = ",".join("?" * len(batch_ids))
    return f"q.batch_id IN ({ph})", list(batch_ids)
```

Wired into two endpoints (both already assemble `conditions`/`params` lists):

- **`GET /quiz/count`** — add `batch_ids: list[int] | None = Query(None)`; apply
  `_batch_filter` like the category filter. The existing empty-result diagnostic is
  unaffected (it re-runs the source filter without category/approval; batch is just
  another AND that produced 0 — acceptable, no special handling needed).
- **`POST /quiz/start`** — add `batch_ids: list[int] | None = None` to
  `QuizStartRequest`; apply `_batch_filter` to the question-selection query's
  conditions (same place `_cat_subcat_filter` / `_source_filter` are applied).

`GET /quiz/sources` is **unchanged** — it lists the uploads to choose from and must
stay stable regardless of batch selection.

Composition: `batch_ids` is a plain AND on `q.batch_id`. With
`question_sources=['blended'], blended_mode='exam_only'`, the source clause already
restricts to `past_paper` rows inside blended KO booklets; ANDing
`q.batch_id IN (<latest KO>)` yields exactly that booklet's matched exam questions.
With `question_sources=['past_paper']` and a past-paper PDF batch selected, it yields
that paper's questions. Empty selection adds no clause.

### 2. Frontend selection UX — `frontend/pages/quiz.html`

State + behaviour (Alpine component):
- New `selectedBatchIds: []`.
- `toggleBatch(id)` adds/removes `id` from `selectedBatchIds`.
- `$watch('selectedBatchIds', () => this.loadAvailableCount())`.
- `loadAvailableCount()` and `startQuiz()` include the selection:
  - count: `this.selectedBatchIds.forEach(id => params.append('batch_ids', id))`
  - start body: `batch_ids: this.selectedBatchIds.length > 0 ? this.selectedBatchIds : null`
- In `loadSources()`, after fetching, prune stale selections:
  `this.selectedBatchIds = this.selectedBatchIds.filter(id => this.sources.some(s => s.batch_id === id))`
  (a batch no longer in the list — e.g. after a category change — must not keep
  filtering invisibly).

"Questions come from" panel:
- Each row becomes a button/clickable toggling `toggleBatch(s.batch_id)`, with a
  selected style (e.g. `bg-indigo-50` + ring or a leading ✓) when
  `selectedBatchIds.includes(s.batch_id)`.
- Header gains a selected count and auto-expands when a selection exists:
  `Questions come from (5 uploads · 1 selected)`; set `showSources = true` whenever
  `selectedBatchIds.length > 0` so the active filter stays visible.
- A one-line hint under the header: *"Tap an upload to limit the quiz to it
  (none selected = all)."*

De-truncation:
- The filename span currently uses `truncate`. Replace with wrapping
  (`break-words`, drop `truncate`) so the full name shows; keep the `:title` tooltip.
  The row already right-aligns the count label, so a wrapped name is fine.

### 3. Reset behaviour

`selectedBatchIds` is pruned (not blanket-cleared) on sources reload, so switching
category keeps any still-valid selections and drops the rest. Starting a quiz does not
clear it (consistent with other filters).

## Data flow

```
User toggles an upload row
  → selectedBatchIds updates
  → $watch → loadAvailableCount() → GET /quiz/count?...&batch_ids=..  → "N available"
Start Quiz
  → POST /quiz/start { ..., batch_ids:[...] }
  → _batch_filter ANDed into the selection query
  → questions drawn only from those uploads
```

## Error handling

- Empty/None `batch_ids` → no clause (all uploads).
- A selected-but-now-absent batch is pruned client-side before it can filter to 0.
- If a valid selection legitimately yields 0 (e.g. selected a past-paper PDF but chose
  the `ai_generated` source), the existing count==0 diagnostic logs the reason and the
  UI shows "None available" — same as today.

## Testing — `tests/test_quiz.py`

- `_batch_filter`: single id, multiple ids (correct placeholders/params), empty/None
  → `(None, [])`.
- `/quiz/count?batch_ids=<b>` returns only that batch's approved questions; two
  `batch_ids` union; omitting the param leaves the total unchanged.
- `/quiz/start` with `batch_ids` serves only questions from those uploads.
- Combined: `batch_ids=<KO booklet>` + `question_sources=blended&blended_mode=exam_only`
  returns only that booklet's matched exam questions (and excludes another blended
  booklet's).

Frontend is an Alpine fragment (no automated test); verified by inspection +
manual smoke (toggle a row, watch the count change, start, confirm questions).

## Rollout

Additive query param + request field; no migration. Old clients that don't send
`batch_ids` behave exactly as before. No version bump or release performed as part of
this change (publishing remains a separate, user-requested step).
