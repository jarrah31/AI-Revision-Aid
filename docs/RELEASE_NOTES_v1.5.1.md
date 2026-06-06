# v1.5.1 — Paper-level Subject & Category

A focused follow-up to v1.5.0 that makes past papers easy to organise — especially
after importing them into a fresh install.

## Highlights

### Assign a Subject & Category per past paper
- Each past paper now carries its own **Subject** and **Category**, editable
  right from the **Past Paper Library** page. Every paper shows a category pill
  (or "Uncategorised") with an **Edit** button.
- The editor lets you choose the Subject, **pick an existing Category** from a
  dropdown, or **type a brand-new one** in the "…or new category" box — ideal
  when a freshly imported subject has no categories yet.
- Changing a paper's classification **cascades to all of its questions**, so
  quizzing and coverage by category/subject keep working correctly.

### Imported papers keep their category
- Fixes the root cause behind imported papers landing uncategorised: category is
  now stored on the paper itself, so it travels with **export → import**.

### One-time backfill
- Existing past papers are automatically seeded with the most common category
  among their questions, preserving any tagging you'd already done — no manual
  re-tagging needed.

### Simpler question view
- The old per-question category dropdown and bulk-tag toolbar are gone; category
  is now a single, paper-level choice.

## Notes
- No database action required — the backfill runs automatically on startup and
  is a safe no-op thereafter.
