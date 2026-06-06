# v1.5.2 — Manual paper pairing & multi-pair processing fix

A focused follow-up to v1.5.1 that makes the past-paper upload flow robust when
auto-detection can't pair a question paper with its mark scheme — and fixes
processing of more than one pair at a time.

## Manually pair a question paper with its mark scheme
- When detection can't match a QP to its MS, every question-paper card on the
  **Verify Detected Papers** step now has a **mark-scheme dropdown**. Pick the
  right mark scheme by hand and the pair is processed together.
- This applies to **both** matched-pair cards (a QP whose MS wasn't found) and
  the new **Unmatched Question Papers** section.
- An already-attached mark scheme stays selectable, so you can also **re-pair** a
  QP if detection matched the wrong one.
- Unmatched question papers are now fully editable — set the **year, paper
  number and tier** yourself before processing.

## Detected metadata edits now actually apply
- Fixes a bug where the Year / Paper / Tier / Board edits on the confirmation
  step were ignored — the auto-detected values were always stored. Your
  corrections are now honoured (falling back to detection only where you didn't
  override a field).

## Processing more than one paper pair
- Fixes a "Failed to check status" error (stuck on "0 of 0 complete") when
  submitting two or more pairs at once. The progress page now polls batch status
  correctly for any number of papers.
