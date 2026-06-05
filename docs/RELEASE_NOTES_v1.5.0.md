# v1.5.0 — Past Papers, Blended Quizzes & Backups

This is a big release centred on **past exam papers**: a full library to manage
them, the ability to blend their questions with your AI-generated knowledge-organiser
questions, richer quiz sourcing, and — new — the ability to **export and import**
papers so the expensive AI extraction is never lost.

> **Note:** these notes cover everything since **v1.4.0**, the last image that was
> actually published. (v1.4.1 was tagged but its Docker build never ran due to a
> CI failure, now fixed — so its upload-history improvements are included below.)

## ✨ Highlights

### 📚 Past Paper Library
A dedicated page to manage everything extracted from your uploaded exam papers.
- Browse every past paper by subject, with question and figure counts.
- Edit question text, answers, type, difficulty and the exam question reference (e.g. `1a`, `2(i)`).
- Tag questions to categories/subcategories — per question or in bulk ("Approve All").
- Delete a paper and all its questions/figures in one click.
- **Figure re-crop**: drag-and-resize a box over the original page to re-crop a figure, and attach/detach figures per question.
- See the source filename under each paper, plus a per-paper coverage chip.

### 🔗 Blended quizzes (knowledge organiser × past papers)
- Match each knowledge-organiser point to up to **3** real exam questions asked in different ways.
- **Regenerate a blend** against your current past-paper corpus as you add more papers — with a themed progress modal, a summary of what changed, and the AI cost of the run.
- Choose how a blended quiz behaves: **AI + Exam Mixed** (AI questions for topics not yet covered by exams, plus the matched exam questions) or **Exam Only**.
- Blended is mutually exclusive with the plain KO / Past Paper sources to keep selections unambiguous.

### 🎯 Smarter quiz sourcing & provenance
- Pick your question sources when starting a quiz: **Knowledge Organiser**, **Past Paper**, or **Blended**.
- Every question now shows little **source pills**: 🤖 AI-generated vs 📄 real exam question — and for exam questions, which paper, tier (Foundation/Higher), exam board/year and source filename it came from.
- Multiple Choice mode is hidden automatically when it doesn't apply (e.g. exam-only sources).

### ☑️ Multiple-response ("tick the boxes") questions
- Detects and stores both single- and multi-answer tick-box questions from past papers.
- Renders and marks them correctly in the quiz, including the right number of required selections.
- One-click **Re-detect tick-box questions** button (with busy/spinner state) on the Past Papers page.

### 📈 Coverage tracking
- New `GET /api/questions/coverage` endpoint with a per-paper breakdown.
- A coverage banner on the View Q&As page and a coverage chip on the previous-uploads list, so you can see how much of each paper has been turned into questions.

### 🗂️ Upload history & previous uploads _(from v1.4.1)_
- Each upload-history card now shows **option chips** at a glance: PDF / Images / Handwritten, knowledge-organiser type, the category → subcategory path, and tier.
- A **"See previous uploads →"** link below the upload submit button.
- The `/api/costs/history` endpoint returns richer per-batch detail (batch type, source type, handwritten flag, tier, category and subcategory names).

### 💸 Cost & model transparency
- Records **which AI model** processed each API call.
- Per-process cost and model breakdown on the upload history.
- Past-paper question counts surfaced per batch in the cost history.

### 💾 Past Paper export / import (backup & transfer) — new
- Export selected past papers to a single portable **`.zip`** — questions, answers, multiple-choice options, and figure images all included. The download is named after the exam file.
- Import that zip into any RevisionAid instance to recreate the papers **without re-running the AI**.
- Existing papers are detected by exam board + year + paper number + tier and **skipped with a warning** rather than duplicated.
- Original PDFs are intentionally excluded to keep backups small — figure display and re-cropping still work because the page images are bundled.
- Export/Import controls live right on the Past Papers page, with per-paper selection.

## 🐛 Fixes & robustness
- Correctly pair question papers with mark schemes when the cover omits the exam year, or when the tier letter is folded into the paper number.
- Store past-paper questions with a null answer instead of crashing when no answer is found.
- Canonicalise zero-padding when matching question references to mark-scheme answers.
- Default past-paper extraction to the faster/cheaper Haiku model.
- Skip MCQ distractor generation for past-paper questions (they carry their own options).
- Fix a doubled `/api` prefix on some Past Papers API calls and avoid spurious `/images/null` requests.
- Auto-select the first subject that has past papers when the page loads.
- Suppress recoverable MuPDF structure-tree warnings during PDF rendering.
- Don't render the re-crop modal image until it's opened (removes a spurious "page unavailable" alert on load).
- **CI:** fixed three cost-history tests that requested the wrong URL and had been failing the pipeline since v1.4.1.

---

_Full changelog: `v1.4.0...v1.5.0`._
