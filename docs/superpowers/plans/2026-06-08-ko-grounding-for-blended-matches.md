# KO Grounding for Blended Matches — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the text matcher blends a KO booklet, run a vision "grounding" pass that verifies each matched exam question against the KO page, drops unsupported matches, and stores written reasoning + a cropped KO region shown in the admin card and the quiz "Show Source" panel.

**Architecture:** A new `ground_matches_to_ko` service call (vision) runs inside `_match_and_replace_with_past_papers` after `match_ko_to_past_papers`. It returns, per candidate, `{supported, reasoning, bbox_pct, snippet}`. Unsupported candidates are dropped; supported ones get their KO region cropped to a PNG and their reasoning + crop path written onto the `questions` row (two new columns). Provenance surfaces both to the quiz; the admin card reads the columns directly.

**Tech Stack:** Python 3 / FastAPI / SQLite, Anthropic SDK (vision messages), Pillow (cropping), Alpine.js v3 frontend. Tests: pytest (in-memory SQLite, AI calls mocked).

**Spec:** `docs/superpowers/specs/2026-06-08-ko-grounding-for-blended-matches-design.md`

**Conventions (from CLAUDE.md):**
- Run tests with `venv/bin/python -m pytest` (system pytest is absent).
- DB migrations: add columns via the `_add_column_if_missing` list in `init_db()` (`backend/database.py`).
- Never commit `data/*.db*`. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Do NOT bump `APP_VERSION` or tag/release in this plan — publishing is a separate, user-requested step.

---

## File Structure

**Create:**
- `backend/prompts/grounding.py` — `GROUNDING_PROMPT` string constant.
- `tests/test_claude_service_grounding.py` — unit tests for `ground_matches_to_ko` (AI mocked).

**Modify:**
- `backend/database.py` — two new `questions` columns in the migration list.
- `backend/services/pdf_processor.py` — `downscale_png()` + `save_ko_crop()` helpers.
- `backend/services/claude_service.py` — `GROUNDING_MODEL`, settings registration, `_valid_bbox()`, `ground_matches_to_ko()`.
- `backend/routers/admin.py` — register the two new AI settings in `_AI_SETTING_METADATA`.
- `backend/routers/upload.py` — call grounding in `_match_and_replace_with_past_papers`; write columns; clear them in `_restore_blend`; helper `_load_ko_page_png()`.
- `backend/routers/quiz.py` — `_attach_source_meta` adds `ko_reasoning` + `ko_crop_url`.
- `frontend/pages/review-category.html` — KO crop + reasoning in the admin Source panel.
- `frontend/pages/quiz.html` — KO crop + reasoning in the quiz Source panel.
- `tests/test_upload.py`, `tests/test_quiz.py`, `tests/test_admin.py` — extend coverage.

---

## Task 1: DB migration — two new `questions` columns

**Files:**
- Modify: `backend/database.py` (the `_add_column_if_missing` list inside `init_db()`, near line 362 where `answer_from_mark_scheme` is added)
- Test: `tests/test_upload.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_upload.py` (near the other migration/flag tests):

```python
def test_questions_table_has_ko_grounding_columns(db_conn):
    """The blend-grounding migration adds reasoning + crop columns to questions."""
    cols = {row[1] for row in db_conn.execute("PRAGMA table_info(questions)").fetchall()}
    assert "ko_grounding_reasoning" in cols
    assert "ko_crop_filename" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_upload.py::test_questions_table_has_ko_grounding_columns -q`
Expected: FAIL — columns missing.

- [ ] **Step 3: Add the migration**

In `backend/database.py`, in the list passed to `_add_column_if_missing` (same list that ends with the `answer_from_mark_scheme` line), append:

```python
        "ALTER TABLE questions ADD COLUMN ko_grounding_reasoning TEXT DEFAULT NULL",
        "ALTER TABLE questions ADD COLUMN ko_crop_filename TEXT DEFAULT NULL",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_upload.py::test_questions_table_has_ko_grounding_columns -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_upload.py
git commit -m "feat: add ko_grounding_reasoning + ko_crop_filename columns

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `pdf_processor` — `downscale_png` + `save_ko_crop`

**Files:**
- Modify: `backend/services/pdf_processor.py` (after `crop_section_to_bytes`, before `png_to_base64`)
- Test: `tests/test_pdf_processor.py` (create if absent)

- [ ] **Step 1: Write the failing tests**

Create/extend `tests/test_pdf_processor.py`:

```python
import io
from PIL import Image
from backend.services import pdf_processor as pp


def _png(w, h, color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_downscale_png_shrinks_large_image():
    out = pp.downscale_png(_png(3000, 2000), max_px=1100)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 1100
    assert img.size == (1100, 733)  # aspect preserved


def test_downscale_png_leaves_small_image_untouched():
    src = _png(800, 600)
    out = pp.downscale_png(src, max_px=1100)
    assert Image.open(io.BytesIO(out)).size == (800, 600)


def test_save_ko_crop_writes_file_and_returns_relative_path(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "DATA_DIR", tmp_path)
    rel = pp.save_ko_crop(
        batch_id=42, page_number=3, ko_id=7, pp_id=9,
        png_bytes=_png(1000, 1000),
        bbox_pct={"x": 25, "y": 25, "w": 50, "h": 50},
    )
    assert rel == "batch_42/page_3_kocrop_7_9.png"
    saved = tmp_path / "images" / rel
    assert saved.exists()
    # padded 50%-region crop is smaller than the full page
    assert max(Image.open(saved).size) < 1000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_pdf_processor.py -q`
Expected: FAIL — `downscale_png` / `save_ko_crop` not defined.

- [ ] **Step 3: Implement the helpers**

In `backend/services/pdf_processor.py`, after `crop_section_to_bytes`:

```python
def downscale_png(png_bytes: bytes, max_px: int = 1100) -> bytes:
    """Return PNG bytes scaled so the longest side is at most max_px.

    Cheaper vision input and good enough for a coarse bounding box. Images already
    within bounds are returned unchanged.
    """
    img = Image.open(io.BytesIO(png_bytes))
    longest = max(img.size)
    if longest <= max_px:
        return png_bytes
    scale = max_px / longest
    new_size = (round(img.size[0] * scale), round(img.size[1] * scale))
    resized = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def save_ko_crop(
    batch_id: int,
    page_number: int,
    ko_id: int,
    pp_id: int,
    png_bytes: bytes,
    bbox_pct: dict,
    padding_pct: float = 6.0,
) -> str:
    """Crop the KO region that grounds a blended match and save it under a name
    keyed by the KO and past-paper question ids. Returns the relative filename
    (served by the /images mount). bbox_pct keys: x, y, w, h (percent of page).
    """
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    x1 = max(0, int((bbox_pct["x"] - padding_pct) / 100 * w))
    y1 = max(0, int((bbox_pct["y"] - padding_pct) / 100 * h))
    x2 = min(w, int((bbox_pct["x"] + bbox_pct["w"] + padding_pct) / 100 * w))
    y2 = min(h, int((bbox_pct["y"] + bbox_pct["h"] + padding_pct) / 100 * h))
    if x2 <= x1 or y2 <= y1:          # degenerate box → fall back to full page
        x1, y1, x2, y2 = 0, 0, w, h
    cropped = img.crop((x1, y1, x2, y2))

    batch_dir = DATA_DIR / "images" / f"batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    filename = f"page_{page_number}_kocrop_{ko_id}_{pp_id}.png"
    cropped.save(batch_dir / filename, "PNG", optimize=True)
    return f"batch_{batch_id}/{filename}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_pdf_processor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/pdf_processor.py tests/test_pdf_processor.py
git commit -m "feat: downscale_png + save_ko_crop image helpers for KO grounding

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Grounding prompt module

**Files:**
- Create: `backend/prompts/grounding.py`

- [ ] **Step 1: Create the prompt constant**

Create `backend/prompts/grounding.py`:

```python
GROUNDING_PROMPT = """You are checking whether a Knowledge Organiser (KO) revision page genuinely supports a set of candidate exam questions that have been matched to one KO point.

You are given an IMAGE of the KO page, and JSON:

{payload}

The JSON has:
- "ko_point": the KO revision point ("ko_question", "ko_answer").
- "candidates": exam questions matched to it, each with a unique "id", "question", "answer".

For EACH candidate, look at the KO page image and decide whether the page genuinely contains the knowledge needed to answer that exam question — the same specific fact/concept, consistent with the candidate's answer.

For each candidate return:
- "id": the candidate id (unchanged).
- "supported": true only if the KO page genuinely contains the supporting knowledge; false otherwise. Be strict — superficial topic overlap is NOT support.
- "reasoning": one or two sentences, student-friendly, naming where on the page the answer is found (e.g. "The 'Levels of Organisation' box defines an organ as a group of tissues."). For unsupported candidates, briefly say why not.
- "snippet": the exact short phrase quoted from the KO page that supports it (empty string if unsupported).
- "bbox_pct": the approximate rectangle on the page containing that snippet, as percentages of the page: {{"x": <left%>, "y": <top%>, "w": <width%>, "h": <height%>}}. A generous, approximate box is fine. Use null if unsupported or you cannot locate it.

Return ONLY valid JSON, no prose:
{{"results": [{{"id": 456, "supported": true, "reasoning": "...", "snippet": "...", "bbox_pct": {{"x": 5, "y": 10, "w": 40, "h": 12}}}}]}}
"""
```

- [ ] **Step 2: Verify it imports**

Run: `venv/bin/python -c "from backend.prompts.grounding import GROUNDING_PROMPT; assert '{payload}' in GROUNDING_PROMPT; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/prompts/grounding.py
git commit -m "feat: grounding prompt for KO-vs-exam-question verification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `claude_service` — `ground_matches_to_ko`

**Files:**
- Modify: `backend/services/claude_service.py` (model constant near line 33; `AI_SETTING_DEFAULTS` near line 65; prompt import near the other prompt imports; new code after `match_ko_to_past_papers`)
- Test: `tests/test_claude_service_grounding.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_service_grounding.py`:

```python
"""Unit tests for ground_matches_to_ko (vision call mocked)."""
import json
import types

import backend.services.claude_service as cs


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn", in_tok=50, out_tok=30):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok)
        self.stop_reason = stop_reason


def _client_returning(fn):
    class _C:
        class messages:
            @staticmethod
            def create(**kwargs):
                return fn(kwargs)
    return _C()


def _stub_settings(monkeypatch):
    monkeypatch.setattr(cs, "_get_ai_setting", lambda k: cs.AI_SETTING_DEFAULTS[k])


KO = {"id": 1, "question_text": "What is an organ?", "answer_text": "a group of tissues"}
CANDS = [
    {"id": 10, "question_text": "Name a group of tissues working together", "answer_text": "organ"},
    {"id": 11, "question_text": "Define homeostasis", "answer_text": "keeping internal conditions stable"},
]


def test_parses_supported_and_unsupported(monkeypatch):
    _stub_settings(monkeypatch)
    body = json.dumps({"results": [
        {"id": 10, "supported": True, "reasoning": "Organ defined in the box.",
         "snippet": "Organ: a group of tissues", "bbox_pct": {"x": 5, "y": 10, "w": 40, "h": 12}},
        {"id": 11, "supported": False, "reasoning": "Homeostasis not on this page.",
         "snippet": "", "bbox_pct": None},
    ]})
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(lambda kw: _FakeMessage(body)))

    results, usage = cs.ground_matches_to_ko(KO, b"\x89PNG-fake", CANDS)
    by_id = {r["past_paper_question_id"]: r for r in results}
    assert by_id[10]["supported"] is True
    assert by_id[10]["bbox_pct"] == {"x": 5.0, "y": 10.0, "w": 40.0, "h": 12.0}
    assert by_id[11]["supported"] is False
    assert by_id[11]["bbox_pct"] is None
    assert usage["cost_usd"] > 0


def test_image_block_is_sent(monkeypatch):
    _stub_settings(monkeypatch)
    captured = {}
    def grab(kw):
        captured["content"] = kw["messages"][0]["content"]
        return _FakeMessage(json.dumps({"results": []}))
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(grab))
    cs.ground_matches_to_ko(KO, b"fake-png-bytes", CANDS)
    kinds = [b["type"] for b in captured["content"]]
    assert "image" in kinds and "text" in kinds


def test_invalid_bbox_becomes_none(monkeypatch):
    _stub_settings(monkeypatch)
    body = json.dumps({"results": [
        {"id": 10, "supported": True, "reasoning": "x", "snippet": "y",
         "bbox_pct": {"x": 5, "y": 10, "w": 0, "h": 12}},  # zero width → invalid
    ]})
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(lambda kw: _FakeMessage(body)))
    results, _ = cs.ground_matches_to_ko(KO, b"png", [CANDS[0]])
    assert results[0]["bbox_pct"] is None
    assert results[0]["supported"] is True


def test_parse_failure_keeps_all_supported_and_records_cost(monkeypatch):
    """A garbled body must NOT drop real matches — they survive ungrounded, and
    the call still records its cost."""
    _stub_settings(monkeypatch)
    monkeypatch.setattr(cs, "get_client",
                        lambda: _client_returning(lambda kw: _FakeMessage("not json at all")))
    results, usage = cs.ground_matches_to_ko(KO, b"png", CANDS)
    assert all(r["supported"] for r in results)
    assert all(r["bbox_pct"] is None for r in results)
    assert usage["cost_usd"] > 0


def test_api_error_keeps_all_supported(monkeypatch):
    _stub_settings(monkeypatch)
    def boom(kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(boom))
    results, usage = cs.ground_matches_to_ko(KO, b"png", CANDS)
    assert [r["past_paper_question_id"] for r in results] == [10, 11]
    assert all(r["supported"] for r in results)
    assert usage["cost_usd"] == 0.0


def test_no_candidates_makes_no_call(monkeypatch):
    _stub_settings(monkeypatch)
    def boom(kw):
        raise AssertionError("should not be called")
    monkeypatch.setattr(cs, "get_client", lambda: _client_returning(boom))
    results, usage = cs.ground_matches_to_ko(KO, b"png", [])
    assert results == []
    assert usage["cost_usd"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_claude_service_grounding.py -q`
Expected: FAIL — `ground_matches_to_ko` not defined.

- [ ] **Step 3: Add model constant, settings, prompt import**

In `backend/services/claude_service.py`:

Near the model constants (after `FACT_CHECK_MODEL`, line ~33):

```python
GROUNDING_MODEL        = "claude-sonnet-4-6"   # Vision — verify a match against the KO page
```

With the other `from backend.prompts...` imports at the top of the file, add:

```python
from backend.prompts.grounding import GROUNDING_PROMPT
```

In `AI_SETTING_DEFAULTS`, add a model entry (with the other models) and a prompt entry (with the other prompts):

```python
    "ai_model_grounding":           GROUNDING_MODEL,
```
```python
    "ai_prompt_grounding":          GROUNDING_PROMPT,
```

- [ ] **Step 4: Implement `_valid_bbox` and `ground_matches_to_ko`**

Add after `match_ko_to_past_papers` (end of file region):

```python
def _valid_bbox(d) -> dict | None:
    """Coerce a model-returned bbox into {x,y,w,h} floats, or None if implausible.

    Localization is forgiving by design (small models are loose); we only reject
    boxes we can't use: non-dict, missing keys, non-positive size, or out of the
    0–100 percentage range.
    """
    if not isinstance(d, dict):
        return None
    try:
        x, y, w, h = float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or not (0 <= x <= 100 and 0 <= y <= 100):
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def ground_matches_to_ko(
    ko_point: dict, ko_page_png: bytes | None, candidates: list[dict]
) -> tuple[list[dict], dict]:
    """Vision-verify each candidate exam question against the KO page image.

    Returns (results, usage). Each result:
      {"past_paper_question_id": int, "supported": bool, "reasoning": str,
       "bbox_pct": {x,y,w,h}|None, "snippet": str}

    Non-fatal philosophy: the quality gate only drops a match on an EXPLICIT
    supported=false. An API/parse failure, an omitted candidate, or a missing
    image keeps the match (supported, no crop) — an infra hiccup must never
    silently empty a blend.
    """
    empty_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": None}
    if not candidates:
        return [], empty_usage

    from backend.services.pdf_processor import downscale_png, png_to_base64

    model       = _get_ai_setting("ai_model_grounding")
    prompt_tmpl = _get_ai_setting("ai_prompt_grounding")

    payload = json.dumps({
        "ko_point": {"ko_question": ko_point.get("question_text") or "",
                     "ko_answer":   ko_point.get("answer_text") or ""},
        "candidates": [{"id": c["id"],
                        "question": c.get("question_text") or "",
                        "answer":   c.get("answer_text") or ""} for c in candidates],
    }, indent=2)
    prompt = prompt_tmpl.format(payload=payload)

    def _keep_all(reason: str = "") -> list[dict]:
        return [{"past_paper_question_id": c["id"], "supported": True,
                 "reasoning": reason, "bbox_pct": None, "snippet": ""} for c in candidates]

    content: list[dict] = [{"type": "text", "text": prompt}]
    if ko_page_png:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": png_to_base64(downscale_png(ko_page_png))}})

    client = get_client()
    try:
        message = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        logger.warning("ground: API call failed (non-fatal, keeping %d match(es)): %s",
                       len(candidates), e)
        return _keep_all(), {**empty_usage, "model": model}

    usage = _calc_usage(message, model)
    try:
        if message.stop_reason == "max_tokens":
            raise ValueError("response truncated (max_tokens)")
        result = _loads_json_response(message)
    except Exception as e:
        logger.warning("ground: could not parse response (non-fatal, keeping %d "
                       "match(es)): %s [raw_preview=%r]",
                       len(candidates), e, _message_text(message)[:300])
        return _keep_all(), usage

    by_id = {r.get("id"): r for r in result.get("results", []) if r.get("id") is not None}
    out: list[dict] = []
    for c in candidates:
        r = by_id.get(c["id"])
        if r is None:                       # model omitted it → keep, no crop
            out.append({"past_paper_question_id": c["id"], "supported": True,
                        "reasoning": "", "bbox_pct": None, "snippet": ""})
            continue
        out.append({
            "past_paper_question_id": c["id"],
            "supported": bool(r.get("supported", True)),
            "reasoning": (r.get("reasoning") or "").strip(),
            "bbox_pct": _valid_bbox(r.get("bbox_pct")),
            "snippet": (r.get("snippet") or "").strip(),
        })
    logger.info("ground: KO %s — %d/%d candidate(s) supported (cost $%.4f)",
                ko_point.get("id"), sum(1 for r in out if r["supported"]),
                len(out), usage["cost_usd"])
    return out, usage
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_claude_service_grounding.py -q`
Expected: PASS (all 6).

- [ ] **Step 6: Commit**

```bash
git add backend/services/claude_service.py tests/test_claude_service_grounding.py
git commit -m "feat: ground_matches_to_ko vision verification service

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Register the two new AI settings in Admin

**Files:**
- Modify: `backend/routers/admin.py` (`_AI_SETTING_METADATA`, near line 374/380)
- Test: `tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Find how the settings endpoint is tested in `tests/test_admin.py` (search for `ai_model_matching` or `/admin` settings). Add:

```python
def test_ai_settings_include_grounding(client, admin_headers):
    """The grounding model + prompt are exposed in AI Settings."""
    r = client.get("/api/admin/ai-settings", headers=admin_headers)
    assert r.status_code == 200
    keys = {item["key"] for item in r.json()["settings"]}
    assert "ai_model_grounding" in keys
    assert "ai_prompt_grounding" in keys
```

> The endpoint is `GET /api/admin/ai-settings` and returns
> `{"settings": [...], "available_models": [...]}`; each settings item has a `"key"`.
> `admin_headers` is the existing fixture used throughout `tests/test_admin.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_admin.py::test_ai_settings_include_grounding -q`
Expected: FAIL — keys absent.

- [ ] **Step 3: Register the metadata**

In `backend/routers/admin.py`, in `_AI_SETTING_METADATA`, add (alongside the matching entries):

```python
    "ai_model_grounding":              {"label": "Model",  "type": "model",  "group": "Blended Match Grounding", "group_key": "grounding"},
    "ai_prompt_grounding":             {"label": "Prompt", "type": "prompt", "group": "Blended Match Grounding", "group_key": "grounding"},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_admin.py::test_ai_settings_include_grounding -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/admin.py tests/test_admin.py
git commit -m "feat: expose Blended Match Grounding model + prompt in Admin

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Wire grounding into the blend pipeline

**Files:**
- Modify: `backend/routers/upload.py`
  - imports (lines ~14-24): add `DATA_DIR, downscale_png, save_ko_crop`; add `ground_matches_to_ko` from claude_service
  - new helper `_load_ko_page_png`
  - `_match_and_replace_with_past_papers` per-KO loop (lines ~250-314)
  - `_restore_blend` revert UPDATE (lines ~340-355)
- Test: `tests/test_upload.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_upload.py`. These mirror the existing blend tests in that file:
module imported as `upload`, the `_set_past_paper(db_conn, batch_id)` helper already
defined there, the `isolated_db` fixture + `monkeypatch.setattr(upload, "DB_PATH",
isolated_db)`, and raw-SQL question inserts. They monkeypatch BOTH the matcher and the
grounding call (both are module-level names in `upload`):

```python
def _insert_q(db, batch_id, user_id, subject_id, source, text="q", answer="a", page=1):
    cur = db.execute(
        """INSERT INTO questions (batch_id, user_id, subject_id, page_number,
           question_text, answer_text, question_source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (batch_id, user_id, subject_id, page, text, answer, source))
    db.commit()
    return cur.lastrowid


def _patch_blend(monkeypatch, matches, ground_fn):
    """Stub the matcher + grounding so the blend runs purely on fixture data.
    ground_fn(ko_point, png, candidates) -> list[result dict]."""
    monkeypatch.setattr(upload, "match_ko_to_past_papers",
                        lambda ko, pp: (matches, dict(_MATCH_USAGE)))
    monkeypatch.setattr(upload, "ground_matches_to_ko",
                        lambda ko, png, cands: (ground_fn(ko, png, cands), dict(_MATCH_USAGE)))
    monkeypatch.setattr(upload, "_load_ko_page_png", lambda batch_id, page: b"fake-png")
    monkeypatch.setattr(upload, "save_ko_crop",
                        lambda **kw: f"batch_{kw['batch_id']}/page_{kw['page_number']}"
                                     f"_kocrop_{kw['ko_id']}_{kw['pp_id']}.png")


def test_grounding_drops_unsupported_match(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """A candidate the grounding step marks unsupported is NOT written into the blend."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    uid, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(uid, sid)
    pp_batch = make_batch(uid, sid)
    _set_past_paper(db_conn, pp_batch)
    ko_q = _insert_q(db_conn, ko_batch, uid, sid, "ai_generated", "What is an organ?")
    pp_q = _insert_q(db_conn, pp_batch, uid, sid, "past_paper", "Name a group of tissues")

    _patch_blend(monkeypatch,
                 [{"ko_question_id": ko_q, "past_paper_question_id": pp_q}],
                 lambda ko, png, cands: [
                     {"past_paper_question_id": c["id"], "supported": False,
                      "reasoning": "no", "bbox_pct": None, "snippet": ""} for c in cands])

    upload._match_and_replace_with_past_papers(ko_batch, uid, sid, db_conn)

    row = db_conn.execute("SELECT question_source, ko_grounding_reasoning FROM questions "
                          "WHERE id = ?", (ko_q,)).fetchone()
    assert row[0] == "ai_generated"   # stayed an AI question; nothing replaced
    assert row[1] is None


def test_grounding_persists_reasoning_and_crop(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    uid, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(uid, sid)
    pp_batch = make_batch(uid, sid)
    _set_past_paper(db_conn, pp_batch)
    ko_q = _insert_q(db_conn, ko_batch, uid, sid, "ai_generated", "What is an organ?")
    pp_q = _insert_q(db_conn, pp_batch, uid, sid, "past_paper", "Name a group of tissues")

    _patch_blend(monkeypatch,
                 [{"ko_question_id": ko_q, "past_paper_question_id": pp_q}],
                 lambda ko, png, cands: [
                     {"past_paper_question_id": c["id"], "supported": True,
                      "reasoning": "Defined in the Levels of Organisation box.",
                      "bbox_pct": {"x": 5, "y": 10, "w": 40, "h": 12},
                      "snippet": "Organ: tissues"} for c in cands])

    upload._match_and_replace_with_past_papers(ko_batch, uid, sid, db_conn)

    row = db_conn.execute(
        "SELECT question_source, ko_grounding_reasoning, ko_crop_filename "
        "FROM questions WHERE id = ?", (ko_q,)).fetchone()
    assert row[0] == "past_paper"
    assert row[1] == "Defined in the Levels of Organisation box."
    assert row[2] == f"batch_{ko_batch}/page_1_kocrop_{ko_q}_{pp_q}.png"


def test_grounding_invalid_bbox_stores_reasoning_without_crop(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    uid, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(uid, sid)
    pp_batch = make_batch(uid, sid)
    _set_past_paper(db_conn, pp_batch)
    ko_q = _insert_q(db_conn, ko_batch, uid, sid, "ai_generated")
    pp_q = _insert_q(db_conn, pp_batch, uid, sid, "past_paper")

    _patch_blend(monkeypatch,
                 [{"ko_question_id": ko_q, "past_paper_question_id": pp_q}],
                 lambda ko, png, cands: [
                     {"past_paper_question_id": c["id"], "supported": True,
                      "reasoning": "supported but unlocated", "bbox_pct": None,
                      "snippet": ""} for c in cands])

    upload._match_and_replace_with_past_papers(ko_batch, uid, sid, db_conn)
    row = db_conn.execute("SELECT ko_grounding_reasoning, ko_crop_filename FROM questions "
                          "WHERE id = ?", (ko_q,)).fetchone()
    assert row[0] == "supported but unlocated"
    assert row[1] is None


def test_restore_blend_clears_grounding(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    uid, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(uid, sid)
    pp_batch = make_batch(uid, sid)
    _set_past_paper(db_conn, pp_batch)
    ko_q = _insert_q(db_conn, ko_batch, uid, sid, "ai_generated", "What is an organ?")
    pp_q = _insert_q(db_conn, pp_batch, uid, sid, "past_paper", "Name a group of tissues")

    _patch_blend(monkeypatch,
                 [{"ko_question_id": ko_q, "past_paper_question_id": pp_q}],
                 lambda ko, png, cands: [
                     {"past_paper_question_id": c["id"], "supported": True, "reasoning": "r",
                      "bbox_pct": {"x": 1, "y": 1, "w": 10, "h": 10}, "snippet": "s"}
                     for c in cands])
    upload._match_and_replace_with_past_papers(ko_batch, uid, sid, db_conn)

    upload._restore_blend(ko_batch, db_conn)
    row = db_conn.execute("SELECT question_source, ko_grounding_reasoning, ko_crop_filename "
                          "FROM questions WHERE id = ?", (ko_q,)).fetchone()
    assert row[0] == "ai_generated"
    assert row[1] is None and row[2] is None
```

> `_MATCH_USAGE`, `_set_past_paper`, and the `upload` import already exist at the top of
> `tests/test_upload.py`. Raw inserts default `page_number=1`, so crop filenames use page 1.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_upload.py -k grounding -q`
Expected: FAIL — `up.ground_matches_to_ko` / `up._load_ko_page_png` not defined; columns not written.

- [ ] **Step 3: Add imports + `_load_ko_page_png` helper**

In `backend/routers/upload.py`, extend the pdf_processor import block:

```python
from backend.services.pdf_processor import (
    render_page_to_png,
    save_full_page_image,
    crop_image_region,
    png_to_base64,
    get_pdf_page_count,
    load_image_as_png_bytes,
    save_ko_crop,
    DATA_DIR,
)
```

Add `ground_matches_to_ko` to the `from backend.services.claude_service import (...)` block.
(`downscale_png` is used inside the grounding service, not here, so it is not imported in upload.)

Add this helper near the top of the module (after the imports / other module-level helpers):

```python
def _load_ko_page_png(batch_id: int, page_number: int) -> bytes | None:
    """Return the saved full-page PNG bytes for a KO page, or None if absent
    (e.g. a legacy batch processed before page images were stored)."""
    path = DATA_DIR / "images" / f"batch_{batch_id}" / f"page_{page_number}_full.png"
    try:
        return path.read_bytes()
    except OSError:
        return None
```

- [ ] **Step 4: Run grounding in the per-KO loop**

In `_match_and_replace_with_past_papers`, replace the per-KO loop body (the `for ko_q_id, pp_ids in grouped.items():` block, lines ~250-314) so grounding runs first, drops unsupported candidates, and the kept loop writes the new columns. Full replacement:

```python
    used_pp_ids: set[int] = set()
    replaced = 0
    inserted = 0
    grounding_dropped = 0
    # Grounding cost is tracked separately: the matcher's api_usage row + batch cost
    # were already written above, so grounding gets its own 'ko_grounding' row below.
    grounding_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": None}
    for ko_q_id, pp_ids in grouped.items():
        ko_q = next((q for q in ko_questions if q["id"] == ko_q_id), None)
        if not ko_q:
            continue

        # ── Grounding: vision-verify each candidate against the KO page, drop
        #    unsupported ones, and crop the supporting region for survivors. ──
        candidates, seen_c = [], set()
        for pid in pp_ids:
            if pid in seen_c:
                continue
            seen_c.add(pid)
            pp = next((q for q in past_paper_qs if q["id"] == pid), None)
            if pp:
                candidates.append(pp)

        ground: dict[int, dict] = {}  # pp_id -> {"reasoning", "crop_filename"}
        if candidates:
            ko_png = _load_ko_page_png(batch_id, ko_q["page_number"])
            results, g_usage = ground_matches_to_ko(ko_q, ko_png, candidates)
            grounding_usage["input_tokens"]  += g_usage["input_tokens"]
            grounding_usage["output_tokens"] += g_usage["output_tokens"]
            grounding_usage["cost_usd"]      += g_usage["cost_usd"]
            grounding_usage["model"] = g_usage.get("model") or grounding_usage["model"]
            for r in results:
                if not r["supported"]:
                    grounding_dropped += 1
                    continue
                crop_filename = None
                if ko_png and r["bbox_pct"]:
                    try:
                        crop_filename = save_ko_crop(
                            batch_id=batch_id, page_number=ko_q["page_number"],
                            ko_id=ko_q_id, pp_id=r["past_paper_question_id"],
                            png_bytes=ko_png, bbox_pct=r["bbox_pct"],
                        )
                    except Exception:
                        crop_filename = None
                ground[r["past_paper_question_id"]] = {
                    "reasoning": r["reasoning"] or None,
                    "crop_filename": crop_filename,
                }

        # Only ids that survived grounding, in the matcher's original order.
        surviving = [pid for pid in pp_ids if pid in ground]

        kept = 0
        for pp_q_id in surviving:
            if kept >= 3:
                break  # cap: at most 3 exam questions per KO point
            if pp_q_id in used_pp_ids:
                continue  # each past-paper question is used once overall
            pp_q = next((q for q in past_paper_qs if q["id"] == pp_q_id), None)
            if not pp_q:
                continue
            used_pp_ids.add(pp_q_id)
            g = ground.get(pp_q_id, {})
            g_reason = g.get("reasoning")
            g_crop = g.get("crop_filename")

            parts = [pp_q["exam_board"] or "", str(pp_q["exam_year"] or ""), pp_q["paper_number"] or ""]
            source_detail = " ".join(p for p in parts if p).strip() or None

            if kept == 0:
                db.execute(
                    """UPDATE questions
                       SET blend_origin_text = question_text,
                           blend_origin_answer = answer_text,
                           blend_origin_options = options_json,
                           question_text = ?,
                           answer_text = ?,
                           question_source = 'past_paper',
                           question_source_detail = ?,
                           options_json = ?,
                           source_batch_id = ?,
                           answer_from_mark_scheme = ?,
                           ko_grounding_reasoning = ?,
                           ko_crop_filename = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (pp_q["question_text"], pp_q["answer_text"], source_detail,
                     pp_q["options_json"], pp_q["source_batch_id"],
                     pp_q["answer_from_mark_scheme"], g_reason, g_crop, ko_q_id),
                )
                replaced += 1
            else:
                db.execute(
                    """INSERT INTO questions
                       (batch_id, user_id, subject_id, category_id, subcategory_id,
                        page_number, question_text, answer_text, question_type, difficulty,
                        approved, question_source, question_source_detail, options_json,
                        blend_inserted, source_batch_id, answer_from_mark_scheme,
                        ko_grounding_reasoning, ko_crop_filename)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'past_paper', ?, ?, 1, ?, ?, ?, ?)""",
                    (batch_id, user_id, subject_id,
                     ko_q["category_id"], ko_q["subcategory_id"],
                     ko_q["page_number"], pp_q["question_text"], pp_q["answer_text"],
                     pp_q["question_type"], pp_q["difficulty"],
                     ko_q["approved"], source_detail, pp_q["options_json"],
                     pp_q["source_batch_id"], pp_q["answer_from_mark_scheme"],
                     g_reason, g_crop),
                )
                inserted += 1
            kept += 1

    # Record grounding spend as its own api_usage row + batch cost increment.
    if grounding_usage["input_tokens"] or grounding_usage["output_tokens"]:
        db.execute(
            """INSERT INTO api_usage
               (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
               VALUES (?, ?, 'ko_grounding', ?, ?, ?, ?)""",
            (user_id, batch_id, grounding_usage["input_tokens"],
             grounding_usage["output_tokens"], grounding_usage["cost_usd"],
             grounding_usage.get("model")),
        )
        db.execute(
            "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
            (grounding_usage["cost_usd"], batch_id),
        )

    db.commit()
    total_cost = match_usage["cost_usd"] + grounding_usage["cost_usd"]
    logger.info(
        "blend[batch=%s]: replaced %d KO question(s) in place, inserted %d extra "
        "past-paper question(s); grounding dropped %d candidate(s); %d total matches "
        "returned (cost $%.4f incl. grounding $%.4f)",
        batch_id, replaced, inserted, grounding_dropped, len(matches),
        total_cost, grounding_usage["cost_usd"],
    )
    return {"replaced": replaced, "inserted": inserted, "cost_usd": total_cost}
```

> The existing code already names the matcher's usage dict `match_usage` and builds
> `grouped` above this block — keep those. This replaces from `used_pp_ids = set()`
> through the end of the function.

- [ ] **Step 5: Clear the columns in `_restore_blend`**

In `_restore_blend`, add the two columns to the in-place revert UPDATE's SET list (alongside `answer_from_mark_scheme = 0`):

```python
               answer_from_mark_scheme = 0,
               ko_grounding_reasoning = NULL,
               ko_crop_filename = NULL,
```

- [ ] **Step 6: Run the tests**

Run: `venv/bin/python -m pytest tests/test_upload.py -k "grounding or blend or restore" -q`
Expected: PASS (new grounding tests + existing blend/restore tests still green).

- [ ] **Step 7: Commit**

```bash
git add backend/routers/upload.py tests/test_upload.py
git commit -m "feat: ground blended matches — drop unsupported, store reasoning + crop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Surface grounding in quiz provenance

**Files:**
- Modify: `backend/routers/quiz.py` (`_attach_source_meta`, lines ~171-187)
- Test: `tests/test_quiz.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_quiz.py`:

```python
def test_provenance_includes_ko_grounding(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """A grounded blended question exposes reasoning + crop URL in provenance;
    a plain past-paper question without grounding does not."""
    uid, _ = regular_user
    sid = make_subject()
    b = make_batch(uid, sid, filename="pp.pdf")
    _set_batch_type(db_conn, b, "past_paper")   # past_paper source filter needs this
    qid = make_question(b, uid, sid, question_source="past_paper")
    db_conn.execute(
        "UPDATE questions SET ko_grounding_reasoning = ?, ko_crop_filename = ? WHERE id = ?",
        ("Organ is defined here.", "batch_1/page_1_kocrop_1_2.png", qid))
    db_conn.commit()

    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "question_sources": ["past_paper"], "count": 5},
                    headers=user_headers)
    assert r.status_code == 200
    q = next(x for x in r.json()["questions"] if x["id"] == qid)
    assert q["provenance"]["ko_reasoning"] == "Organ is defined here."
    assert q["provenance"]["ko_crop_url"] == "/images/batch_1/page_1_kocrop_1_2.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_quiz.py::test_provenance_includes_ko_grounding -q`
Expected: FAIL — `ko_reasoning` KeyError.

- [ ] **Step 3: Extend `_attach_source_meta`**

In `backend/routers/quiz.py`, inside `_attach_source_meta`, in the `if q.get("question_source") == "past_paper":` branch (right after the `mark_scheme_verified` line), add:

```python
            reasoning = q.get("ko_grounding_reasoning")
            if reasoning:
                prov["ko_reasoning"] = reasoning
            crop = q.get("ko_crop_filename")
            if crop:
                prov["ko_crop_url"] = "/images/" + crop
```

> The test asserts both keys present because the row has both set. Keep them
> conditional so plain past-paper questions don't carry empty keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_quiz.py::test_provenance_includes_ko_grounding -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/quiz.py tests/test_quiz.py
git commit -m "feat: expose KO grounding reasoning + crop in quiz provenance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Admin card — show KO crop + reasoning

**Files:**
- Modify: `frontend/pages/review-category.html` (the Source panel, `x-show="showSource[q.id]"`, after the `source_context` blockquote)

(No automated test — frontend fragment. Manual verification at the end.)

- [ ] **Step 1: Add the crop + reasoning block**

In `frontend/pages/review-category.html`, inside the Source panel (`<div x-show="showSource[q.id]" ...>`), immediately after the `source_context` `<blockquote>` and before the "Page thumbnail" block, add:

```html
                        <!-- KO grounding: where in the KO this exam question is answered -->
                        <div x-show="q.ko_crop_filename || q.ko_grounding_reasoning"
                             class="rounded-lg border border-emerald-200 bg-emerald-50/40 p-2 space-y-2">
                            <p class="text-xs font-medium text-emerald-700">Where in the KO</p>
                            <p x-show="q.ko_grounding_reasoning"
                               class="text-xs text-gray-600"
                               x-text="q.ko_grounding_reasoning"></p>
                            <img x-show="q.ko_crop_filename"
                                 :src="'/images/' + q.ko_crop_filename"
                                 class="max-h-40 rounded border border-emerald-200 cursor-zoom-in"
                                 @click="window.open('/images/' + q.ko_crop_filename, '_blank')"
                                 @error="$el.style.display='none'"
                                 alt="KO region">
                        </div>
```

> `q.ko_crop_filename` / `q.ko_grounding_reasoning` arrive automatically — the admin
> questions endpoint selects `q.*` (`backend/routers/questions.py`).

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/review-category.html
git commit -m "feat: show KO crop + reasoning in admin question Source panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Quiz Source panel — show KO crop + reasoning

**Files:**
- Modify: `frontend/pages/quiz.html` (Source section, lines ~1009-1030)

(No automated test — frontend fragment. Manual verification next.)

- [ ] **Step 1: Widen the Source toggle's visibility condition**

In `frontend/pages/quiz.html`, change the Source section wrapper condition so it also shows when grounding exists:

Find:
```html
            <div x-show="currentQuestion?.source_context || currentQuestion?.batch_id"
                 class="mt-4 pt-3 border-t border-gray-100">
```
Replace with:
```html
            <div x-show="currentQuestion?.source_context || currentQuestion?.batch_id || currentQuestion?.provenance?.ko_crop_url || currentQuestion?.provenance?.ko_reasoning"
                 class="mt-4 pt-3 border-t border-gray-100">
```

- [ ] **Step 2: Add the KO crop + reasoning block**

Inside `<div x-show="showSource" x-transition class="mt-3 space-y-3">`, as the FIRST child (before the existing "Full source page thumbnail" block), add:

```html
                    <!-- KO grounding: the exact region of the KO that answers this question -->
                    <div x-show="currentQuestion?.provenance?.ko_crop_url || currentQuestion?.provenance?.ko_reasoning"
                         class="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3 space-y-2">
                        <p class="text-xs font-medium text-emerald-700">Where in your Knowledge Organiser</p>
                        <p x-show="currentQuestion?.provenance?.ko_reasoning"
                           class="text-sm text-gray-700"
                           x-text="currentQuestion?.provenance?.ko_reasoning"></p>
                        <img x-show="currentQuestion?.provenance?.ko_crop_url"
                             :src="currentQuestion?.provenance?.ko_crop_url"
                             @click="lightboxSrc = currentQuestion?.provenance?.ko_crop_url"
                             class="max-h-52 w-auto rounded-lg border border-emerald-200 shadow-sm cursor-zoom-in"
                             alt="KO region that answers this question">
                    </div>
```

> Uses the existing `lightboxSrc` zoom mechanism already used by the full-page image
> just below it. Shown whenever the student expands "View Source", before or after
> answering (deliberate revision aid).

- [ ] **Step 3: Commit**

```bash
git add frontend/pages/quiz.html
git commit -m "feat: show KO crop + reasoning in quiz Source panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Full-suite verification + manual check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `venv/bin/python -m pytest tests/ --tb=short -q`
Expected: all pass (the 290 prior + the new grounding/migration/provenance tests).

- [ ] **Step 2: Manual smoke test (local)**

Run: `python run.py`, then in the app:
1. Open a Science KO booklet's blend and hit **Regenerate Blend**. Watch logs for
   `ground: KO ... supported` and `blend[...]: ... grounding dropped N candidate(s)`.
2. In **Admin/Review** for that booklet, expand **Source** on a blended question —
   confirm the "Where in the KO" crop + reasoning appear.
3. Start a **Blended** quiz; on a matched question click **View Source** — confirm the
   KO crop + reasoning show (the crop is the close-up region, clickable to zoom).

- [ ] **Step 3: Final confirmation**

Confirm no `data/*.db*` files are staged in any commit (`git status`), and report
the suite result. Do NOT bump APP_VERSION / tag / release — that's a separate
user-requested step.

---

## Self-Review Notes

- **Spec coverage:** quality gate drops unsupported (Task 6 + 4); reasoning + crop stored (Task 1, 2, 6); DB-overridable model/prompt defaulting to sonnet (Task 3, 4, 5); admin card (Task 8); quiz Source any-time (Task 9, with the deliberate reversal of the "hide source" stance handled by showing only the targeted crop); restore clears columns (Task 6); non-fatal grounding never empties a blend (Task 4 tests + Task 6). All spec sections map to a task.
- **Type/name consistency:** `ground_matches_to_ko(ko_point, ko_page_png, candidates) -> (results, usage)`; result keys `past_paper_question_id / supported / reasoning / bbox_pct / snippet`; `save_ko_crop(batch_id, page_number, ko_id, pp_id, png_bytes, bbox_pct, padding_pct)`; `downscale_png(png_bytes, max_px)`; columns `ko_grounding_reasoning`, `ko_crop_filename`; provenance keys `ko_reasoning`, `ko_crop_url`. Used identically across service, upload, quiz, tests, and templates.
- **Cost accounting:** grounding usage is folded into `match_usage` so the existing blend cost log/return includes it.
```
