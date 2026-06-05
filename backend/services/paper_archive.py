import io
import json
import re
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
