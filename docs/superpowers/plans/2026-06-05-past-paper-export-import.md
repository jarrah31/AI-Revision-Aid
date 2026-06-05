# Past Paper Export / Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user export selected past papers (questions, mcq options, and figure PNGs) to a portable `.zip` and import that zip into any RevisionAid instance, recreating the papers without re-running the AI.

**Architecture:** A self-contained service module (`backend/services/paper_archive.py`) does all serialisation, zip pack/unpack, and ID-remapping. Two thin endpoints in `backend/routers/past_papers.py` (`GET /export`, `POST /import`) handle auth/ownership and HTTP shaping. The Past Papers page gains per-paper checkboxes plus Export/Import buttons. PDFs are excluded; the whole `data/images/batch_N/` folder is bundled so figure display and recrop keep working.

**Tech Stack:** FastAPI, SQLite (sqlite3.Row), Python `zipfile`/`io`/`json`, Alpine.js v3 (no build step), Tailwind via CDN. Tests with pytest against in-memory/isolated SQLite (conftest fixtures).

**Spec:** `docs/superpowers/specs/2026-06-05-past-paper-export-import-design.md`

---

## File Structure

- **Create** `backend/services/paper_archive.py` — all export/import logic:
  - `serialize_paper(batch_id, user_id, db) -> dict` — one paper's portable dict.
  - `build_archive(batch_ids, user_id, db) -> (bytes, filename)` — the zip + download name.
  - `read_archive(zip_bytes) -> dict` — validate + parse manifest and per-paper data.
  - `import_paper(paper, user_id, db) -> dict` — duplicate check, name resolution, insert + remap, file extraction.
- **Modify** `backend/routers/past_papers.py` — add `GET /export` and `POST /import` endpoints.
- **Modify** `frontend/pages/past-papers.html` — checkboxes + Export/Import buttons + result modal.
- **Create** `tests/test_paper_archive.py` — service + endpoint tests.

### Critical testability note (applies to every backend task)
The service MUST resolve the data directory at **call time** via the `database` module so the conftest monkeypatch (`isolated_db` sets `database.DATA_DIR` to a tmp dir) takes effect. Do:

```python
from backend import database

def _images_root() -> Path:
    return Path(database.DATA_DIR) / "images"
```

Do **NOT** write `from backend.database import DATA_DIR` or a module-level `DATA_DIR = Path(...)`. Other routers bind a fixed real-path copy; this service must not, or figure round-trip tests would touch the real `data/` folder.

### Data contract (paper.json)
```json
{
  "batch": {
    "filename": "Biology-AQA-84611F-QP-JUN23.PDF",
    "batch_type": "past_paper",
    "exam_board": "AQA", "exam_year": 2023,
    "paper_number": "Paper 1", "tier": "Foundation",
    "page_start": 1, "page_end": 40, "source_type": "pdf",
    "subject_name": "Biology",
    "category_name": null, "subcategory_name": null
  },
  "images": [
    {"image_index": 0, "page_number": 25, "rel_path": "page_25_img_0.png",
     "description": "", "crop_x": 1.0, "crop_y": 2.0, "crop_w": 3.0, "crop_h": 4.0,
     "width": 100, "height": 80}
  ],
  "questions": [
    {"page_number": 25, "question_text": "...", "answer_text": "...",
     "question_type": "factual", "difficulty": 1, "approved": 1,
     "question_source": "past_paper", "question_source_detail": null,
     "question_ref": "1a", "source_context": null, "options_json": null,
     "image_index": 0,
     "mcq_options": [{"option_text": "...", "is_correct": 1}]}
  ]
}
```
- `images[].rel_path` is the path **inside** `papers/<slug>/images/` (the on-disk `images.filename` has a `batch_N/` prefix that is stripped on export and re-added on import).
- `questions[].image_index` references `images[].image_index`, or `null`.

---

## Task 1: Serialize one paper to a portable dict

**Files:**
- Create: `backend/services/paper_archive.py`
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paper_archive.py
import io
import json
import zipfile
from pathlib import Path

import pytest

from backend import database
from backend.services import paper_archive


def _make_past_paper(db_conn, user_id, subject_id, *, filename="Bio-QP.PDF",
                     exam_board="AQA", exam_year=2023, paper_number="Paper 1",
                     tier="Foundation", category_id=None, subcategory_id=None):
    cur = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end, status,
            batch_type, exam_board, exam_year, paper_number, tier, source_type,
            category_id, subcategory_id)
           VALUES (?, ?, ?, 'x.pdf', 1, 40, 'completed',
                   'past_paper', ?, ?, ?, ?, 'pdf', ?, ?)""",
        (user_id, subject_id, filename, exam_board, exam_year, paper_number, tier,
         category_id, subcategory_id),
    )
    db_conn.commit()
    bid = cur.lastrowid
    db_conn.execute("UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
                    (f"batch_{bid}.pdf", bid))
    db_conn.commit()
    return bid


def _add_image(db_conn, batch_id, page_number=25, rel="page_25_img_0.png",
               write_bytes=b"PNGDATA"):
    cur = db_conn.execute(
        """INSERT INTO images
           (batch_id, page_number, filename, description, crop_x, crop_y,
            crop_w, crop_h, width, height)
           VALUES (?, ?, ?, 'fig', 1.0, 2.0, 3.0, 4.0, 100, 80)""",
        (batch_id, page_number, f"batch_{batch_id}/{rel}"),
    )
    db_conn.commit()
    img_dir = Path(database.DATA_DIR) / "images" / f"batch_{batch_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / rel).write_bytes(write_bytes)
    return cur.lastrowid


def test_serialize_paper_captures_rows_by_name(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid)
    img_id = _add_image(db_conn, bid)
    q = db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source, question_ref, image_id)
           VALUES (?, ?, ?, 25, 'Q?', 'A.', 1, 'past_paper', '1a', ?)""",
        (bid, user_id, sid, img_id),
    )
    db_conn.commit()
    qid = q.lastrowid
    db_conn.execute(
        "INSERT INTO mcq_options (question_id, option_text, is_correct) VALUES (?, 'A.', 1)",
        (qid,),
    )
    db_conn.commit()

    data = paper_archive.serialize_paper(bid, user_id, db_conn)

    assert data["batch"]["subject_name"] == "Biology"
    assert data["batch"]["exam_board"] == "AQA"
    assert "id" not in data["batch"]            # no raw IDs exported
    assert data["images"][0]["rel_path"] == "page_25_img_0.png"
    assert data["images"][0]["image_index"] == 0
    assert data["questions"][0]["question_ref"] == "1a"
    assert data["questions"][0]["image_index"] == 0
    assert data["questions"][0]["mcq_options"] == [{"option_text": "A.", "is_correct": 1}]


def test_serialize_paper_rejects_foreign_or_non_pp(db_conn, regular_user, second_user, make_subject):
    user_id, _ = regular_user
    other_id, _ = second_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid)
    with pytest.raises(ValueError):
        paper_archive.serialize_paper(bid, other_id, db_conn)  # not owner
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.services.paper_archive` / `AttributeError: serialize_paper`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/services/paper_archive.py
import sqlite3
from pathlib import Path

from backend import database

_PORTABLE_BATCH_FIELDS = (
    "filename", "batch_type", "exam_board", "exam_year", "paper_number",
    "tier", "page_start", "page_end", "source_type",
)
_PORTABLE_QUESTION_FIELDS = (
    "page_number", "question_text", "answer_text", "question_type", "difficulty",
    "approved", "question_source", "question_source_detail", "question_ref",
    "source_context", "options_json",
)


def _images_root() -> Path:
    return Path(database.DATA_DIR) / "images"


def serialize_paper(batch_id: int, user_id: int, db: sqlite3.Connection) -> dict:
    """Build a portable dict for one past-paper batch owned by user_id."""
    batch = db.execute(
        """SELECT b.*, s.name AS subject_name,
                  c.name AS category_name, sc.name AS subcategory_name
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           LEFT JOIN categories c ON c.id = b.category_id
           LEFT JOIN subcategories sc ON sc.id = b.subcategory_id
           WHERE b.id = ? AND b.user_id = ? AND b.batch_type = 'past_paper'""",
        (batch_id, user_id),
    ).fetchone()
    if not batch:
        raise ValueError(f"Past paper {batch_id} not found for this user")

    batch_out = {k: batch[k] for k in _PORTABLE_BATCH_FIELDS}
    batch_out["subject_name"] = batch["subject_name"]
    batch_out["category_name"] = batch["category_name"]
    batch_out["subcategory_name"] = batch["subcategory_name"]

    img_rows = db.execute(
        "SELECT * FROM images WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    images = []
    id_to_index = {}
    prefix = f"batch_{batch_id}/"
    for idx, img in enumerate(img_rows):
        id_to_index[img["id"]] = idx
        rel = img["filename"]
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        images.append({
            "image_index": idx,
            "page_number": img["page_number"],
            "rel_path": rel,
            "description": img["description"],
            "crop_x": img["crop_x"], "crop_y": img["crop_y"],
            "crop_w": img["crop_w"], "crop_h": img["crop_h"],
            "width": img["width"], "height": img["height"],
        })

    q_rows = db.execute(
        "SELECT * FROM questions WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    questions = []
    for q in q_rows:
        item = {k: q[k] for k in _PORTABLE_QUESTION_FIELDS}
        item["image_index"] = id_to_index.get(q["image_id"])
        opts = db.execute(
            "SELECT option_text, is_correct FROM mcq_options WHERE question_id = ? ORDER BY id",
            (q["id"],),
        ).fetchall()
        item["mcq_options"] = [
            {"option_text": o["option_text"], "is_correct": o["is_correct"]} for o in opts
        ]
        questions.append(item)

    return {"batch": batch_out, "images": images, "questions": questions}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_archive.py tests/test_paper_archive.py
git commit -m "feat: serialize a past paper to a portable dict"
```

---

## Task 2: Build the zip archive (bytes + filename)

**Files:**
- Modify: `backend/services/paper_archive.py`
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_archive_single_paper_filename_and_contents(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid,
                           filename="Biology-AQA-84611F-QP-JUN23.PDF")
    _add_image(db_conn, bid, rel="page_25_img_0.png", write_bytes=b"CROP")
    # full-page PNG present on disk but NOT in images table -> must still be bundled
    (Path(database.DATA_DIR) / "images" / f"batch_{bid}" / "page_25_full.png").write_bytes(b"FULL")

    blob, filename = paper_archive.build_archive([bid], user_id, db_conn)

    assert filename == "Biology-AQA-84611F-QP-JUN23.revaid.zip"
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "revisionaid-pastpapers"
    assert manifest["version"] == 1
    assert len(manifest["papers"]) == 1
    slug = manifest["papers"][0]["slug"]
    assert f"papers/{slug}/paper.json" in names
    assert f"papers/{slug}/images/page_25_img_0.png" in names
    assert f"papers/{slug}/images/page_25_full.png" in names   # full page bundled
    assert zf.read(f"papers/{slug}/images/page_25_full.png") == b"FULL"


def test_build_archive_multi_paper_combined_name(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    b1 = _make_past_paper(db_conn, user_id, sid, filename="P1.PDF", paper_number="Paper 1")
    b2 = _make_past_paper(db_conn, user_id, sid, filename="P2.PDF", paper_number="Paper 2")
    blob, filename = paper_archive.build_archive([b1, b2], user_id, db_conn)
    assert filename.startswith("RevisionAid-PastPapers-") and filename.endswith(".zip")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert len(json.loads(zf.read("manifest.json"))["papers"]) == 2


def test_build_archive_skips_foreign_paper(db_conn, regular_user, second_user, make_subject):
    user_id, _ = regular_user
    other_id, _ = second_user
    sid = make_subject("Biology")
    mine = _make_past_paper(db_conn, user_id, sid, filename="Mine.PDF")
    theirs = _make_past_paper(db_conn, other_id, sid, filename="Theirs.PDF")
    blob, _ = paper_archive.build_archive([mine, theirs], user_id, db_conn)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert len(json.loads(zf.read("manifest.json"))["papers"]) == 1


def test_build_archive_no_valid_ids_raises(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    with pytest.raises(ValueError):
        paper_archive.build_archive([99999], user_id, db_conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k build_archive -q`
Expected: FAIL — `AttributeError: build_archive`.

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `paper_archive.py` (merge with existing import block):

```python
import io
import json
import re
import zipfile
from datetime import date
```

Append:

```python
def _slugify(name: str) -> str:
    """Filesystem/zip-safe slug from a filename (no extension, no separators)."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", name).strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return slug or "paper"


def build_archive(batch_ids, user_id, db):
    """Return (zip_bytes, download_filename) for the user's past papers in batch_ids.

    Foreign / non-past-paper ids are silently skipped. Raises ValueError if none
    of the requested ids are valid.
    """
    buf = io.BytesIO()
    papers_meta = []
    used_slugs = set()
    valid_filenames = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for bid in batch_ids:
            try:
                data = serialize_paper(bid, user_id, db)
            except ValueError:
                continue  # skip foreign / non-past-paper

            base_slug = _slugify(data["batch"]["filename"])
            slug = base_slug
            n = 2
            while slug in used_slugs:
                slug = f"{base_slug}-{n}"
                n += 1
            used_slugs.add(slug)

            zf.writestr(f"papers/{slug}/paper.json", json.dumps(data, indent=2))

            # Bundle the ENTIRE on-disk image folder (crops + full-page PNGs).
            src_dir = _images_root() / f"batch_{bid}"
            if src_dir.is_dir():
                for f in sorted(src_dir.rglob("*")):
                    if f.is_file():
                        arc = f"papers/{slug}/images/{f.relative_to(src_dir).as_posix()}"
                        zf.writestr(arc, f.read_bytes())

            papers_meta.append({
                "slug": slug,
                "filename": data["batch"]["filename"],
                "exam_board": data["batch"]["exam_board"],
                "exam_year": data["batch"]["exam_year"],
                "paper_number": data["batch"]["paper_number"],
                "tier": data["batch"]["tier"],
                "question_count": len(data["questions"]),
            })
            valid_filenames.append(data["batch"]["filename"])

        if not papers_meta:
            raise ValueError("No exportable past papers in the requested selection")

        manifest = {
            "format": "revisionaid-pastpapers",
            "version": 1,
            "exported_at": date.today().isoformat(),
            "papers": papers_meta,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    if len(valid_filenames) == 1:
        download_name = f"{_slugify(valid_filenames[0])}.revaid.zip"
    else:
        download_name = f"RevisionAid-PastPapers-{date.today().isoformat()}.zip"
    return buf.getvalue(), download_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k build_archive -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_archive.py tests/test_paper_archive.py
git commit -m "feat: build combined past-paper export zip"
```

---

## Task 3: Read + validate an archive

**Files:**
- Modify: `backend/services/paper_archive.py`
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
def _zip_from(manifest, papers):
    """papers: list of (slug, paper_dict, {rel_path: bytes})."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for slug, pdict, files in papers:
            zf.writestr(f"papers/{slug}/paper.json", json.dumps(pdict))
            for rel, data in files.items():
                zf.writestr(f"papers/{slug}/images/{rel}", data)
    return buf.getvalue()


def test_read_archive_parses_papers():
    manifest = {"format": "revisionaid-pastpapers", "version": 1,
                "exported_at": "2026-06-05",
                "papers": [{"slug": "s1", "filename": "P1.PDF"}]}
    pdict = {"batch": {"filename": "P1.PDF"}, "images": [], "questions": []}
    blob = _zip_from(manifest, [("s1", pdict, {"page_1_full.png": b"X"})])
    parsed = paper_archive.read_archive(blob)
    assert len(parsed["papers"]) == 1
    assert parsed["papers"][0]["slug"] == "s1"
    assert parsed["papers"][0]["data"]["batch"]["filename"] == "P1.PDF"
    assert parsed["papers"][0]["files"]["page_1_full.png"] == b"X"


def test_read_archive_rejects_non_zip():
    with pytest.raises(ValueError):
        paper_archive.read_archive(b"not a zip")


def test_read_archive_rejects_bad_format():
    blob = _zip_from({"format": "something-else", "version": 1, "papers": []}, [])
    with pytest.raises(ValueError):
        paper_archive.read_archive(blob)


def test_read_archive_rejects_unsupported_version():
    blob = _zip_from({"format": "revisionaid-pastpapers", "version": 999, "papers": []}, [])
    with pytest.raises(ValueError):
        paper_archive.read_archive(blob)


def test_read_archive_rejects_path_traversal():
    manifest = {"format": "revisionaid-pastpapers", "version": 1, "papers": [{"slug": "s1"}]}
    pdict = {"batch": {"filename": "P1.PDF"}, "images": [], "questions": []}
    # craft an entry that escapes the images folder
    blob = _zip_from(manifest, [("s1", pdict, {"../../evil.png": b"X"})])
    with pytest.raises(ValueError):
        paper_archive.read_archive(blob)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k read_archive -q`
Expected: FAIL — `AttributeError: read_archive`.

- [ ] **Step 3: Write minimal implementation**

Append to `paper_archive.py`:

```python
SUPPORTED_VERSION = 1


def _safe_rel(rel: str) -> str:
    """Reject path-traversal / absolute entries; return the normalised relative path."""
    posix = rel.replace("\\", "/")
    if posix.startswith("/") or ".." in posix.split("/"):
        raise ValueError(f"Unsafe path in archive: {rel}")
    return posix


def read_archive(zip_bytes: bytes) -> dict:
    """Validate and parse an export zip into {manifest, papers:[{slug,data,files}]}."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise ValueError("Not a valid .zip archive")

    try:
        manifest = json.loads(zf.read("manifest.json"))
    except KeyError:
        raise ValueError("Archive is missing manifest.json")
    except json.JSONDecodeError:
        raise ValueError("Archive manifest.json is not valid JSON")

    if manifest.get("format") != "revisionaid-pastpapers":
        raise ValueError("Not a RevisionAid past-paper archive")
    if manifest.get("version") != SUPPORTED_VERSION:
        raise ValueError(f"Unsupported archive version: {manifest.get('version')}")

    papers = []
    for entry in manifest.get("papers", []):
        slug = entry["slug"]
        try:
            data = json.loads(zf.read(f"papers/{slug}/paper.json"))
        except KeyError:
            raise ValueError(f"Archive missing paper.json for '{slug}'")
        prefix = f"papers/{slug}/images/"
        files = {}
        for name in zf.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                rel = _safe_rel(name[len(prefix):])
                files[rel] = zf.read(name)
        papers.append({"slug": slug, "data": data, "files": files})

    return {"manifest": manifest, "papers": papers}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k read_archive -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_archive.py tests/test_paper_archive.py
git commit -m "feat: parse and validate import archive with path-traversal guard"
```

---

## Task 4: Import one paper (duplicate check, name resolution, insert + remap)

**Files:**
- Modify: `backend/services/paper_archive.py`
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_paper_round_trip(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF")
    img_id = _add_image(db_conn, bid, rel="page_25_img_0.png", write_bytes=b"CROP")
    q = db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source, question_ref, image_id)
           VALUES (?, ?, ?, 25, 'Q?', 'A.', 1, 'past_paper', '1a', ?)""",
        (bid, user_id, sid, img_id),
    )
    db_conn.commit()
    db_conn.execute(
        "INSERT INTO mcq_options (question_id, option_text, is_correct) VALUES (?, 'A.', 1)",
        (q.lastrowid,),
    )
    db_conn.commit()

    parsed = paper_archive.read_archive(paper_archive.build_archive([bid], user_id, db_conn)[0])
    paper = parsed["papers"][0]

    # Wipe original so the import is a true recreation.
    db_conn.execute("DELETE FROM upload_batches WHERE id = ?", (bid,))
    db_conn.commit()

    result = paper_archive.import_paper(paper, user_id, db_conn)

    assert result["status"] == "imported"
    new_bid = result["batch_id"]
    new = db_conn.execute("SELECT * FROM upload_batches WHERE id = ?", (new_bid,)).fetchone()
    assert new["batch_type"] == "past_paper"
    assert new["pdf_path"] == "imported"
    assert new["user_id"] == user_id
    nq = db_conn.execute("SELECT * FROM questions WHERE batch_id = ?", (new_bid,)).fetchall()
    assert len(nq) == 1 and nq[0]["question_ref"] == "1a"
    # image FK remapped to a NEW image row in the NEW batch
    nimg = db_conn.execute("SELECT * FROM images WHERE id = ?", (nq[0]["image_id"],)).fetchone()
    assert nimg["batch_id"] == new_bid
    assert nimg["filename"] == f"batch_{new_bid}/page_25_img_0.png"
    # figure file written to the new batch folder
    assert (Path(database.DATA_DIR) / "images" / f"batch_{new_bid}" / "page_25_img_0.png").read_bytes() == b"CROP"
    # mcq remapped
    nopt = db_conn.execute(
        "SELECT option_text FROM mcq_options WHERE question_id = ?", (nq[0]["id"],)
    ).fetchall()
    assert [o["option_text"] for o in nopt] == ["A."]


def test_import_paper_creates_missing_subject(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF")
    parsed = paper_archive.read_archive(paper_archive.build_archive([bid], user_id, db_conn)[0])
    db_conn.execute("DELETE FROM upload_batches WHERE id = ?", (bid,))
    db_conn.execute("DELETE FROM subjects WHERE id = ?", (sid,))
    db_conn.commit()

    result = paper_archive.import_paper(parsed["papers"][0], user_id, db_conn)
    assert result["status"] == "imported"
    sub = db_conn.execute("SELECT id FROM subjects WHERE name = 'Biology'").fetchone()
    assert sub is not None  # subject recreated by name


def test_import_paper_skips_duplicate(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF",
                           exam_board="AQA", exam_year=2023,
                           paper_number="Paper 1", tier="Foundation")
    parsed = paper_archive.read_archive(paper_archive.build_archive([bid], user_id, db_conn)[0])
    # original still present -> same board/year/number/tier => duplicate
    result = paper_archive.import_paper(parsed["papers"][0], user_id, db_conn)
    assert result["status"] == "skipped"
    assert result["reason"] == "duplicate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k import_paper -q`
Expected: FAIL — `AttributeError: import_paper`.

- [ ] **Step 3: Write minimal implementation**

Append to `paper_archive.py`:

```python
def _resolve_subject(name: str, db) -> int:
    row = db.execute("SELECT id FROM subjects WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = db.execute("INSERT INTO subjects (name) VALUES (?)", (name,))
    return cur.lastrowid


def _resolve_category(name, subject_id, db):
    if not name:
        return None
    row = db.execute(
        "SELECT id FROM categories WHERE subject_id = ? AND name = ?", (subject_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = db.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    )
    return cur.lastrowid


def _resolve_subcategory(name, category_id, db):
    if not name or category_id is None:
        return None
    row = db.execute(
        "SELECT id FROM subcategories WHERE category_id = ? AND name = ?", (category_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = db.execute(
        "INSERT INTO subcategories (category_id, name) VALUES (?, ?)", (category_id, name)
    )
    return cur.lastrowid


def import_paper(paper: dict, user_id: int, db) -> dict:
    """Import one parsed paper ({slug,data,files}) for user_id. Returns a result dict."""
    data = paper["data"]
    files = paper.get("files", {})
    b = data["batch"]

    # Duplicate check: same board/year/number/tier already owned by this user.
    dup = db.execute(
        """SELECT id FROM upload_batches
           WHERE user_id = ? AND batch_type = 'past_paper'
             AND IFNULL(exam_board,'') = IFNULL(?, '')
             AND IFNULL(exam_year,0)  = IFNULL(?, 0)
             AND IFNULL(paper_number,'') = IFNULL(?, '')
             AND IFNULL(tier,'') = IFNULL(?, '')""",
        (user_id, b["exam_board"], b["exam_year"], b["paper_number"], b["tier"]),
    ).fetchone()
    if dup:
        return {"status": "skipped", "reason": "duplicate", "filename": b["filename"]}

    subject_id = _resolve_subject(b["subject_name"], db)
    category_id = _resolve_category(b.get("category_name"), subject_id, db)
    subcategory_id = _resolve_subcategory(b.get("subcategory_name"), category_id, db)

    cur = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, subcategory_id, filename, pdf_path,
            page_start, page_end, status, batch_type, exam_board, exam_year,
            paper_number, tier, source_type, completed_at)
           VALUES (?, ?, ?, ?, ?, 'imported', ?, ?, 'completed', 'past_paper',
                   ?, ?, ?, ?, ?, datetime('now'))""",
        (user_id, subject_id, category_id, subcategory_id, b["filename"],
         b["page_start"], b["page_end"], b["exam_board"], b["exam_year"],
         b["paper_number"], b["tier"], b.get("source_type", "pdf")),
    )
    new_bid = cur.lastrowid

    # Write figure files into the new batch folder (whole bundled images set).
    dest_dir = _images_root() / f"batch_{new_bid}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for rel, blob in files.items():
        safe = _safe_rel(rel)
        out = dest_dir / safe
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)

    # Insert image rows, mapping export image_index -> new image id.
    index_to_id = {}
    for img in data["images"]:
        icur = db.execute(
            """INSERT INTO images
               (batch_id, page_number, filename, description, crop_x, crop_y,
                crop_w, crop_h, width, height)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_bid, img["page_number"], f"batch_{new_bid}/{img['rel_path']}",
             img["description"], img["crop_x"], img["crop_y"],
             img["crop_w"], img["crop_h"], img["width"], img["height"]),
        )
        index_to_id[img["image_index"]] = icur.lastrowid

    # Insert questions (remap image_index -> image id) and their mcq options.
    for q in data["questions"]:
        image_id = index_to_id.get(q.get("image_index")) if q.get("image_index") is not None else None
        qcur = db.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text, answer_text,
                question_type, difficulty, approved, question_source,
                question_source_detail, question_ref, source_context, options_json,
                image_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_bid, user_id, subject_id, q["page_number"], q["question_text"],
             q["answer_text"], q.get("question_type", "factual"),
             q.get("difficulty", 1), q.get("approved", 1),
             q.get("question_source", "past_paper"), q.get("question_source_detail"),
             q.get("question_ref"), q.get("source_context"), q.get("options_json"),
             image_id),
        )
        qid = qcur.lastrowid
        for opt in q.get("mcq_options", []):
            db.execute(
                "INSERT OR IGNORE INTO mcq_options (question_id, option_text, is_correct) VALUES (?, ?, ?)",
                (qid, opt["option_text"], opt["is_correct"]),
            )

    db.commit()
    return {
        "status": "imported",
        "batch_id": new_bid,
        "filename": b["filename"],
        "question_count": len(data["questions"]),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k import_paper -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_archive.py tests/test_paper_archive.py
git commit -m "feat: import a past paper with ID remap and duplicate skip"
```

---

## Task 5: Export endpoint

**Files:**
- Modify: `backend/routers/past_papers.py` (add endpoint; merge imports)
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
def test_export_endpoint_streams_zip(client, db_conn, regular_user, user_headers, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF")
    resp = client.get(f"/api/past-papers/export?ids={bid}", headers=user_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "Bio-QP.revaid.zip" in resp.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "manifest.json" in zf.namelist()


def test_export_endpoint_400_when_no_valid_ids(client, user_headers):
    resp = client.get("/api/past-papers/export?ids=99999", headers=user_headers)
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k export_endpoint -q`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `backend/routers/past_papers.py`, add to imports:

```python
from fastapi import Response
from backend.services import paper_archive
```

Add this endpoint (place it after `list_past_papers`):

```python
@router.get("/export")
def export_past_papers(
    ids: str = Query(..., description="Comma-separated past-paper batch ids"),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Export the user's selected past papers as a downloadable .zip."""
    try:
        batch_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers")
    if not batch_ids:
        raise HTTPException(status_code=400, detail="No paper ids provided")
    try:
        blob, filename = paper_archive.build_archive(batch_ids, user["id"], db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

Note: `/export` is declared before the `/{batch_id}` routes already in the file, so the literal path wins; if a `GET /{batch_id}` is ever added, keep `/export` above it.

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k export_endpoint -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/past_papers.py tests/test_paper_archive.py
git commit -m "feat: add past-paper export endpoint"
```

---

## Task 6: Import endpoint

**Files:**
- Modify: `backend/routers/past_papers.py`
- Test: `tests/test_paper_archive.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_endpoint_round_trip(client, db_conn, regular_user, user_headers, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF")
    _add_image(db_conn, bid, rel="page_25_img_0.png", write_bytes=b"CROP")
    blob = paper_archive.build_archive([bid], user_id, db_conn)[0]
    db_conn.execute("DELETE FROM upload_batches WHERE id = ?", (bid,))
    db_conn.commit()

    resp = client.post(
        "/api/past-papers/import",
        headers=user_headers,
        files={"file": ("backup.zip", blob, "application/zip")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["imported"]) == 1
    assert body["skipped"] == []
    new_bid = body["imported"][0]["batch_id"]
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM upload_batches WHERE id = ?", (new_bid,)
    ).fetchone()["c"] == 1


def test_import_endpoint_reports_duplicate(client, db_conn, regular_user, user_headers, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid, filename="Bio-QP.PDF")
    blob = paper_archive.build_archive([bid], user_id, db_conn)[0]
    # original still present -> duplicate
    resp = client.post(
        "/api/past-papers/import",
        headers=user_headers,
        files={"file": ("backup.zip", blob, "application/zip")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == []
    assert body["skipped"][0]["reason"] == "duplicate"


def test_import_endpoint_rejects_garbage(client, user_headers):
    resp = client.post(
        "/api/past-papers/import",
        headers=user_headers,
        files={"file": ("x.zip", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k import_endpoint -q`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `backend/routers/past_papers.py` add to the `fastapi` import line: `File`, `UploadFile`:

```python
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
```

Add the endpoint:

```python
@router.post("/import")
async def import_past_papers(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Import past papers from a previously exported .zip."""
    blob = await file.read()
    try:
        parsed = paper_archive.read_archive(blob)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    imported, skipped, errors = [], [], []
    for paper in parsed["papers"]:
        try:
            result = paper_archive.import_paper(paper, user["id"], db)
        except Exception as e:  # pragma: no cover - defensive
            db.rollback()
            errors.append({"slug": paper.get("slug"), "error": str(e)})
            continue
        if result["status"] == "imported":
            imported.append(result)
        else:
            skipped.append(result)

    return {"imported": imported, "skipped": skipped, "errors": errors}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -k import_endpoint -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full archive test file + commit**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/test_paper_archive.py -q`
Expected: PASS (all tests in the file).

```bash
git add backend/routers/past_papers.py tests/test_paper_archive.py
git commit -m "feat: add past-paper import endpoint"
```

---

## Task 7: Frontend — checkboxes + Export/Import buttons + result modal

**Files:**
- Modify: `frontend/pages/past-papers.html`

This task has no automated test (no build step / Alpine UI). Verify in the preview after wiring.

- [ ] **Step 1: Read the current page to find the Alpine component, the paper list loop, and the api helper usage**

Run: `grep -n "x-data\|x-for\|API\.\|papers\|fetchPapers\|loadPapers\|subject" frontend/pages/past-papers.html`
Identify: the component's `x-data` object, the method that loads papers, the variable holding the paper array (assume `papers`), and the per-paper loop element (assume `<template x-for="p in papers">` / `<tr>` or `<div>`).

- [ ] **Step 2: Add selection + import state to the x-data object**

In the `x-data` object literal, add these properties (alongside existing state):

```js
selected: [],
importing: false,
importResult: null,   // { imported:[], skipped:[], errors:[] }
importError: '',
```

And these methods (add inside the same object; adapt `this.papers`/`this.subjectId` to the page's real names found in Step 1):

```js
toggleSelect(id) {
    const i = this.selected.indexOf(id);
    if (i === -1) this.selected.push(id); else this.selected.splice(i, 1);
},
allSelected() {
    return this.papers.length > 0 && this.selected.length === this.papers.length;
},
toggleSelectAll() {
    this.selected = this.allSelected() ? [] : this.papers.map(p => p.id);
},
exportSelected() {
    if (!this.selected.length) return;
    const url = '/api/past-papers/export?ids=' + this.selected.join(',');
    const token = localStorage.getItem('token');
    fetch(url, { headers: { Authorization: 'Bearer ' + token } })
        .then(r => { if (!r.ok) throw new Error('Export failed'); return r.blob().then(b => ({ b, r })); })
        .then(({ b, r }) => {
            const cd = r.headers.get('content-disposition') || '';
            const m = cd.match(/filename="?([^"]+)"?/);
            const name = m ? m[1] : 'past-papers.zip';
            const a = document.createElement('a');
            a.href = URL.createObjectURL(b);
            a.download = name;
            a.click();
            URL.revokeObjectURL(a.href);
        })
        .catch(e => { this.importError = e.message; });
},
async importFile(ev) {
    const f = ev.target.files[0];
    if (!f) return;
    this.importing = true; this.importResult = null; this.importError = '';
    try {
        const fd = new FormData();
        fd.append('file', f);
        const token = localStorage.getItem('token');
        const r = await fetch('/api/past-papers/import', {
            method: 'POST',
            headers: { Authorization: 'Bearer ' + token },
            body: fd,
        });
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail || 'Import failed');
        this.importResult = body;
        await this.loadPapers();   // refresh list (use the page's real loader name)
    } catch (e) {
        this.importError = e.message;
    } finally {
        this.importing = false;
        ev.target.value = '';   // allow re-importing the same file
    }
},
```

NOTE: confirm the token key — check an existing fetch/API call in the page or `frontend/js/api.js` for how the JWT is stored (`localStorage.getItem('token')` is the assumed key; match whatever the codebase already uses).

- [ ] **Step 3: Add the toolbar (Export/Import buttons) above the paper list**

Match the page's existing Tailwind button styling. Insert above the list:

```html
<div class="flex items-center gap-2 mb-3">
  <button @click="exportSelected()" :disabled="selected.length === 0"
          class="px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white disabled:opacity-40 disabled:cursor-not-allowed">
    Export selected (<span x-text="selected.length"></span>)
  </button>
  <label class="px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 cursor-pointer hover:bg-gray-200">
    Import…
    <input type="file" accept=".zip" class="hidden" @change="importFile($event)">
  </label>
  <span x-show="importing" class="text-sm text-gray-500">Importing…</span>
</div>
```

- [ ] **Step 4: Add a checkbox column to the paper loop**

Inside the per-paper row element (found in Step 1), add as the first cell/element:

```html
<input type="checkbox" :checked="selected.includes(p.id)" @change="toggleSelect(p.id)"
       class="w-4 h-4 rounded border-gray-300">
```

And add a select-all checkbox in the list header (if the list has one):

```html
<input type="checkbox" :checked="allSelected()" @change="toggleSelectAll()"
       class="w-4 h-4 rounded border-gray-300">
```

- [ ] **Step 5: Add the result modal**

Place near the end of the component's root element. Reuse the page's existing modal theme if one exists; otherwise:

```html
<div x-show="importResult || importError" style="display:none"
     class="fixed inset-0 z-50 flex items-center justify-center p-4">
  <div class="absolute inset-0 bg-gray-900/50" @click="importResult = null; importError = ''"></div>
  <div class="relative bg-white rounded-2xl shadow-xl max-w-md w-full p-6">
    <h3 class="text-lg font-semibold mb-3">Import results</h3>
    <template x-if="importError">
      <p class="text-red-600 text-sm" x-text="importError"></p>
    </template>
    <template x-if="importResult">
      <div class="space-y-2 text-sm">
        <p class="text-green-700">
          Imported <span x-text="importResult.imported.length"></span> paper(s).
        </p>
        <template x-if="importResult.skipped.length">
          <div>
            <p class="text-amber-700 font-medium">Skipped:</p>
            <ul class="list-disc list-inside text-gray-600">
              <template x-for="s in importResult.skipped" :key="s.filename">
                <li><span x-text="s.filename"></span> — <span x-text="s.reason"></span></li>
              </template>
            </ul>
          </div>
        </template>
        <template x-if="importResult.errors && importResult.errors.length">
          <div>
            <p class="text-red-700 font-medium">Errors:</p>
            <ul class="list-disc list-inside text-gray-600">
              <template x-for="e in importResult.errors" :key="e.slug">
                <li><span x-text="e.slug"></span> — <span x-text="e.error"></span></li>
              </template>
            </ul>
          </div>
        </template>
      </div>
    </template>
    <div class="mt-4 text-right">
      <button @click="importResult = null; importError = ''"
              class="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium">Close</button>
    </div>
  </div>
</div>
```

Alpine reminder: do not put literal double-quotes inside any `x-data="..."` attribute value (it breaks out of the attribute and dumps JS as page text). Keep JS comments inside x-data free of `"`.

- [ ] **Step 6: Verify in the preview**

Run the dev server if not running: `python run.py` (FastAPI on :8000; preview proxy on :8001).
- Load the Past Papers page in the preview, select a paper, click **Export selected**, confirm a `.zip` downloads named after the exam file.
- Click **Import…**, choose that zip, confirm the result modal shows "Skipped: … duplicate" (since the original still exists). Delete the paper, re-import, confirm it shows "Imported 1 paper(s)" and the list refreshes with the paper back.
- Check the browser console for errors and confirm no raw JS text is visible on the page.

- [ ] **Step 7: Commit**

```bash
git add frontend/pages/past-papers.html
git commit -m "feat: export/import UI on the Past Papers page"
```

---

## Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `ANTHROPIC_API_KEY="" JWT_SECRET="test" venv/bin/pytest tests/ --tb=short -q`
Expected: all pass except the 3 known pre-existing `test_costs` 404 failures (unless that separate fix has since landed). The new `tests/test_paper_archive.py` tests all pass.

- [ ] **Step 2: If anything unexpected fails, stop and debug**

Use the superpowers:systematic-debugging skill — do not patch around failures. Root-cause first.

- [ ] **Step 3: Final review against the spec**

Re-read `docs/superpowers/specs/2026-06-05-past-paper-export-import-design.md` and confirm every section maps to delivered code: export endpoint, import endpoint, combined zip + per-paper subfolders, PDFs excluded, full image folder bundled, subject/category by name, ID remap, duplicate skip, path-traversal guard, excluded per-user tables, UI on Past Papers page.

---

## Self-Review (completed by plan author)

- **Spec coverage:** archive format → Task 2/3; export endpoint → Task 5; import endpoint + remap + duplicate skip + name resolution → Task 4/6; exclude PDFs / bundle whole image folder → Task 2 (rglob) + Task 4 (write files); path-traversal guard → Task 3 (`_safe_rel`); excluded per-user tables → only `questions`/`mcq_options`/`images` are serialized (Task 1); UI → Task 7; testing matrix → Tasks 1–6 + Task 8. All covered.
- **Placeholder scan:** no TBD/TODO; every code step shows full code. Frontend Step 2 flags page-specific names (`papers`, `loadPapers`, token key) to confirm during implementation — these are real lookups, not placeholders.
- **Type/name consistency:** `serialize_paper`, `build_archive`, `read_archive`, `import_paper`, `_safe_rel`, `_images_root`, `_slugify` used consistently across tasks; `image_index` ↔ `index_to_id` remap consistent between Task 1 (export) and Task 4 (import); `rel_path` stripping (export) matches re-prefixing `batch_{new_bid}/` (import).
