import shutil
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def delete_batch_images(batch_id: int):
    """Delete all images for a batch."""
    batch_dir = DATA_DIR / "images" / f"batch_{batch_id}"
    if batch_dir.exists():
        shutil.rmtree(batch_dir)


def delete_batch_pdf(batch_id: int):
    """Delete the stored PDF for a batch."""
    pdf_path = DATA_DIR / "pdfs" / f"batch_{batch_id}.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
