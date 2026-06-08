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
