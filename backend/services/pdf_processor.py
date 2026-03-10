import base64
from pathlib import Path
import pymupdf
from PIL import Image
import io

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def render_page_to_png(pdf_path: str, page_num: int, dpi: int = 200) -> bytes:
    """Render a single PDF page to PNG bytes at the given DPI."""
    doc = pymupdf.open(pdf_path)
    page = doc[page_num]
    scale = dpi / 72
    mat = pymupdf.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def save_full_page_image(batch_id: int, page_number: int, png_bytes: bytes) -> str:
    """Save a full-page PNG and return the relative filename."""
    batch_dir = DATA_DIR / "images" / f"batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    filename = f"page_{page_number}_full.png"
    filepath = batch_dir / filename
    filepath.write_bytes(png_bytes)
    return f"batch_{batch_id}/{filename}"


def crop_image_region(
    batch_id: int,
    page_number: int,
    image_index: int,
    png_bytes: bytes,
    bbox_x_pct: float,
    bbox_y_pct: float,
    bbox_w_pct: float,
    bbox_h_pct: float,
    padding_pct: float = 5.0,
) -> tuple[str, int, int]:
    """Crop a region from a page PNG. Returns (relative_filename, width, height)."""
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size

    # Convert percentages to pixels with padding
    x1 = max(0, int((bbox_x_pct - padding_pct) / 100 * w))
    y1 = max(0, int((bbox_y_pct - padding_pct) / 100 * h))
    x2 = min(w, int((bbox_x_pct + bbox_w_pct + padding_pct) / 100 * w))
    y2 = min(h, int((bbox_y_pct + bbox_h_pct + padding_pct) / 100 * h))

    cropped = img.crop((x1, y1, x2, y2))

    batch_dir = DATA_DIR / "images" / f"batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    filename = f"page_{page_number}_img_{image_index}.png"
    filepath = batch_dir / filename
    cropped.save(filepath, "PNG", optimize=True)

    return f"batch_{batch_id}/{filename}", cropped.width, cropped.height


def crop_section_to_bytes(
    png_bytes: bytes,
    bbox_x_pct: float,
    bbox_y_pct: float,
    bbox_w_pct: float,
    bbox_h_pct: float,
    padding_pct: float = 1.0,
) -> bytes:
    """Crop a section from a page PNG and return PNG bytes (not saved to disk)."""
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    x1 = max(0, int((bbox_x_pct - padding_pct) / 100 * w))
    y1 = max(0, int((bbox_y_pct - padding_pct) / 100 * h))
    x2 = min(w, int((bbox_x_pct + bbox_w_pct + padding_pct) / 100 * w))
    y2 = min(h, int((bbox_y_pct + bbox_h_pct + padding_pct) / 100 * h))
    cropped = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    cropped.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def png_to_base64(png_bytes: bytes) -> str:
    """Encode PNG bytes to base64 string for Claude API."""
    return base64.standard_b64encode(png_bytes).decode("utf-8")


def get_pdf_page_count(pdf_path: str) -> int:
    """Get total number of pages in a PDF."""
    doc = pymupdf.open(pdf_path)
    count = len(doc)
    doc.close()
    return count
