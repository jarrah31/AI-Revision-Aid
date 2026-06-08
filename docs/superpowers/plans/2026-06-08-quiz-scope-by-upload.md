# Quiz Scope by Upload — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user scope a quiz to one or more specific uploads by selecting them in the "Questions come from" list, and show full (untruncated) upload filenames.

**Architecture:** Add a `_batch_filter(batch_ids)` helper to `backend/routers/quiz.py` (mirroring `_cat_subcat_filter`), AND-composed into `/quiz/count` and `/quiz/start`. Make the existing "Questions come from" rows in `frontend/pages/quiz.html` selectable toggles that feed a `batch_ids` filter; de-truncate the filenames. Live-setup-only — no DB migration, no session persistence.

**Tech Stack:** FastAPI + SQLite (backend), Alpine.js v3 (frontend, no build step). Tests: pytest (`venv/bin/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-06-08-quiz-scope-by-upload-design.md`

**Conventions (from CLAUDE.md):**
- Run tests with `venv/bin/python -m pytest` (no system pytest).
- Never stage `data/*.db*`. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do NOT bump `APP_VERSION`, tag, or push — publishing is a separate, user-requested step.

---

## File Structure

**Modify:**
- `backend/routers/quiz.py` — new `_batch_filter`; `batch_ids` param on `/count`; `batch_ids` field on `QuizStartRequest` + applied in `/start`.
- `frontend/pages/quiz.html` — `selectedBatchIds` state, `toggleBatch`, watcher, count/start params, prune-on-reload, selectable + de-truncated rows, header count + hint.
- `tests/test_quiz.py` — `_batch_filter` unit + count/start batch-filter tests.

**Context — existing patterns this mirrors (already in `backend/routers/quiz.py`):**
- `_cat_subcat_filter(category_ids, subcategory_ids)` returns `(sql_fragment, params)` or `(None, [])`.
- `/count` (`get_question_count`) builds `conditions=["q.user_id = ?", "q.approved = 1"]` + `params=[user["id"]]`, then appends `subject_id`, the category filter, the source filter.
- `/start` (`start_quiz`) builds the same `conditions`/`params` and applies the same three filters before `where = " AND ".join(conditions)`.
- `tests/test_quiz.py` has `_blend_fixture(db, uid, sid, make_batch, make_question)` → returns `(ko, pp)` batch ids: a KO booklet (`booklet.pdf`) with 2 `ai_generated` + 1 `past_paper` ("blended exam") question, and a standalone past-paper batch (`exam.pdf`) with 2 `past_paper` questions ("standalone 1"/"standalone 2"). Also `_set_batch_type(db, batch_id, t)`.

---

## Task 1: Backend `_batch_filter` + wire into `/quiz/count`

**Files:**
- Modify: `backend/routers/quiz.py` (add `_batch_filter` near `_source_filter` ~line 150; apply in `get_question_count` after the source filter ~line 221)
- Test: `tests/test_quiz.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_quiz.py` (it already imports nothing special; add the `_batch_filter` import at the top of the new test or inline):

```python
def test_batch_filter_helper():
    from backend.routers.quiz import _batch_filter
    assert _batch_filter(None) == (None, [])
    assert _batch_filter([]) == (None, [])
    assert _batch_filter([5]) == ("q.batch_id IN (?)", [5])
    assert _batch_filter([5, 9]) == ("q.batch_id IN (?,?)", [5, 9])


def test_count_filters_by_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    ko, pp = _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    def count(*batch_ids, sources=()):
        qs = "".join(f"&batch_ids={b}" for b in batch_ids)
        qs += "".join(f"&question_sources={s}" for s in sources)
        r = client.get(f"/api/quiz/count?subject_id={sid}{qs}", headers=user_headers)
        assert r.status_code == 200
        return r.json()["count"]

    assert count() == 5                         # no batch filter = everything
    assert count(ko) == 3                        # the KO booklet's 2 AI + 1 blended
    assert count(pp) == 2                        # standalone past-paper batch only
    assert count(ko, pp) == 5                    # union of both
    # composes with the source filter: the KO booklet's matched exam question only
    r = client.get(
        f"/api/quiz/count?subject_id={sid}&batch_ids={ko}"
        f"&question_sources=blended&blended_mode=exam_only",
        headers=user_headers,
    )
    assert r.json()["count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_quiz.py -k "batch_filter_helper or count_filters_by_batch" -q`
Expected: FAIL — `_batch_filter` not defined (and the count endpoint ignores `batch_ids`).

- [ ] **Step 3: Add the `_batch_filter` helper**

In `backend/routers/quiz.py`, immediately AFTER the `_source_filter` function (it ends with `return "(" + " OR ".join(clauses) + ")", []`), add:

```python
def _batch_filter(batch_ids: list[int] | None):
    """Return (sql_fragment, params) restricting to specific upload batches.

    Empty/None → (None, []) (no filter — all uploads). Mirrors _cat_subcat_filter:
    a plain AND on q.batch_id, composable with the subject/category/source filters.
    """
    if not batch_ids:
        return None, []
    ph = ",".join("?" * len(batch_ids))
    return f"q.batch_id IN ({ph})", list(batch_ids)
```

- [ ] **Step 4: Add the `batch_ids` param + apply it in `get_question_count`**

In the `get_question_count` signature, add `batch_ids` alongside the other `Query(None)` params (e.g. after `subcategory_ids`):

```python
    subcategory_ids: list[int] | None = Query(None),
    batch_ids: list[int] | None = Query(None),
    question_sources: list[str] | None = Query(None),
```

In the body, AFTER the source-filter block (the `src_filter, src_params = _source_filter(...)` / `if src_filter:` lines), add:

```python
    batch_f, batch_p = _batch_filter(batch_ids)
    if batch_f:
        conditions.append(batch_f)
        params.extend(batch_p)
```

(The existing count==0 diagnostic below is unaffected — it intentionally re-runs only the source filter without category/approval; an extra batch AND that yields 0 is fine.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_quiz.py -k "batch_filter_helper or count_filters_by_batch" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/quiz.py tests/test_quiz.py
git commit -m "feat: filter quiz count by upload batch_ids

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `batch_ids` on `/quiz/start`

**Files:**
- Modify: `backend/routers/quiz.py` (`QuizStartRequest` ~line 44; `start_quiz` filter block ~line 334)
- Test: `tests/test_quiz.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_quiz.py`:

```python
def test_start_quiz_filters_by_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """Starting a quiz with batch_ids draws questions only from those uploads."""
    uid, _ = regular_user
    sid = make_subject()
    ko, pp = _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    # Restrict to the standalone past-paper batch → only its two questions.
    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "batch_ids": [pp], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    assert texts == {"standalone 1", "standalone 2"}

    # Restrict to the KO booklet → its 3 questions, none from the standalone batch.
    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "batch_ids": [ko], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    assert "standalone 1" not in texts and "standalone 2" not in texts
    assert "blended exam" in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_quiz.py::test_start_quiz_filters_by_batch -q`
Expected: FAIL — `batch_ids` ignored, so the standalone-only assertion fails (KO questions also returned).

- [ ] **Step 3: Add `batch_ids` to the request model**

In `QuizStartRequest`, add (after `subcategory_ids`):

```python
    batch_ids: list[int] | None = None          # restrict to specific uploads; None/empty = all
```

- [ ] **Step 4: Apply the filter in `start_quiz`**

In `start_quiz`, AFTER the source-filter block (`src_filter, src_params = _source_filter(req.question_sources, req.blended_mode)` / `if src_filter:` …), and BEFORE `where = " AND ".join(conditions)`, add:

```python
    batch_f, batch_p = _batch_filter(req.batch_ids)
    if batch_f:
        conditions.append(batch_f)
        params.extend(batch_p)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_quiz.py::test_start_quiz_filters_by_batch -q`
Expected: PASS.

Then the full quiz suite: `venv/bin/python -m pytest tests/test_quiz.py -q` — expect PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/quiz.py tests/test_quiz.py
git commit -m "feat: scope quiz start to selected upload batch_ids

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Frontend — selectable uploads + de-truncated names

**Files:**
- Modify: `frontend/pages/quiz.html`

(No automated test — Alpine fragment. Verify by tag balance + grep + the manual steps in Step 7.)

- [ ] **Step 1: Add `selectedBatchIds` state**

Find:
```html
    sources: [],             // provenance: uploads contributing to the current selection
    showSources: false,      // provenance panel expanded?
```
Replace with:
```html
    sources: [],             // provenance: uploads contributing to the current selection
    selectedBatchIds: [],    // [] = all uploads; otherwise restrict the quiz to these batch ids
    showSources: false,      // provenance panel expanded?
```

- [ ] **Step 2: Watch `selectedBatchIds` to recompute the count**

Find:
```html
        this.$watch('blendedMode',          () => this.loadAvailableCount());
```
Replace with:
```html
        this.$watch('blendedMode',          () => this.loadAvailableCount());
        this.$watch('selectedBatchIds',     () => this.loadAvailableCount());
```

- [ ] **Step 3: Add a `toggleBatch` method and pass `batch_ids` to count + start**

Find the `loadSources()` method:
```html
    loadSources() {
        clearTimeout(this._sourcesTimer);
        this._sourcesTimer = setTimeout(async () => {
            const params = new URLSearchParams();
            if (this.subjectId) params.set('subject_id', this.subjectId);
            this.selectedCategoryIds.forEach(id => params.append('category_ids', id));
            this.selectedSubcategoryIds.forEach(id => params.append('subcategory_ids', id));
            try {
                this.sources = await API.get('/quiz/sources?' + params.toString());
            } catch (e) { this.sources = []; }
        }, 200);
    },
```
Replace with (adds stale-selection pruning after the fetch, plus a `toggleBatch` method):
```html
    loadSources() {
        clearTimeout(this._sourcesTimer);
        this._sourcesTimer = setTimeout(async () => {
            const params = new URLSearchParams();
            if (this.subjectId) params.set('subject_id', this.subjectId);
            this.selectedCategoryIds.forEach(id => params.append('category_ids', id));
            this.selectedSubcategoryIds.forEach(id => params.append('subcategory_ids', id));
            try {
                this.sources = await API.get('/quiz/sources?' + params.toString());
            } catch (e) { this.sources = []; }
            // Drop any selected upload that's no longer in the list (e.g. after a
            // category change) so it can't keep filtering invisibly.
            this.selectedBatchIds = this.selectedBatchIds.filter(
                id => this.sources.some(s => s.batch_id === id));
        }, 200);
    },

    toggleBatch(id) {
        const i = this.selectedBatchIds.indexOf(id);
        if (i === -1) this.selectedBatchIds = [...this.selectedBatchIds, id];
        else this.selectedBatchIds = this.selectedBatchIds.filter(b => b !== id);
        if (this.selectedBatchIds.length > 0) this.showSources = true;
    },
```

- [ ] **Step 4: Append `batch_ids` to the count request**

Find (inside `loadAvailableCount`):
```html
            this.questionSources.forEach(s => params.append('question_sources', s));
            if (this.blendedSelected()) params.set('blended_mode', this.blendedMode);
```
Replace with:
```html
            this.questionSources.forEach(s => params.append('question_sources', s));
            this.selectedBatchIds.forEach(id => params.append('batch_ids', id));
            if (this.blendedSelected()) params.set('blended_mode', this.blendedMode);
```

- [ ] **Step 5: Pass `batch_ids` in the start body**

Find (inside `startQuiz`, the `/quiz/start` POST body):
```html
                question_sources: this.questionSources.length > 0 ? this.questionSources : null,
```
Replace with:
```html
                question_sources: this.questionSources.length > 0 ? this.questionSources : null,
                batch_ids: this.selectedBatchIds.length > 0 ? this.selectedBatchIds : null,
```

- [ ] **Step 6: Make the rows selectable + de-truncate the filename + header count + hint**

(6a) Header count — find:
```html
                        Questions come from
                        <span class="text-gray-400 font-normal" x-text="'(' + sources.length + ' upload' + (sources.length !== 1 ? 's' : '') + ')'"></span>
                    </button>
```
Replace with:
```html
                        Questions come from
                        <span class="text-gray-400 font-normal"
                              x-text="'(' + sources.length + ' upload' + (sources.length !== 1 ? 's' : '') + (selectedBatchIds.length ? ' · ' + selectedBatchIds.length + ' selected' : '') + ')'"></span>
                    </button>
```

(6b) Selectable rows + hint + de-truncate — find:
```html
                    <div x-show="showSources"
                         class="mt-2 border border-gray-100 rounded-lg divide-y divide-gray-50 max-h-48 overflow-y-auto">
                        <template x-for="s in sources" :key="s.batch_id">
                            <div class="flex items-center justify-between gap-3 px-3 py-1.5 text-xs">
                                <span class="min-w-0 flex items-center gap-1.5">
                                    <span x-text="sourceIcon(s)"></span>
                                    <span class="font-mono text-gray-600 truncate" :title="s.filename" x-text="s.filename"></span>
                                </span>
                                <span class="text-gray-400 whitespace-nowrap" x-text="sourceCountLabel(s)"></span>
                            </div>
                        </template>
                    </div>
```
Replace with:
```html
                    <p x-show="showSources" class="text-xs text-gray-400 mt-1 mb-1">Tap an upload to limit the quiz to it (none selected = all).</p>
                    <div x-show="showSources"
                         class="mt-1 border border-gray-100 rounded-lg divide-y divide-gray-50 max-h-48 overflow-y-auto">
                        <template x-for="s in sources" :key="s.batch_id">
                            <button type="button" @click="toggleBatch(s.batch_id)"
                                    class="w-full flex items-center justify-between gap-3 px-3 py-1.5 text-xs text-left transition"
                                    :class="selectedBatchIds.includes(s.batch_id) ? 'bg-indigo-50' : 'hover:bg-gray-50'">
                                <span class="min-w-0 flex items-start gap-1.5">
                                    <span x-text="selectedBatchIds.includes(s.batch_id) ? '✓' : sourceIcon(s)"
                                          :class="selectedBatchIds.includes(s.batch_id) ? 'text-indigo-600 font-bold' : ''"></span>
                                    <span class="font-mono break-words" :title="s.filename" x-text="s.filename"
                                          :class="selectedBatchIds.includes(s.batch_id) ? 'text-indigo-700 font-medium' : 'text-gray-600'"></span>
                                </span>
                                <span class="text-gray-400 whitespace-nowrap" x-text="sourceCountLabel(s)"></span>
                            </button>
                        </template>
                    </div>
```

- [ ] **Step 7: Verify the markup**

Run a tag-balance check (you added/removed equal numbers; net div delta is −1 because the row `<div>` became a `<button>`, and you added one `<p>` and one `<button>`):
```bash
venv/bin/python -c "s=open('frontend/pages/quiz.html').read(); print('div', s.count('<div'), s.count('</div>'), '| button', s.count('<button'), s.count('</button>'))"
```
Expected: the `<div`/`</div>` counts match each other, and `<button`/`</button>` counts match each other (balanced). Also:
```bash
grep -n "toggleBatch\|selectedBatchIds\|break-words\|Tap an upload" frontend/pages/quiz.html
```
Expected: shows the new state field, the watcher, `toggleBatch` (definition + the row `@click`), the count/start params, the prune line, and the de-truncated `break-words` filename.

Manual smoke (optional but recommended): `python run.py`, open the quiz setup, expand "Questions come from", click an upload → it highlights with a ✓, the header shows "· 1 selected", and "Questions: N / available" updates to that upload's count. Start the quiz and confirm questions come only from the selected upload. Click again to deselect → back to all.

- [ ] **Step 8: Commit**

```bash
git add frontend/pages/quiz.html
git commit -m "feat: select uploads in quiz setup to scope questions; show full names

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all pass (the 308 prior + the 3 new backend tests).

- [ ] **Step 2: Confirm scope**

Confirm `git status` shows no `data/*.db*` staged across the feature commits, and that `backend/app.py` `APP_VERSION` is unchanged (no release performed). Report the suite result.

---

## Self-Review Notes

- **Spec coverage:** multi-select batch filter on count (Task 1) + start (Task 2); `_batch_filter` mirrors `_cat_subcat_filter`; `/quiz/sources` untouched; selectable rows + header count + auto-expand + hint + stale-selection pruning + de-truncated names (Task 3); live-setup-only, no migration/persistence (no such task — correct). All spec sections map to a task.
- **Type/name consistency:** `_batch_filter(batch_ids) -> (sql|None, list)`; query/body field name `batch_ids` (backend) ↔ `selectedBatchIds` (frontend state) ↔ `batch_ids` (request params/body); `toggleBatch(id)`. Used identically across tasks.
- **Composition:** `batch_ids` is ANDed after the source filter in both endpoints, so `blended_mode='exam_only' + batch_ids=<KO>` yields exactly that booklet's matched exam questions (asserted in Task 1).
