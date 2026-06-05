import io
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import date
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


def _slugify(name: str) -> str:
    """Filesystem/zip-safe slug from a filename (no extension, no separators)."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", name).strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return slug or "paper"


def build_archive(batch_ids: list[int], user_id: int, db: sqlite3.Connection) -> tuple[bytes, str]:
    """Return (zip_bytes, download_filename) for the user's past papers in batch_ids.

    Foreign / non-past-paper ids are silently skipped. Raises ValueError if none
    of the requested ids are valid.
    """
    today = date.today()
    buf = io.BytesIO()
    papers_meta = []
    used_slugs = set()

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

        if not papers_meta:
            raise ValueError("No exportable past papers in the requested selection")

        manifest = {
            "format": "revisionaid-pastpapers",
            "version": 1,
            "exported_at": today.isoformat(),
            "papers": papers_meta,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    if len(papers_meta) == 1:
        download_name = f"{_slugify(papers_meta[0]['filename'])}.revaid.zip"
    else:
        download_name = f"RevisionAid-PastPapers-{today.isoformat()}.zip"
    return buf.getvalue(), download_name


SUPPORTED_VERSION = 1


def _safe_rel(rel: str) -> str:
    """Reject path-traversal / absolute / dot entries; return the normalised relative path."""
    posix = rel.replace("\\", "/")
    parts = posix.split("/")
    if posix.startswith("/") or ".." in parts or "." in parts:
        raise ValueError(f"Unsafe path in archive: {rel}")
    return posix


def _resolve_subject(name: str, db: sqlite3.Connection) -> int:
    row = db.execute("SELECT id FROM subjects WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = db.execute("INSERT INTO subjects (name) VALUES (?)", (name,))
    return cur.lastrowid


def _resolve_category(name, subject_id: int, db: sqlite3.Connection):
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


def _resolve_subcategory(name, category_id, db: sqlite3.Connection):
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

    for q in data["questions"]:
        img_idx = q.get("image_index")
        image_id = index_to_id.get(img_idx) if img_idx is not None else None
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

    # Write figure files only after all DB inserts have succeeded, so a failed
    # insert leaves nothing on disk. Roll back the files if a write fails.
    dest_dir = _images_root() / f"batch_{new_bid}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel, blob in files.items():
            safe = _safe_rel(rel)
            out = dest_dir / safe
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise

    db.commit()
    return {
        "status": "imported",
        "batch_id": new_bid,
        "filename": b["filename"],
        "question_count": len(data["questions"]),
    }


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
        slug = entry.get("slug")
        if not slug:
            raise ValueError("Manifest paper entry missing 'slug'")
        try:
            data = json.loads(zf.read(f"papers/{slug}/paper.json"))
        except KeyError:
            raise ValueError(f"Archive missing paper.json for '{slug}'")
        except json.JSONDecodeError:
            raise ValueError(f"Invalid paper.json for '{slug}'")
        prefix = f"papers/{slug}/images/"
        files = {}
        for name in zf.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                rel = _safe_rel(name[len(prefix):])
                files[rel] = zf.read(name)
        papers.append({"slug": slug, "data": data, "files": files})

    return {"manifest": manifest, "papers": papers}
