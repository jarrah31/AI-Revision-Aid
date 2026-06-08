import re
import sqlite3
import json
import logging
import traceback
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional, List

from backend.auth import get_current_user
from backend.database import get_db, DB_PATH
from backend.services.pdf_processor import (
    render_page_to_png,
    save_full_page_image,
    crop_image_region,
    png_to_base64,
    get_pdf_page_count,
    load_image_as_png_bytes,
    save_ko_crop,
)
from backend.services.claude_service import (
    extract_qa_from_page_with_fallback,
    extract_qa_from_past_paper,
    detect_paper_type,
    match_ko_to_past_papers,
    extract_sections_from_handwritten,
    extract_qa_from_text,
    ground_matches_to_ko,
)
from backend.services.multi_response_service import detect_and_store_multi_response

DATA_DIR = Path(__file__).parent.parent.parent / "data"

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_ko_page_png(batch_id: int, page_number: int) -> bytes | None:
    """Return the saved full-page PNG bytes for a KO page, or None if absent
    (e.g. a legacy batch processed before page images were stored)."""
    path = DATA_DIR / "images" / f"batch_{batch_id}" / f"page_{page_number}_full.png"
    try:
        return path.read_bytes()
    except OSError:
        return None

# Page types that contain exam questions
_QUESTION_PAGE_TYPES = {"questions", "both"}
# Page types that contain mark scheme answers
_MS_PAGE_TYPES = {"mark_scheme", "both"}


class OcrSectionIn(BaseModel):
    image_num: int
    section_order: int
    title: str | None = None
    content: str = ""


def _normalise_ref(ref: str) -> str:
    """Normalise a question_ref to a consistent form for matching.
    e.g. '1 (a)' -> '1a', '2b.' -> '2b', 'Question 3' -> '3',
         '02.1' -> '21', '2.1' -> '21', '04.6' -> '46'

    Leading zeros are stripped per segment: models are inconsistent about
    zero-padding *between* the question-paper and mark-scheme extraction calls
    (observed on AQA JUN22: QP '02.1' vs MS '2.1'). Without this, the two refs
    normalise to '021' vs '21' and the mark-scheme answer never attaches to the
    question. Splitting on separators first preserves segment boundaries so a
    sub-part's own leading zero ('04.06') is handled independently.
    """
    if not ref:
        return ref
    import re
    r = ref.strip().lower()
    r = re.sub(r"^question\s*", "", r)            # strip leading "question"
    segments = re.split(r"[\s.()\[\]]+", r)       # split on spaces/dots/brackets
    out = []
    for seg in segments:
        if not seg:
            continue
        seg = re.sub(r"^0+(\d)", r"\1", seg)      # strip leading zeros from a digit run
        out.append(seg)
    return "".join(out)


def _process_ms_pages(
    ms_pdf_path: str,
    subject_name: str,
    user_id: int,
    batch_id: int,
    db: sqlite3.Connection,
) -> dict[str, str]:
    """Process a standalone mark scheme PDF.
    Returns dict of {normalised_question_ref: answer_text}.
    Logs API usage against the batch.
    """
    ms_answers: dict[str, str] = {}
    total_pages = get_pdf_page_count(ms_pdf_path)

    for page_num in range(total_pages):
        display_page = page_num + 1
        try:
            png_bytes = render_page_to_png(ms_pdf_path, page_num)
            image_b64 = png_to_base64(png_bytes)
            result, usage = extract_qa_from_past_paper(image_b64, subject_name)

            # Record cost
            db.execute(
                """INSERT INTO api_usage
                   (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
                   VALUES (?, ?, 'ms_extraction', ?, ?, ?, ?)""",
                (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"], usage.get("model")),
            )
            db.execute(
                "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
                (usage["cost_usd"], batch_id),
            )
            db.commit()

            for ans in result.get("answers", []):
                ref = _normalise_ref(ans.get("question_ref", ""))
                answer = ans.get("answer", "").strip()
                if ref and answer:
                    ms_answers[ref] = answer

        except Exception as e:
            print(f"[MS] Error on page {display_page}: {e}")
            continue

    return ms_answers


def _apply_ms_answers(batch_id: int, ms_answers: dict[str, str], db: sqlite3.Connection) -> int:
    """Update questions in a batch with mark scheme answers.
    Returns the number of questions updated.
    """
    if not ms_answers:
        return 0

    updated = 0
    questions = db.execute(
        "SELECT id, question_ref FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchall()

    for q in questions:
        ref = _normalise_ref(q["question_ref"] or "")
        if ref and ref in ms_answers:
            db.execute(
                "UPDATE questions SET answer_text = ?, answer_from_mark_scheme = 1, "
                "updated_at = datetime('now') WHERE id = ?",
                (ms_answers[ref], q["id"]),
            )
            updated += 1

    if updated:
        db.commit()
        print(f"[MS correlation] updated {updated} questions with mark scheme answers")

    return updated


def _match_and_replace_with_past_papers(
    batch_id: int, user_id: int, subject_id: int, db: sqlite3.Connection
) -> dict:
    """After KO processing, replace AI questions with past paper equivalents where found.

    Returns a summary dict: {"replaced", "inserted", "cost_usd"}.
    """
    ko_questions = db.execute(
        "SELECT id, question_text, answer_text, category_id, subcategory_id, "
        "       approved, page_number "
        "FROM questions "
        "WHERE batch_id = ? AND question_source = 'ai_generated'",
        (batch_id,),
    ).fetchall()
    if not ko_questions:
        logger.info(
            "blend[batch=%s]: no ai_generated KO questions to match — nothing to blend. "
            "(If this batch was already blended, its KO rows may have become "
            "question_source='past_paper'; a legacy irreversible blend can't be "
            "restored, leaving no ai_generated rows to re-match.)",
            batch_id,
        )
        return {"replaced": 0, "inserted": 0, "cost_usd": 0.0}

    # No LIMIT: the matcher needs the full past-paper corpus. (Large corpora raise AI token cost — an accepted trade-off.)
    past_paper_qs = db.execute(
        """SELECT q.id, q.question_text, q.answer_text, q.options_json,
                  q.question_type, q.difficulty, q.answer_from_mark_scheme,
                  q.image_id, q.batch_id AS source_batch_id,
                  b.exam_board, b.exam_year, b.paper_number
           FROM questions q
           JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.subject_id = ? AND q.user_id = ? AND q.question_source = 'past_paper'
             AND b.batch_type = 'past_paper'
           ORDER BY q.id DESC""",
        (subject_id, user_id),
    ).fetchall()
    if not past_paper_qs:
        logger.info(
            "blend[batch=%s]: no past-paper corpus for subject_id=%s user_id=%s "
            "(need question_source='past_paper' rows in a batch_type='past_paper' "
            "upload) — nothing to match against. %d KO questions went unmatched.",
            batch_id, subject_id, user_id, len(ko_questions),
        )
        return {"replaced": 0, "inserted": 0, "cost_usd": 0.0}  # No past papers uploaded yet — graceful no-op

    logger.info(
        "blend[batch=%s]: matching %d KO questions against %d past-paper questions "
        "(subject_id=%s user_id=%s)",
        batch_id, len(ko_questions), len(past_paper_qs), subject_id, user_id,
    )
    try:
        matches, match_usage = match_ko_to_past_papers(
            [dict(q) for q in ko_questions],
            [dict(q) for q in past_paper_qs],
        )
    except Exception as e:
        logger.warning("blend[batch=%s]: matching call failed: %s", batch_id, e, exc_info=True)
        return {"replaced": 0, "inserted": 0, "cost_usd": 0.0}

    # Record the matching cost against the batch (the call happened regardless
    # of whether any matches were found).
    db.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
           VALUES (?, ?, 'ko_matching', ?, ?, ?, ?)""",
        (user_id, batch_id, match_usage["input_tokens"], match_usage["output_tokens"],
         match_usage["cost_usd"], match_usage.get("model")),
    )
    db.execute(
        "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
        (match_usage["cost_usd"], batch_id),
    )
    db.commit()

    if not matches:
        logger.info(
            "blend[batch=%s]: matcher returned 0 matches across %d KO questions and "
            "%d past-paper questions (cost $%.4f). Likely no lexical/semantic overlap, "
            "or every candidate was rejected at verification.",
            batch_id, len(ko_questions), len(past_paper_qs), match_usage["cost_usd"],
        )
        return {"replaced": 0, "inserted": 0, "cost_usd": match_usage["cost_usd"]}

    # Group matches by KO question, preserving the order the matcher returned them.
    grouped: dict[int, list[int]] = {}
    for m in matches:
        ko_q_id = m.get("ko_question_id")
        pp_q_id = m.get("past_paper_question_id")
        if not ko_q_id or not pp_q_id:
            continue
        grouped.setdefault(ko_q_id, []).append(pp_q_id)

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
                candidates.append(dict(pp))

        ground: dict[int, dict] = {}  # pp_id -> {"reasoning", "crop_filename"}
        if candidates:
            ko_png = _load_ko_page_png(batch_id, ko_q["page_number"])
            results, g_usage = ground_matches_to_ko(dict(ko_q), ko_png, candidates)
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
        surviving = [pid for pid in dict.fromkeys(pp_ids) if pid in ground]

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
                           blend_origin_image_id = image_id,
                           image_id = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (pp_q["question_text"], pp_q["answer_text"], source_detail,
                     pp_q["options_json"], pp_q["source_batch_id"],
                     pp_q["answer_from_mark_scheme"], g_reason, g_crop,
                     pp_q["image_id"], ko_q_id),
                )
                replaced += 1
            else:
                db.execute(
                    """INSERT INTO questions
                       (batch_id, user_id, subject_id, category_id, subcategory_id,
                        page_number, question_text, answer_text, question_type, difficulty,
                        approved, question_source, question_source_detail, options_json,
                        blend_inserted, source_batch_id, answer_from_mark_scheme,
                        ko_grounding_reasoning, ko_crop_filename, image_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'past_paper', ?, ?, 1, ?, ?, ?, ?, ?)""",
                    (batch_id, user_id, subject_id,
                     ko_q["category_id"], ko_q["subcategory_id"],
                     ko_q["page_number"], pp_q["question_text"], pp_q["answer_text"],
                     pp_q["question_type"], pp_q["difficulty"],
                     ko_q["approved"], source_detail, pp_q["options_json"],
                     pp_q["source_batch_id"], pp_q["answer_from_mark_scheme"],
                     g_reason, g_crop, pp_q["image_id"]),
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


def _restore_blend(batch_id: int, db: sqlite3.Connection) -> dict:
    """Tear down a KO batch's blend, returning it to its pre-blend state.

    - Deletes rows inserted as extra exam matches (blend_inserted=1). The FK
      ON DELETE CASCADE cleans up their srs_cards / quiz_answers automatically.
    - Reverts in-place replacements back to the original AI question stashed in
      blend_origin_*, keeping the same question_id so SRS history survives.

    Rows blended under the pre-reversible scheme (blend_origin_text IS NULL and
    blend_inserted=0) cannot be restored — they are left untouched. Returns a
    summary dict: {"deleted", "restored"}.
    """
    deleted = db.execute(
        "DELETE FROM questions WHERE batch_id = ? AND blend_inserted = 1", (batch_id,)
    ).rowcount
    restored = db.execute(
        """UPDATE questions
           SET question_text = blend_origin_text,
               answer_text = blend_origin_answer,
               options_json = blend_origin_options,
               question_source = 'ai_generated',
               question_source_detail = NULL,
               source_batch_id = NULL,
               answer_from_mark_scheme = 0,
               ko_grounding_reasoning = NULL,
               ko_crop_filename = NULL,
               image_id = blend_origin_image_id,
               blend_origin_image_id = NULL,
               blend_origin_text = NULL,
               blend_origin_answer = NULL,
               blend_origin_options = NULL,
               updated_at = datetime('now')
           WHERE batch_id = ? AND blend_origin_text IS NOT NULL""",
        (batch_id,),
    ).rowcount
    db.commit()
    return {"deleted": deleted, "restored": restored}


def regenerate_blend(
    batch_id: int, user_id: int, subject_id: int, db: sqlite3.Connection
) -> dict:
    """Re-run a KO batch's blend from scratch against the *current* past-paper
    corpus: restore the batch to its pre-blend state, then re-match. Returns the
    matcher summary plus the restore counts."""
    logger.info("reblend[batch=%s]: starting regenerate (user_id=%s subject_id=%s)",
                batch_id, user_id, subject_id)
    restore = _restore_blend(batch_id, db)
    logger.info("reblend[batch=%s]: restored %d in-place row(s), deleted %d inserted row(s) "
                "before re-matching", batch_id, restore["restored"], restore["deleted"])
    summary = _match_and_replace_with_past_papers(batch_id, user_id, subject_id, db)
    return {**summary, "restored": restore["restored"], "deleted": restore["deleted"]}


def process_batch(
    batch_id: int,
    pdf_path: str,
    subject_name: str,
    subject_id: int,
    user_id: int,
    page_start: int,
    page_end: int,
    batch_type: str = "knowledge_organiser",
    ms_pdf_path: str | None = None,
    blend_past_papers: bool = True,
    category_id: int | None = None,
    source_type: str = "pdf",
    subcategory_id: int | None = None,
):
    """Background task: process PDF pages (or uploaded images) through Claude and store results."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    try:
        db.execute(
            "UPDATE upload_batches SET status = 'processing' WHERE id = ?",
            (batch_id,),
        )
        db.commit()

        # For image uploads, discover the saved files and override page range
        saved_images: list[Path] = []
        if source_type == "images":
            saved_images = sorted(
                (DATA_DIR / "pdfs").glob(f"batch_{batch_id}_img_*"),
                key=lambda p: int(re.search(r"_img_(\d+)", p.name).group(1)),
            )
            page_start = 1
            page_end = len(saved_images)

        # Collect mark scheme answers found inline (combined Q+MS pages or mark scheme sections)
        ms_answers_inline: dict[str, str] = {}

        for page_num in range(page_start - 1, page_end):  # 0-indexed
            display_page = page_num + 1
            try:
                # Get PNG bytes — from uploaded image file or rendered PDF page
                if source_type == "images":
                    png_bytes = load_image_as_png_bytes(saved_images[page_num])
                else:
                    png_bytes = render_page_to_png(pdf_path, page_num)
                save_full_page_image(batch_id, display_page, png_bytes)

                # Send to Claude — different extraction for past papers vs KO
                image_b64 = png_to_base64(png_bytes)

                if batch_type == "past_paper":
                    result, usage = extract_qa_from_past_paper(image_b64, subject_name)  # noqa: past papers don't need section splitting
                    page_type = result.get("page_type", "cover")

                    # Collect any mark scheme answers from this page (combined or MS section)
                    if page_type in _MS_PAGE_TYPES:
                        for ans in result.get("answers", []):
                            ref = _normalise_ref(ans.get("question_ref", ""))
                            answer = ans.get("answer", "").strip()
                            if ref and answer:
                                ms_answers_inline[ref] = answer

                    # Extract exam metadata from cover page
                    if page_type == "cover":
                        eb  = result.get("exam_board")
                        ey  = result.get("exam_year")
                        pn  = result.get("paper_number")
                        tr  = result.get("tier")
                        if any(v is not None for v in (eb, ey, pn, tr)):
                            db.execute(
                                """UPDATE upload_batches
                                   SET exam_board = COALESCE(?, exam_board),
                                       exam_year  = COALESCE(?, exam_year),
                                       paper_number = COALESCE(?, paper_number),
                                       tier       = COALESCE(?, tier)
                                   WHERE id = ?""",
                                (eb, ey, pn, tr, batch_id),
                            )

                    # Skip pages with no exam questions
                    if page_type not in _QUESTION_PAGE_TYPES:
                        db.execute(
                            "UPDATE upload_batches SET processed_pages = ?, cost_usd = cost_usd + ? WHERE id = ?",
                            (page_num - (page_start - 1) + 1, usage["cost_usd"], batch_id),
                        )
                        db.execute(
                            """INSERT INTO api_usage
                               (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
                               VALUES (?, ?, 'qa_extraction', ?, ?, ?, ?)""",
                            (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"], usage.get("model")),
                        )
                        db.commit()
                        continue

                    question_source = "past_paper"
                else:
                    result, usage = extract_qa_from_page_with_fallback(png_bytes, subject_name)
                    question_source = "ai_generated"

                # Record API usage for this page
                db.execute(
                    """INSERT INTO api_usage
                       (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
                       VALUES (?, ?, 'qa_extraction', ?, ?, ?, ?)""",
                    (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"], usage.get("model")),
                )
                db.execute(
                    "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
                    (usage["cost_usd"], batch_id),
                )

                # Process image regions — crop figures/diagrams for both KO and past-paper pages
                image_id_map = {}  # index -> db image id
                for i, img_data in enumerate(result.get("images", [])):
                    # `.get(k, default)` only fires for MISSING keys; the model
                    # can emit an explicit JSON null (e.g. "bbox_x_pct": null),
                    # which would feed None into crop_image_region's arithmetic
                    # and raise "NoneType - float", dropping the whole page.
                    # Coerce null/None to a sensible numeric default.
                    filename, width, height = crop_image_region(
                        batch_id,
                        display_page,
                        i,
                        png_bytes,
                        img_data.get("bbox_x_pct") if img_data.get("bbox_x_pct") is not None else 0,
                        img_data.get("bbox_y_pct") if img_data.get("bbox_y_pct") is not None else 0,
                        img_data.get("bbox_w_pct") if img_data.get("bbox_w_pct") is not None else 100,
                        img_data.get("bbox_h_pct") if img_data.get("bbox_h_pct") is not None else 100,
                    )
                    cursor = db.execute(
                        """INSERT INTO images (batch_id, page_number, filename, description,
                           crop_x, crop_y, crop_w, crop_h, width, height)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id,
                            display_page,
                            filename,
                            img_data.get("description", ""),
                            img_data.get("bbox_x_pct"),
                            img_data.get("bbox_y_pct"),
                            img_data.get("bbox_w_pct"),
                            img_data.get("bbox_h_pct"),
                            width,
                            height,
                        ),
                    )
                    image_id_map[i] = cursor.lastrowid

                # Store questions
                for q in result.get("questions", []):
                    related_idx = q.get("related_image_index")
                    # The model occasionally returns a LIST here (a question that
                    # references multiple figures) instead of a single 0-based
                    # index. dict.get(list) raises "unhashable type: list" and the
                    # per-page handler would discard every question on the page.
                    # Take the first referenced figure.
                    if isinstance(related_idx, list):
                        related_idx = related_idx[0] if related_idx else None
                    image_id = image_id_map.get(related_idx) if related_idx is not None else None
                    q_ref = _normalise_ref(q.get("question_ref", "")) or None

                    db.execute(
                        """INSERT INTO questions
                           (batch_id, user_id, subject_id, category_id, subcategory_id,
                            page_number, question_text, answer_text, question_type, difficulty,
                            image_id, source_context, question_source, question_ref)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id,
                            user_id,
                            subject_id,
                            category_id,
                            subcategory_id,
                            display_page,
                            # `.get(k, default)` only fires for MISSING keys; the model can
                            # emit an explicit JSON null (e.g. a question-paper question with
                            # no answer yet — answers arrive later from the mark scheme), so
                            # coerce None to "" to satisfy the NOT NULL columns.
                            q.get("question") or "",
                            q.get("answer") or "",
                            q.get("type") or "factual",
                            q.get("difficulty") or 1,
                            image_id,
                            q.get("source_quote") or None,
                            question_source,
                            q_ref,
                        ),
                    )

                # Update progress
                db.execute(
                    "UPDATE upload_batches SET processed_pages = ? WHERE id = ?",
                    (page_num - (page_start - 1) + 1, batch_id),
                )
                db.commit()

            except Exception as e:
                print(f"Error processing page {display_page}: {e}")
                traceback.print_exc()
                db.execute(
                    """UPDATE upload_batches
                       SET error_message = COALESCE(error_message || '; ', '') || ?
                       WHERE id = ?""",
                    (f"Page {display_page}: {str(e)}", batch_id),
                )
                db.commit()
                continue

        # ── Post-processing ────────────────────────────────────────────────────

        if batch_type == "past_paper":
            # 1) Apply mark scheme answers found inline (combined Q+MS pages)
            if ms_answers_inline:
                _apply_ms_answers(batch_id, ms_answers_inline, db)

            # 2) Process separate mark scheme PDF if provided
            if ms_pdf_path and Path(ms_pdf_path).exists():
                try:
                    ms_answers_separate = _process_ms_pages(
                        ms_pdf_path, subject_name, user_id, batch_id, db
                    )
                    _apply_ms_answers(batch_id, ms_answers_separate, db)
                except Exception as e:
                    print(f"Mark scheme processing failed (non-fatal): {e}")
                    traceback.print_exc()

            # 3) Structure multiple-response ("tick N boxes") questions now that
            #    mark-scheme answers (which mark correctness) have been applied.
            try:
                n = detect_and_store_multi_response(batch_id, subject_name, user_id, db)
                if n:
                    print(f"[multi_response] structured {n} question(s) for batch {batch_id}")
            except Exception as e:
                print(f"[multi_response] step failed (non-fatal): {e}")

        elif batch_type == "knowledge_organiser" and blend_past_papers:
            # Replace AI questions with past paper equivalents where found
            try:
                _match_and_replace_with_past_papers(batch_id, user_id, subject_id, db)
            except Exception as e:
                print(f"Matching step failed (non-fatal): {e}")
                traceback.print_exc()

        # Mark complete
        db.execute(
            """UPDATE upload_batches
               SET status = 'completed', completed_at = datetime('now')
               WHERE id = ?""",
            (batch_id,),
        )
        db.commit()

    except Exception as e:
        print(f"Batch processing failed: {e}")
        traceback.print_exc()
        db.execute(
            "UPDATE upload_batches SET status = 'failed', error_message = ? WHERE id = ?",
            (str(e), batch_id),
        )
        db.commit()
    finally:
        db.close()


def process_batch_ocr(
    batch_id: int,
    subject_name: str,
    subject_id: int,
    user_id: int,
    category_id: int | None = None,
    subcategory_id: int | None = None,
):
    """Background task: OCR handwritten images, store sections, set status=awaiting_ocr_review."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    try:
        db.execute(
            "UPDATE upload_batches SET status = 'ocr_processing' WHERE id = ?",
            (batch_id,),
        )
        db.commit()

        saved_images: list[Path] = sorted(
            (DATA_DIR / "pdfs").glob(f"batch_{batch_id}_img_*"),
            key=lambda p: int(re.search(r"_img_(\d+)", p.name).group(1)),
        )
        total = len(saved_images)
        db.execute(
            "UPDATE upload_batches SET total_pages = ? WHERE id = ?",
            (total, batch_id),
        )
        db.commit()

        for image_num, img_path in enumerate(saved_images, start=1):
            try:
                png_bytes = load_image_as_png_bytes(img_path)
                save_full_page_image(batch_id, image_num, png_bytes)
                image_b64 = png_to_base64(png_bytes)

                sections, usage = extract_sections_from_handwritten(image_b64)

                db.execute(
                    """INSERT INTO api_usage
                       (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
                       VALUES (?, ?, 'handwritten_ocr', ?, ?, ?, ?)""",
                    (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"], usage.get("model")),
                )
                db.execute(
                    "UPDATE upload_batches SET cost_usd = cost_usd + ?, processed_pages = ? WHERE id = ?",
                    (usage["cost_usd"], image_num, batch_id),
                )

                for sec in sections:
                    db.execute(
                        """INSERT INTO ocr_sections
                           (batch_id, image_num, section_order, title, content)
                           VALUES (?, ?, ?, ?, ?)""",
                        (batch_id, image_num, sec["section_order"], sec.get("title"), sec.get("content", "")),
                    )
                db.commit()

            except Exception as e:
                print(f"[OCR] Error on image {image_num}: {e}")
                traceback.print_exc()
                db.execute(
                    """UPDATE upload_batches
                       SET error_message = COALESCE(error_message || '; ', '') || ?
                       WHERE id = ?""",
                    (f"Image {image_num}: {str(e)}", batch_id),
                )
                # Insert blank placeholder so student sees a textarea for this image
                db.execute(
                    """INSERT INTO ocr_sections (batch_id, image_num, section_order, title, content)
                       VALUES (?, ?, 1, NULL, '')""",
                    (batch_id, image_num),
                )
                db.commit()
                continue

        db.execute(
            "UPDATE upload_batches SET status = 'awaiting_ocr_review' WHERE id = ?",
            (batch_id,),
        )
        db.commit()

    except Exception as e:
        print(f"OCR batch processing failed: {e}")
        traceback.print_exc()
        db.execute(
            "UPDATE upload_batches SET status = 'failed', error_message = ? WHERE id = ?",
            (str(e), batch_id),
        )
        db.commit()
    finally:
        db.close()


def process_batch_from_text(
    batch_id: int,
    subject_name: str,
    subject_id: int,
    user_id: int,
    category_id: int | None = None,
    subcategory_id: int | None = None,
):
    """Background task: generate Q&A from confirmed OCR text sections (no image re-processing)."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    try:
        db.execute(
            "UPDATE upload_batches SET status = 'processing', processed_pages = 0 WHERE id = ?",
            (batch_id,),
        )
        db.commit()

        sections = db.execute(
            """SELECT image_num, section_order, title, content
               FROM ocr_sections
               WHERE batch_id = ?
               ORDER BY image_num, section_order""",
            (batch_id,),
        ).fetchall()

        if not sections:
            raise ValueError("No OCR sections found for this batch")

        # Group sections by image_num
        images: dict[int, list] = {}
        for sec in sections:
            images.setdefault(sec["image_num"], []).append(sec)

        total_images = len(images)
        db.execute(
            "UPDATE upload_batches SET total_pages = ? WHERE id = ?",
            (total_images, batch_id),
        )
        db.commit()

        for image_num, img_sections in sorted(images.items()):
            try:
                text_parts = []
                for sec in img_sections:
                    heading = sec["title"] or f"Section {sec['section_order']}"
                    text_parts.append(f"## {heading}\n\n{sec['content']}")
                text_content = "\n\n".join(text_parts)

                result, usage = extract_qa_from_text(text_content, subject_name)

                db.execute(
                    """INSERT INTO api_usage
                       (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
                       VALUES (?, ?, 'handwritten_qa', ?, ?, ?, ?)""",
                    (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"], usage.get("model")),
                )
                db.execute(
                    "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
                    (usage["cost_usd"], batch_id),
                )

                for q in result.get("questions", []):
                    db.execute(
                        """INSERT INTO questions
                           (batch_id, user_id, subject_id, category_id, subcategory_id,
                            page_number, question_text, answer_text, question_type, difficulty,
                            source_context, question_source)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai_generated')""",
                        (
                            batch_id, user_id, subject_id, category_id, subcategory_id,
                            image_num,
                            q.get("question", ""),
                            q.get("answer", ""),
                            q.get("type", "factual"),
                            q.get("difficulty", 1),
                            q.get("source_quote") or None,
                        ),
                    )

                db.execute(
                    "UPDATE upload_batches SET processed_pages = ? WHERE id = ?",
                    (image_num, batch_id),
                )
                db.commit()

            except Exception as e:
                print(f"[QA-from-text] Error on image {image_num}: {e}")
                traceback.print_exc()
                db.execute(
                    """UPDATE upload_batches
                       SET error_message = COALESCE(error_message || '; ', '') || ?
                       WHERE id = ?""",
                    (f"Image {image_num}: {str(e)}", batch_id),
                )
                db.commit()
                continue

        db.execute(
            """UPDATE upload_batches
               SET status = 'completed', completed_at = datetime('now')
               WHERE id = ?""",
            (batch_id,),
        )
        db.commit()

    except Exception as e:
        print(f"Text Q&A batch processing failed: {e}")
        traceback.print_exc()
        db.execute(
            "UPDATE upload_batches SET status = 'failed', error_message = ? WHERE id = ?",
            (str(e), batch_id),
        )
        db.commit()
    finally:
        db.close()


_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@router.post("")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File(default=[]),
    subject_id: int = Form(...),
    page_start: int = Form(1),
    page_end: int | None = Form(None),
    is_shared: int = Form(0),
    is_handwritten: int = Form(0),
    batch_type: str = Form("knowledge_organiser"),
    blend_past_papers: int = Form(1),
    category_id: int | None = Form(None),
    subcategory_id: int | None = Form(None),
    mark_scheme_file: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    # Determine source type: images take precedence over PDF
    valid_images = [img for img in images if img and img.filename]
    source_type = "images" if valid_images else "pdf"

    if source_type == "pdf":
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="A PDF file is required")
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    else:
        for img in valid_images:
            ext = Path(img.filename).suffix.lower()
            if ext not in _ALLOWED_IMAGE_EXTS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported image format: {img.filename}. Accepted formats: JPG, PNG, GIF, WEBP.",
                )

    # Validate batch_type
    if batch_type not in ("knowledge_organiser", "past_paper"):
        batch_type = "knowledge_organiser"

    # Validate mark scheme file (PDF uploads only)
    if source_type == "pdf" and mark_scheme_file and mark_scheme_file.filename:
        if not mark_scheme_file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Mark scheme must be a PDF file")
    else:
        mark_scheme_file = None

    # Verify subject exists
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    pdf_dir = DATA_DIR / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # For image uploads derive page range from image count
    if source_type == "images":
        page_start = 1
        page_end = len(valid_images)
        filename = valid_images[0].filename if len(valid_images) == 1 else f"{len(valid_images)} images"
    else:
        filename = file.filename

    # Only apply handwritten OCR mode for image uploads
    if source_type != "images":
        is_handwritten = 0

    # Create batch record first to get ID (page_end resolved after PDF is saved for PDFs;
    # exam_board/year/paper_number/tier filled in during processing from the cover page)
    _page_end_placeholder = page_end if page_end is not None else 1
    cursor = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, subcategory_id, filename, pdf_path,
            page_start, page_end, total_pages, is_shared, status, batch_type,
            source_type, is_handwritten)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (
            user["id"], subject_id, category_id, subcategory_id, filename, "",
            page_start, _page_end_placeholder, _page_end_placeholder - page_start + 1, is_shared,
            batch_type, source_type, is_handwritten,
        ),
    )
    db.commit()
    batch_id = cursor.lastrowid

    ms_pdf_path_str: str | None = None

    if source_type == "pdf":
        # Save question paper PDF
        qp_pdf_path = pdf_dir / f"batch_{batch_id}.pdf"
        content = await file.read()
        qp_pdf_path.write_bytes(content)

        # Save mark scheme PDF (if provided)
        if mark_scheme_file and batch_type == "past_paper":
            ms_content = await mark_scheme_file.read()
            if ms_content:
                ms_pdf_path = pdf_dir / f"batch_{batch_id}_ms.pdf"
                ms_pdf_path.write_bytes(ms_content)
                ms_pdf_path_str = str(ms_pdf_path)

        # Update PDF path in record
        db.execute(
            "UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
            (f"batch_{batch_id}.pdf", batch_id),
        )
        db.commit()

        # Validate page range
        total_pages = get_pdf_page_count(str(qp_pdf_path))
        if page_end is None:
            page_end = total_pages
            db.execute(
                "UPDATE upload_batches SET page_end = ?, total_pages = ? WHERE id = ?",
                (page_end, total_pages, batch_id),
            )
            db.commit()
        if page_start < 1 or page_end > total_pages or page_start > page_end:
            db.execute("DELETE FROM upload_batches WHERE id = ?", (batch_id,))
            db.commit()
            qp_pdf_path.unlink(missing_ok=True)
            if ms_pdf_path_str:
                Path(ms_pdf_path_str).unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid page range. PDF has {total_pages} pages.",
            )

        pdf_path_for_task = str(qp_pdf_path)
    else:
        # Save each uploaded image as batch_{id}_img_{n}.{ext}
        for i, img in enumerate(valid_images, start=1):
            ext = Path(img.filename).suffix.lower()
            img_save_path = pdf_dir / f"batch_{batch_id}_img_{i}{ext}"
            content = await img.read()
            img_save_path.write_bytes(content)

        db.execute(
            "UPDATE upload_batches SET pdf_path = 'images' WHERE id = ?",
            (batch_id,),
        )
        db.commit()

        pdf_path_for_task = ""

    # Kick off background processing
    if is_handwritten and source_type == "images":
        background_tasks.add_task(
            process_batch_ocr,
            batch_id,
            subject["name"],
            subject_id,
            user["id"],
            category_id,
            subcategory_id,
        )
    else:
        background_tasks.add_task(
            process_batch,
            batch_id,
            pdf_path_for_task,
            subject["name"],
            subject_id,
            user["id"],
            page_start,
            page_end,
            batch_type,
            ms_pdf_path_str,
            bool(blend_past_papers),
            category_id,
            source_type,
            subcategory_id,
        )

    return {
        "batch_id": batch_id,
        "total_pages": page_end - page_start + 1,
        "has_mark_scheme": ms_pdf_path_str is not None,
        "source_type": source_type,
        "is_handwritten": bool(is_handwritten),
    }


@router.get("/{batch_id}/status")
def get_batch_status(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batch = db.execute(
        "SELECT * FROM upload_batches WHERE id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    question_count = db.execute(
        "SELECT COUNT(*) as c FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchone()["c"]

    return {
        "id": batch["id"],
        "status": batch["status"],
        "total_pages": batch["total_pages"],
        "processed_pages": batch["processed_pages"],
        "question_count": question_count,
        "error_message": batch["error_message"],
        "filename": batch["filename"],
        "batch_type": batch["batch_type"],
        "is_handwritten": bool(batch["is_handwritten"]),
    }


@router.get("/{batch_id}/ocr")
def get_ocr_sections(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return OCR sections for the handwritten review page."""
    batch = db.execute(
        "SELECT * FROM upload_batches WHERE id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    sections = db.execute(
        """SELECT id, image_num, section_order, title, content
           FROM ocr_sections
           WHERE batch_id = ?
           ORDER BY image_num, section_order""",
        (batch_id,),
    ).fetchall()

    # Group sections by image_num and build image_url
    images: dict[int, dict] = {}
    for sec in sections:
        n = sec["image_num"]
        if n not in images:
            images[n] = {
                "image_num": n,
                "image_url": f"/images/batch_{batch_id}/page_{n}_full.png",
                "sections": [],
            }
        images[n]["sections"].append({
            "id": sec["id"],
            "section_order": sec["section_order"],
            "title": sec["title"],
            "content": sec["content"],
        })

    return {
        "batch_id": batch_id,
        "filename": batch["filename"],
        "images": list(images.values()),
    }


class OcrConfirmRequest(BaseModel):
    sections: list[OcrSectionIn]


@router.post("/{batch_id}/ocr/confirm")
def confirm_ocr_sections(
    batch_id: int,
    req: OcrConfirmRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Save confirmed/edited OCR sections and kick off Q&A generation."""
    batch = db.execute(
        "SELECT * FROM upload_batches WHERE id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["status"] != "awaiting_ocr_review":
        raise HTTPException(status_code=400, detail="Batch is not awaiting OCR review")

    subject = db.execute(
        "SELECT * FROM subjects WHERE id = ?", (batch["subject_id"],)
    ).fetchone()

    # Replace sections with confirmed edits
    db.execute("DELETE FROM ocr_sections WHERE batch_id = ?", (batch_id,))
    for sec in req.sections:
        db.execute(
            """INSERT INTO ocr_sections (batch_id, image_num, section_order, title, content)
               VALUES (?, ?, ?, ?, ?)""",
            (batch_id, sec.image_num, sec.section_order, sec.title, sec.content),
        )
    db.commit()

    background_tasks.add_task(
        process_batch_from_text,
        batch_id,
        subject["name"],
        batch["subject_id"],
        user["id"],
        batch["category_id"],
        batch["subcategory_id"] if "subcategory_id" in batch.keys() else None,
    )

    return {"batch_id": batch_id}


@router.get("/pending-ocr")
def get_pending_ocr_reviews(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return batches that are awaiting OCR review for the current user."""
    batches = db.execute(
        """SELECT b.id, b.filename, b.created_at, s.name as subject_name,
                  COUNT(DISTINCT sec.image_num) as image_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           LEFT JOIN ocr_sections sec ON sec.batch_id = b.id
           WHERE b.user_id = ? AND b.status = 'awaiting_ocr_review'
           GROUP BY b.id
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]


@router.put("/{batch_id}/ocr")
def save_ocr_draft(
    batch_id: int,
    req: OcrConfirmRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Save edited OCR sections without triggering Q&A generation (draft save)."""
    batch = db.execute(
        "SELECT id, status FROM upload_batches WHERE id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["status"] != "awaiting_ocr_review":
        raise HTTPException(status_code=400, detail="Batch is not awaiting OCR review")

    db.execute("DELETE FROM ocr_sections WHERE batch_id = ?", (batch_id,))
    for sec in req.sections:
        db.execute(
            """INSERT INTO ocr_sections (batch_id, image_num, section_order, title, content)
               VALUES (?, ?, ?, ?, ?)""",
            (batch_id, sec.image_num, sec.section_order, sec.title, sec.content),
        )
    db.commit()
    return {"batch_id": batch_id}


# ── Multi-paper detection endpoints ───────────────────────────────────────────

class ConfirmPair(BaseModel):
    qp_id: int
    ms_id: int | None = None
    # Optional user-corrected metadata (overrides detected values)
    exam_board: str | None = None
    exam_year: int | None = None
    paper_number: str | None = None
    tier: str | None = None

class ConfirmDetectionRequest(BaseModel):
    session_id: str
    subject_id: int
    category_id: int | None = None
    subcategory_id: int | None = None
    pairs: list[ConfirmPair]


def _normalize_paper_number(pn: str | None) -> str:
    """Normalise a detected paper number for QP<->MS matching.

    Cover pages encode the paper inconsistently. A question paper whose front
    sheet only shows the code (e.g. '8461/1F') is read as 'Paper 1F' — the
    tier letter folded into the number — while the matching mark scheme, which
    prints 'Paper 1' and 'Foundation Tier' separately, is read as 'Paper 1'.
    The tier is already a separate match-key field, so strip a trailing F/H
    tier letter here so the two forms compare equal. Foundation vs Higher
    stays distinguished by the tier field.
    """
    if not pn:
        return ""
    s = pn.lower().strip()
    s = re.sub(r"(\d)\s*[fh]\b", r"\1", s)   # 'paper 1f' -> 'paper 1'
    s = re.sub(r"\s+", " ", s).strip()
    return s


_MONTH_TOKENS = (
    "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|"
    "SUMMER|WINTER|AUTUMN|SPRING"
)


def _year_from_filename(filename: str | None) -> int | None:
    """Best-effort exam year extracted from a filename.

    Question-paper cover sheets often omit the year, so the detector returns
    exam_year=None even though the filename (e.g. 'Biology-AQA-84611F-QP-JUN22.PDF')
    states it. Prefer an explicit 4-digit year, else a month/season code followed
    by a 2-digit year ('JUN22' -> 2022). Returns None when nothing plausible is found.
    """
    if not filename:
        return None
    name = filename.upper()
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", name)   # explicit 4-digit year
    if m:
        return int(m.group(1))
    m = re.search(rf"(?:{_MONTH_TOKENS})[-_ ]?(\d{{2}})(?!\d)", name)  # 'JUN22'
    if m:
        return 2000 + int(m.group(1))
    return None


def _compute_matches(files: list[dict]) -> list[dict]:
    """Group detected files into QP+MS pairs by shared metadata."""
    from collections import defaultdict
    detected = [f for f in files if f["status"] == "detected"]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    unmatched = []

    for f in detected:
        # Fall back to the filename when the cover page didn't state the year
        # (common on candidate question-paper front sheets).
        eff_year = f["exam_year"] or _year_from_filename(f.get("filename"))
        key = (
            (f["exam_board"] or "").lower(),
            eff_year,
            _normalize_paper_number(f["paper_number"]),
            (f["tier"] or "").lower(),
        )
        # Only group when at least board+year are known
        if f["exam_board"] and eff_year:
            groups[key].append(f)
        else:
            unmatched.append(f)

    matches = []
    group_num = 1
    for key, group_files in groups.items():
        qps = [f for f in group_files if f["paper_type"] in ("question_paper", "combined")]
        mss = [f for f in group_files if f["paper_type"] == "mark_scheme"]
        other = [f for f in group_files if f["paper_type"] not in ("question_paper", "combined", "mark_scheme")]

        for qp in qps:
            ms = mss.pop(0) if mss else None
            matches.append({
                "match_group": group_num,
                "qp_id": qp["id"],
                "ms_id": ms["id"] if ms else None,
                "exam_board": qp["exam_board"],
                "exam_year": qp["exam_year"] or key[1],   # key[1] = effective (filename-derived) year
                "paper_number": qp["paper_number"],
                "tier": qp["tier"],
            })
            group_num += 1

        # Remaining unmatched MSs and unknowns
        for f in mss + other:
            unmatched.append(f)

    for f in unmatched:
        matches.append({
            "match_group": None,
            "qp_id": f["id"] if f["paper_type"] != "mark_scheme" else None,
            "ms_id": f["id"] if f["paper_type"] == "mark_scheme" else None,
            "exam_board": f["exam_board"],
            "exam_year": f["exam_year"],
            "paper_number": f["paper_number"],
            "tier": f["tier"],
        })

    return matches


def _detect_papers_task(session_id: str, file_ids: list[int]):
    """Background task: detect paper type for each file's cover page."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    pdf_dir = DATA_DIR / "pdfs"

    try:
        for file_id in file_ids:
            row = db.execute(
                "SELECT * FROM paper_detection_files WHERE id = ?", (file_id,)
            ).fetchone()
            if not row:
                continue

            saved_path = pdf_dir / row["saved_path"]
            try:
                png_bytes = render_page_to_png(str(saved_path), 0)  # page 0 = first page
                image_b64 = png_to_base64(png_bytes)
                result, usage = detect_paper_type(image_b64)

                db.execute(
                    """UPDATE paper_detection_files
                       SET status = 'detected',
                           paper_type = ?, exam_board = ?, exam_year = ?,
                           paper_number = ?, tier = ?, subject_detected = ?
                       WHERE id = ?""",
                    (
                        result.get("paper_type"),
                        result.get("exam_board"),
                        result.get("exam_year") or _year_from_filename(row["filename"]),
                        result.get("paper_number"),
                        result.get("tier"),
                        result.get("subject"),
                        file_id,
                    ),
                )
            except Exception as e:
                db.execute(
                    "UPDATE paper_detection_files SET status = 'failed', error_message = ? WHERE id = ?",
                    (str(e), file_id),
                )
            db.commit()

        db.execute(
            "UPDATE paper_detection_sessions SET status = 'completed' WHERE id = ?",
            (session_id,),
        )
        db.commit()
    except Exception:
        db.execute(
            "UPDATE paper_detection_sessions SET status = 'failed' WHERE id = ?",
            (session_id,),
        )
        db.commit()
    finally:
        db.close()


@router.post("/detect-papers")
async def detect_papers(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    subject_id: int = Form(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Phase 1: accept multiple PDFs, save them, kick off cover-page detection."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    session_id = str(uuid.uuid4())
    pdf_dir = DATA_DIR / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    db.execute(
        "INSERT INTO paper_detection_sessions (id, user_id, subject_id) VALUES (?, ?, ?)",
        (session_id, user["id"], subject_id),
    )
    db.commit()

    file_ids: list[int] = []
    for i, upload in enumerate(files):
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            continue
        saved_name = f"detect_{session_id}_{i}.pdf"
        saved_path = pdf_dir / saved_name
        content = await upload.read()
        saved_path.write_bytes(content)

        cursor = db.execute(
            """INSERT INTO paper_detection_files (session_id, filename, saved_path)
               VALUES (?, ?, ?)""",
            (session_id, upload.filename, saved_name),
        )
        db.commit()
        file_ids.append(cursor.lastrowid)

    if not file_ids:
        raise HTTPException(status_code=400, detail="No valid PDF files found")

    background_tasks.add_task(_detect_papers_task, session_id, file_ids)
    return {"session_id": session_id}


@router.get("/detect-status/{session_id}")
def get_detect_status(
    session_id: str,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Poll detection progress. Returns per-file results and auto-matched pairs."""
    session = db.execute(
        "SELECT * FROM paper_detection_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Detection session not found")

    files = db.execute(
        "SELECT * FROM paper_detection_files WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    file_list = [dict(f) for f in files]
    all_done = session["status"] == "completed"
    matches = _compute_matches(file_list) if all_done else []

    return {
        "status": session["status"],
        "files": file_list,
        "matches": matches,
    }


@router.post("/confirm-detection")
def confirm_detection(
    req: ConfirmDetectionRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Phase 2: for each confirmed QP+MS pair, create a batch and start processing."""
    session = db.execute(
        "SELECT * FROM paper_detection_sessions WHERE id = ? AND user_id = ?",
        (req.session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Detection session not found")

    subject = db.execute(
        "SELECT name FROM subjects WHERE id = ?",
        (req.subject_id,),
    ).fetchone()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    pdf_dir = DATA_DIR / "pdfs"
    batch_ids: list[int] = []

    for pair in req.pairs:
        qp_file = db.execute(
            "SELECT * FROM paper_detection_files WHERE id = ? AND session_id = ?",
            (pair.qp_id, req.session_id),
        ).fetchone()
        if not qp_file:
            continue

        # Get total pages of QP
        src_path = pdf_dir / qp_file["saved_path"]
        try:
            total_pages = get_pdf_page_count(str(src_path))
        except Exception:
            total_pages = 0

        # Honour user-corrected metadata from the confirmation step, falling back
        # to the auto-detected values when the user didn't override a field. This
        # is what lets the user manually fix the year / paper / tier (e.g. when
        # detection failed and they paired the QP and MS by hand).
        exam_board = pair.exam_board if pair.exam_board is not None else qp_file["exam_board"]
        exam_year = (
            pair.exam_year
            if pair.exam_year is not None
            else (qp_file["exam_year"] or _year_from_filename(qp_file["filename"]))
        )
        paper_number = pair.paper_number if pair.paper_number is not None else qp_file["paper_number"]
        tier = pair.tier if pair.tier is not None else qp_file["tier"]

        # Create batch record
        cursor = db.execute(
            """INSERT INTO upload_batches
               (user_id, subject_id, category_id, subcategory_id, filename, pdf_path,
                page_start, page_end, total_pages, is_shared, status, batch_type,
                source_type, exam_board, exam_year, paper_number, tier)
               VALUES (?, ?, ?, ?, ?, '', 1, ?, ?, 0, 'pending', 'past_paper', 'pdf',
                       ?, ?, ?, ?)""",
            (
                user["id"], req.subject_id, req.category_id, req.subcategory_id,
                qp_file["filename"],
                total_pages, total_pages,
                exam_board, exam_year, paper_number, tier,
            ),
        )
        db.commit()
        batch_id = cursor.lastrowid

        # Rename detect file → batch file
        dest_path = pdf_dir / f"batch_{batch_id}.pdf"
        src_path.rename(dest_path)
        db.execute(
            "UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
            (f"batch_{batch_id}.pdf", batch_id),
        )
        db.commit()

        # Handle MS file if paired
        ms_pdf_path: str | None = None
        if pair.ms_id:
            ms_file = db.execute(
                "SELECT * FROM paper_detection_files WHERE id = ? AND session_id = ?",
                (pair.ms_id, req.session_id),
            ).fetchone()
            if ms_file:
                ms_src = pdf_dir / ms_file["saved_path"]
                ms_dest = pdf_dir / f"batch_{batch_id}_ms.pdf"
                if ms_src.exists():
                    ms_src.rename(ms_dest)
                    ms_pdf_path = str(ms_dest)

        background_tasks.add_task(
            process_batch,
            batch_id=batch_id,
            pdf_path=str(dest_path),
            subject_name=subject["name"],
            subject_id=req.subject_id,
            user_id=user["id"],
            page_start=1,
            page_end=total_pages,
            batch_type="past_paper",
            ms_pdf_path=ms_pdf_path,
            category_id=req.category_id,
            subcategory_id=req.subcategory_id,
        )
        batch_ids.append(batch_id)

    return {"batch_ids": batch_ids}


@router.get("/multi-status")
def get_multi_status(
    ids: List[int] = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return status for multiple batch IDs at once."""
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    batches = db.execute(
        f"""SELECT b.id, b.status, b.filename, b.total_pages, b.processed_pages,
                   b.error_message, b.exam_board, b.exam_year, b.paper_number, b.tier,
                   (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count
            FROM upload_batches b
            WHERE b.id IN ({placeholders}) AND b.user_id = ?""",
        (*ids, user["id"]),
    ).fetchall()
    return [dict(b) for b in batches]


@router.get("/history")
def get_upload_history(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batches = db.execute(
        """SELECT b.*, s.name as subject_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as approved_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id
                     AND q.question_source = 'past_paper') as blended_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           WHERE b.user_id = ?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]


@router.post("/{batch_id}/reblend")
def reblend_batch(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Regenerate a Knowledge Organiser batch's past-paper blend from scratch.

    Tears down the existing blend and re-matches every KO point against the
    current (possibly grown) past-paper corpus. Runs synchronously: it's a
    single matching call. Returns a summary of what changed plus the cost.
    """
    batch = db.execute(
        "SELECT id, user_id, batch_type, subject_id FROM upload_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    if not batch or batch["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["batch_type"] != "knowledge_organiser":
        raise HTTPException(
            status_code=400, detail="Only Knowledge Organiser uploads can be blended"
        )

    try:
        summary = regenerate_blend(batch_id, user["id"], batch["subject_id"], db)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Regenerate failed: {e}")
    return summary
