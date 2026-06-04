# Past-Paper Coverage View + Multi-Match Blend — Design

**Date:** 2026-06-04
**Status:** Approved, ready for planning
**Context:** Past-Paper Library, Sub-project 3 (first slice)

## Problem

When a Knowledge Organiser (KO) is uploaded with **"Blend with past papers"** enabled,
the app matches KO knowledge points to real exam questions and uses the real question
instead of an AI-generated one (`_match_and_replace_with_past_papers` +
`match_ko_to_past_papers`). Two gaps remain:

1. **No visibility.** Nothing summarises how much of a KO upload is backed by *real*
   past-paper questions versus *AI-generated* ones. The per-question badges exist on the
   View Q&As page, but there is no count or breakdown.
2. **Reinforcement lost.** The matcher is strictly 1:1 — one KO point keeps at most one
   exam question, and any *other* real exam questions that test the same point in a
   different way are discarded. Those extra phrasings are valuable for reinforcement and
   should be kept.

This spec covers both, as one cycle, because the Blend change directly changes what the
coverage view counts.

## Decisions (locked)

- **Coverage is count-based.** Computed from existing `question_source` data. No new AI
  calls, no topic tagging. (Topics/subtopics remain user-defined; the AI never invents
  taxonomy.)
- **Coverage appears in both existing places:** a chip on each KO row in the previous-
  uploads list, and a summary banner (with per-paper breakdown) at the top of the
  View Q&As page.
- **Multi-match Blend:** a KO point may keep multiple genuinely-different exam questions,
  **capped at 3 per KO point.** Each real exam question is still used only once overall.
- **Scope:** coverage UI applies to `knowledge_organiser` batches only. `past_paper`
  batches are 100% past-paper by definition and get no coverage UI.

## Part A — Coverage View (display)

### A1. Backend

**`/costs/history`** (`backend/routers/costs.py`) — add one subselect per batch so the
list page can render a chip without N extra calls:

```sql
(SELECT COUNT(*) FROM questions q
 WHERE q.batch_id = b.id AND q.question_source = 'past_paper') AS past_paper_count
```

`question_count` is already returned; the chip derives `past_paper_count / question_count`.

**New `GET /api/questions/coverage?batch_id=X`** (`backend/routers/questions.py`) —
authenticated, user-scoped (404 if the batch is not owned by the caller). Authoritative
server-side counts (so the review page's `limit=200` pagination cannot undercount):

```json
{
  "total": 30,
  "past_paper": 12,
  "ai_generated": 18,
  "by_paper": [
    {"source": "AQA 2023 Paper 1", "count": 7},
    {"source": "AQA 2022 Paper 2", "count": 5}
  ]
}
```

- `total` = all questions in the batch; `past_paper` / `ai_generated` = counts by
  `question_source`.
- `by_paper` = `GROUP BY question_source_detail` over `question_source='past_paper'`
  rows, ordered by `count DESC`. A NULL/empty `question_source_detail` is bucketed as
  `"Past Paper"`.

### A2. Frontend

**`frontend/pages/upload-history.html`** — on each row where
`b.batch_type === 'knowledge_organiser'`:
- If `past_paper_count > 0`: a chip, e.g. `📄 12/30 from past papers`.
- If `past_paper_count === 0`: a muted `All AI-generated` label.
- No chip on `past_paper` batches.

**`frontend/pages/review.html`** — on load, fetch `/questions/coverage?batch_id=…` in
parallel with the existing questions load. For `knowledge_organiser` batches, render a
summary banner above the question list:

> **12 of 30 questions (40%) matched to real past papers** · 18 AI-generated
> AQA 2023 Paper 1: 7 · AQA 2022 Paper 2: 5

Guard the percentage when `total === 0`. Hide the banner for `past_paper` batches. The
existing per-question badges are unchanged.

## Part B — Multi-Match Blend (behaviour)

### B1. Matcher (`backend/prompts/matching.py` + `match_ko_to_past_papers`)

- Update `MATCHING_PROMPT`: for each KO question, return **all** past-paper questions
  that test the *same* knowledge point — but **only** when they ask in a genuinely
  different way (not verbatim/near-duplicate wording). Suggest a soft maximum of 3 per
  KO point in the prompt. Keep the rule: *each past-paper question is used for only one
  KO question.*
- Return shape is unchanged — a flat list of `{ko_question_id, past_paper_question_id}`
  pairs — but `ko_question_id` may now repeat. `match_ko_to_past_papers` already returns
  `result.get("matches", [])`; no signature change.

### B2. Application (`backend/routers/upload.py` `_match_and_replace_with_past_papers`)

- Group matches by `ko_question_id`, preserving order.
- Enforce the **cap of 3** per KO point in application code (take the first 3 matches
  per `ko_question_id`), independent of the prompt's soft limit.
- Keep the global `used_pp_ids` dedupe.
- For each KO question that has matches:
  - **First** match → `UPDATE` the KO question's `question_text`, `answer_text`,
    `question_source='past_paper'`, `question_source_detail`, `updated_at` (exactly as
    today).
  - **Each additional** match → `INSERT` a new `questions` row into the **same KO batch**,
    cloning the KO question's `batch_id`, `subject_id`, `user_id`, `category_id`,
    `subcategory_id`, `approved`, and (if present) `page_number`, but with the exam
    question's `question_text`, `answer_text`, `question_source='past_paper'`,
    `question_source_detail`, and `options_json` (so tick-box exam questions carry over).

### B3. Preserved behaviour & known limitation

- When a KO point has exactly one match, behaviour is identical to today (pure 1:1
  replace).
- **Figures not carried:** blended questions do not copy the exam question's `image_id`
  — this matches the existing 1:1 behaviour and is intentionally left unchanged here
  (out of scope).

## Data flow

```
KO upload (Blend on)
  └─ extract → AI questions written
  └─ _match_and_replace_with_past_papers
       └─ match_ko_to_past_papers (now multi-match, cap 3 in app)
            ├─ 1st match per KO point → UPDATE (replace AI question)
            └─ extra matches        → INSERT new past_paper rows in same batch
  └─ View Q&As (review.html): badges + coverage banner (by_paper)
  └─ Previous uploads (upload-history.html): coverage chip
```

## Testing

**Part A**
- `GET /questions/coverage`: correct `total` / `past_paper` / `ai_generated`; `by_paper`
  grouping + `DESC` order; NULL `question_source_detail` bucketed as `"Past Paper"`;
  ownership → 404 for another user's batch; empty batch → zeros, empty `by_paper`.
- `/costs/history`: response includes `past_paper_count` per batch with correct value.

**Part B**
- Matcher returns multiple pairs for one `ko_question_id` → one `UPDATE` + N `INSERT`;
  batch `question_count` grows by N.
- Inserted rows: `question_source='past_paper'`, inherit the KO question's
  `category_id` / `subcategory_id` / `approved`, carry `options_json` when the source
  exam question had one.
- Cap: 5 matches for one KO point → only 3 kept (1 replace + 2 insert).
- Single match per KO point → identical to current 1:1 replace (regression guard).
- `used_pp_ids` dedupe: the same exam question is never attached to two KO points.

**Frontend:** verified in preview (chip on KO rows; banner with per-paper breakdown;
hidden for past-paper batches; `total=0` guard).

## Out of scope (YAGNI)

- A standalone Coverage screen (folded into existing pages by decision).
- Cross-subject / cross-topic aggregate coverage dashboards.
- Carrying exam figures (`image_id`) into blended questions.
- Re-running Blend or re-matching from the coverage UI.
- Partial-credit or fuzzy "near-duplicate" detection beyond the prompt instruction.
