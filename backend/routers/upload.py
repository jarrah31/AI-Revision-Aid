import sqlite3
import json
import traceback
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from typing import Optional

from backend.auth import get_current_user
from backend.database import get_db, DB_PATH
from backend.services.pdf_processor import (
    render_page_to_png,
    save_full_page_image,
    crop_image_region,
    png_to_base64,
    get_pdf_page_count,
)
from backend.services.claude_service import (
    extract_qa_from_page,
    extract_qa_from_past_paper,
    match_ko_to_past_papers,
)

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()

# Page types that contain exam questions
_QUESTION_PAGE_TYPES = {"questions", "both"}
# Page types that contain mark scheme answers
_MS_PAGE_TYPES = {"mark_scheme", "both"}


def _normalise_ref(ref: str) -> str:
    """Normalise a question_ref to a consistent form for matching.
    e.g. '1 (a)' -> '1a', '2b.' -> '2b', 'Question 3' -> '3'
    """
    if not ref:
        return ref
    import re
    r = ref.strip().lower()
    r = re.sub(r"^question\s*", "", r)   # strip leading "question"
    r = re.sub(r"[\s.()\[\]]+", "", r)   # remove spaces, brackets, dots
    return r


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
                   (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
                   VALUES (?, ?, 'ms_extraction', ?, ?, ?)""",
                (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
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
                "UPDATE questions SET answer_text = ?, updated_at = datetime('now') WHERE id = ?",
                (ms_answers[ref], q["id"]),
            )
            updated += 1

    if updated:
        db.commit()
        print(f"[MS correlation] updated {updated} questions with mark scheme answers")

    return updated


def _match_and_replace_with_past_papers(
    batch_id: int, user_id: int, subject_id: int, db: sqlite3.Connection
) -> None:
    """After KO processing, replace AI questions with past paper equivalents where found."""
    ko_questions = db.execute(
        "SELECT id, question_text, answer_text FROM questions "
        "WHERE batch_id = ? AND question_source = 'ai_generated'",
        (batch_id,),
    ).fetchall()
    if not ko_questions:
        return

    past_paper_qs = db.execute(
        """SELECT q.id, q.question_text, q.answer_text,
                  b.exam_board, b.exam_year, b.paper_number
           FROM questions q
           JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.subject_id = ? AND q.user_id = ? AND q.question_source = 'past_paper'
           ORDER BY q.id DESC
           LIMIT 100""",
        (subject_id, user_id),
    ).fetchall()
    if not past_paper_qs:
        return  # No past papers uploaded yet — graceful no-op

    try:
        matches = match_ko_to_past_papers(
            [dict(q) for q in ko_questions],
            [dict(q) for q in past_paper_qs],
        )
    except Exception as e:
        print(f"[match_ko_to_past_papers] matching failed: {e}")
        return

    if not matches:
        return

    used_pp_ids: set[int] = set()
    for m in matches:
        ko_q_id = m.get("ko_question_id")
        pp_q_id = m.get("past_paper_question_id")
        if not ko_q_id or not pp_q_id:
            continue
        if pp_q_id in used_pp_ids:
            continue  # Each past paper question only used once
        used_pp_ids.add(pp_q_id)

        pp_q = next((q for q in past_paper_qs if q["id"] == pp_q_id), None)
        if not pp_q:
            continue

        # Build human-readable source label e.g. "AQA 2023 Paper 1"
        parts = [pp_q["exam_board"] or "", str(pp_q["exam_year"] or ""), pp_q["paper_number"] or ""]
        source_detail = " ".join(p for p in parts if p).strip()

        db.execute(
            """UPDATE questions
               SET question_text = ?,
                   answer_text = ?,
                   question_source = 'past_paper',
                   question_source_detail = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (pp_q["question_text"], pp_q["answer_text"], source_detail or None, ko_q_id),
        )

    db.commit()
    print(f"[match_ko_to_past_papers] replaced {len(used_pp_ids)} questions with past paper equivalents")


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
):
    """Background task: process PDF pages through Claude and store results."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    try:
        db.execute(
            "UPDATE upload_batches SET status = 'processing' WHERE id = ?",
            (batch_id,),
        )
        db.commit()

        # Collect mark scheme answers found inline (combined Q+MS pages or mark scheme sections)
        ms_answers_inline: dict[str, str] = {}

        for page_num in range(page_start - 1, page_end):  # 0-indexed
            display_page = page_num + 1
            try:
                # Render page to PNG
                png_bytes = render_page_to_png(pdf_path, page_num)
                save_full_page_image(batch_id, display_page, png_bytes)

                # Send to Claude — different extraction for past papers vs KO
                image_b64 = png_to_base64(png_bytes)

                if batch_type == "past_paper":
                    result, usage = extract_qa_from_past_paper(image_b64, subject_name)
                    page_type = result.get("page_type", "cover")

                    # Collect any mark scheme answers from this page (combined or MS section)
                    if page_type in _MS_PAGE_TYPES:
                        for ans in result.get("answers", []):
                            ref = _normalise_ref(ans.get("question_ref", ""))
                            answer = ans.get("answer", "").strip()
                            if ref and answer:
                                ms_answers_inline[ref] = answer

                    # Skip pages with no exam questions
                    if page_type not in _QUESTION_PAGE_TYPES:
                        db.execute(
                            "UPDATE upload_batches SET processed_pages = ?, cost_usd = cost_usd + ? WHERE id = ?",
                            (page_num - (page_start - 1) + 1, usage["cost_usd"], batch_id),
                        )
                        db.execute(
                            """INSERT INTO api_usage
                               (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
                               VALUES (?, ?, 'qa_extraction', ?, ?, ?)""",
                            (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
                        )
                        db.commit()
                        continue

                    question_source = "past_paper"
                else:
                    result, usage = extract_qa_from_page(image_b64, subject_name)
                    question_source = "ai_generated"

                # Record API usage for this page
                db.execute(
                    """INSERT INTO api_usage
                       (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
                       VALUES (?, ?, 'qa_extraction', ?, ?, ?)""",
                    (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
                )
                db.execute(
                    "UPDATE upload_batches SET cost_usd = cost_usd + ? WHERE id = ?",
                    (usage["cost_usd"], batch_id),
                )

                # Process image regions (KO batches only — past papers rarely need image crops)
                image_id_map = {}  # index -> db image id
                if batch_type != "past_paper":
                    for i, img_data in enumerate(result.get("images", [])):
                        filename, width, height = crop_image_region(
                            batch_id,
                            display_page,
                            i,
                            png_bytes,
                            img_data.get("bbox_x_pct", 0),
                            img_data.get("bbox_y_pct", 0),
                            img_data.get("bbox_w_pct", 100),
                            img_data.get("bbox_h_pct", 100),
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
                    image_id = image_id_map.get(related_idx) if related_idx is not None else None
                    q_ref = _normalise_ref(q.get("question_ref", "")) or None

                    db.execute(
                        """INSERT INTO questions
                           (batch_id, user_id, subject_id, page_number, question_text,
                            answer_text, question_type, difficulty, image_id, source_context,
                            question_source, question_ref)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id,
                            user_id,
                            subject_id,
                            display_page,
                            q.get("question", ""),
                            q.get("answer", ""),
                            q.get("type", "factual"),
                            q.get("difficulty", 1),
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

        elif batch_type == "knowledge_organiser":
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


@router.post("")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    subject_id: int = Form(...),
    page_start: int = Form(...),
    page_end: int = Form(...),
    is_shared: int = Form(0),
    batch_type: str = Form("knowledge_organiser"),
    exam_board: str | None = Form(None),
    exam_year: int | None = Form(None),
    paper_number: str | None = Form(None),
    tier: str | None = Form(None),
    mark_scheme_file: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Validate batch_type
    if batch_type not in ("knowledge_organiser", "past_paper"):
        batch_type = "knowledge_organiser"

    # Validate mark scheme file if provided
    if mark_scheme_file and mark_scheme_file.filename:
        if not mark_scheme_file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Mark scheme must be a PDF file")
    else:
        mark_scheme_file = None

    # Verify subject exists
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Save PDF
    pdf_dir = DATA_DIR / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # Create batch record first to get ID
    cursor = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end,
            total_pages, is_shared, status, batch_type, exam_board, exam_year, paper_number, tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
        (
            user["id"], subject_id, file.filename, "", page_start, page_end,
            page_end - page_start + 1, is_shared,
            batch_type,
            exam_board if batch_type == "past_paper" else None,
            exam_year if batch_type == "past_paper" else None,
            paper_number if batch_type == "past_paper" else None,
            tier if batch_type == "past_paper" else None,
        ),
    )
    db.commit()
    batch_id = cursor.lastrowid

    # Save question paper PDF
    qp_pdf_path = pdf_dir / f"batch_{batch_id}.pdf"
    content = await file.read()
    qp_pdf_path.write_bytes(content)

    # Save mark scheme PDF (if provided)
    ms_pdf_path_str: str | None = None
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

    # Kick off background processing
    background_tasks.add_task(
        process_batch,
        batch_id,
        str(qp_pdf_path),
        subject["name"],
        subject_id,
        user["id"],
        page_start,
        page_end,
        batch_type,
        ms_pdf_path_str,
    )

    return {
        "batch_id": batch_id,
        "total_pages": page_end - page_start + 1,
        "has_mark_scheme": ms_pdf_path_str is not None,
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
    }


@router.get("/history")
def get_upload_history(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batches = db.execute(
        """SELECT b.*, s.name as subject_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as approved_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           WHERE b.user_id = ?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]
