PAPER_TYPE_DETECTION_PROMPT = """You are examining the first page of a GCSE/A-Level exam document.

Determine what kind of document this is and extract any visible metadata.

Return ONLY valid JSON:
{{
  "paper_type": "question_paper|mark_scheme|combined|unknown",
  "exam_board": "AQA|Edexcel|OCR|WJEC|CCEA|Cambridge|Other|null",
  "exam_year": 2023,
  "paper_number": "Paper 1|Paper 2|Paper 3|Unit 1|Unit 2|null",
  "tier": "Foundation|Higher|null",
  "subject": "Chemistry|Mathematics|Biology|Physics|English Literature|null"
}}

Signals for each paper_type:
- question_paper: candidate name/number/signature fields, "answer in the spaces provided", "do not open until told to", timed exam format
- mark_scheme: "Mark Scheme", "Marking Guidelines", "Examiner's Report", answer/point allocation tables, "accept" or "allow" instructions
- combined: contains BOTH candidate entry fields AND mark scheme answer sections (some AQA papers include both)
- unknown: cannot determine document type from this page alone

Set numeric/string fields to null if not clearly visible on this page.
exam_year must be an integer (e.g. 2023) or null — not a string."""
