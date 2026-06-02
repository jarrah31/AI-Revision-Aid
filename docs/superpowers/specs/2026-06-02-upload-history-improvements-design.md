# Upload History Improvements — Design Spec

**Date:** 2026-06-02  
**Status:** Approved

## Goal

Make the upload history page easier to find and more informative by (a) linking to it from the upload form and (b) showing the options selected for each upload as scannable chip tags.

---

## 1. Link from Upload Page

Add a quiet "See previous uploads →" text link (`<a href="#upload-history">`) below the submit button in `frontend/pages/upload.html`. No new component — a single anchor in the form footer area.

---

## 2. History Card — Options Chips Row

Each card in `frontend/pages/upload-history.html` gains a second metadata line rendered as small pill tags. Tags are only rendered when the value is set (non-null, non-empty).

### Tag definitions

| Tag | Source field(s) | Label logic |
|-----|----------------|-------------|
| Source type | `source_type` + `is_handwritten` | `"Handwritten"` if `is_handwritten`, else `"PDF"` or `"Images"` |
| Upload type | `batch_type` | `"Knowledge Organiser"` — only shown when `batch_type === 'knowledge_organiser'`; past papers already have their own badge |
| Category | `category_name` (joined) | Category name as-is |
| Subcategory | `subcategory_name` (joined) | Shown as `"Category › Subcategory"` when both present; standalone name if only subcategory |
| Tier | `tier` | `"Foundation"` or `"Higher"` — past-paper uploads only |

### Visual treatment

Small rounded pill tags, consistently styled (e.g. `bg-gray-100 text-gray-600 text-xs px-2 py-0.5 rounded-full`). Rendered in the order listed above, left-to-right, wrapping if needed. No icons — text only for scannability.

---

## 3. Backend Change — `/costs/history` Query

The query in `backend/routers/costs.py` (`GET /costs/history`) currently omits several `upload_batches` columns and has no category/subcategory joins.

**Add to SELECT:**
- `b.batch_type`
- `b.source_type`
- `b.is_handwritten`
- `b.tier`
- `c.name AS category_name`
- `sc.name AS subcategory_name`

**Add to FROM/JOIN:**
```sql
LEFT JOIN categories c ON c.id = b.category_id
LEFT JOIN subcategories sc ON sc.id = b.subcategory_id
```

No new DB migrations needed — all columns already exist.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/routers/costs.py` | Extend `/costs/history` SELECT + add LEFT JOINs |
| `frontend/pages/upload-history.html` | Add chips row to each card in the `x-for` loop |
| `frontend/pages/upload.html` | Add "See previous uploads →" link below submit button |

---

## Out of Scope

- `blend_past_papers` option: not persisted in the DB, skip for now
- Filtering or searching the history list
- Delete functionality
- Moving upload-history into the main nav bar
