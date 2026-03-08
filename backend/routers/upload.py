import sqlite3
import json
import traceback
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks

from backend.auth import get_current_user
from backend.database import get_db, DB_PATH
from backend.services.pdf_processor import (
    render_page_to_png,
    save_full_page_image,
    crop_image_region,
    png_to_base64,
    get_pdf_page_count,
)
from backend.services.claude_service import extract_qa_from_page

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()


def process_batch(batch_id: int, pdf_path: str, subject_name: str, subject_id: int,
                  user_id: int, page_start: int, page_end: int):
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

        for page_num in range(page_start - 1, page_end):  # 0-indexed
            display_page = page_num + 1
            try:
                # Render page to PNG
                png_bytes = render_page_to_png(pdf_path, page_num)
                save_full_page_image(batch_id, display_page, png_bytes)

                # Send to Claude for Q&A extraction
                image_b64 = png_to_base64(png_bytes)
                result, usage = extract_qa_from_page(image_b64, subject_name)

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

                # Process image regions
                image_id_map = {}  # index -> db image id
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

                    db.execute(
                        """INSERT INTO questions
                           (batch_id, user_id, subject_id, page_number, question_text,
                            answer_text, question_type, difficulty, image_id, source_context)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

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
            total_pages, is_shared, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (user["id"], subject_id, file.filename, "", page_start, page_end,
         page_end - page_start + 1, is_shared),
    )
    db.commit()
    batch_id = cursor.lastrowid

    # Save PDF with batch ID in filename
    pdf_path = pdf_dir / f"batch_{batch_id}.pdf"
    content = await file.read()
    pdf_path.write_bytes(content)

    # Update PDF path in record
    relative_path = f"batch_{batch_id}.pdf"
    db.execute(
        "UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
        (relative_path, batch_id),
    )
    db.commit()

    # Validate page range
    total_pages = get_pdf_page_count(str(pdf_path))
    if page_start < 1 or page_end > total_pages or page_start > page_end:
        db.execute("DELETE FROM upload_batches WHERE id = ?", (batch_id,))
        db.commit()
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid page range. PDF has {total_pages} pages.",
        )

    # Kick off background processing
    background_tasks.add_task(
        process_batch,
        batch_id,
        str(pdf_path),
        subject["name"],
        subject_id,
        user["id"],
        page_start,
        page_end,
    )

    return {"batch_id": batch_id, "total_pages": page_end - page_start + 1}


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
    }


@router.get("/history")
def get_upload_history(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batches = db.execute(
        """SELECT b.*, s.name as subject_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           WHERE b.user_id = ?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]
