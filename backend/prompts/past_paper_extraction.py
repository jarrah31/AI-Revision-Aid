PAST_PAPER_EXTRACTION_PROMPT = """You are processing a page from a GCSE/A-Level {subject} exam paper or mark scheme.

First, determine the page type:
- "questions": question paper page with numbered exam questions (no mark scheme answers)
- "mark_scheme": mark scheme / answer guide page only (no exam questions)
- "both": page containing both exam questions AND their mark scheme answers together
- "cover": cover page, title page, instructions page, or blank page

Return JSON with this exact structure (always include both "questions" and "answers" arrays):
{{
  "page_type": "questions|mark_scheme|both|cover",
  "questions": [
    {{
      "question_ref": "1a",
      "question": "Exact question text as written on the paper",
      "answer": "Mark scheme answer if visible; otherwise AI-inferred answer",
      "marks": 2,
      "type": "factual|definition|calculation|extended_writing|diagram-based",
      "difficulty": 2
    }}
  ],
  "answers": [
    {{
      "question_ref": "1a",
      "answer": "Full mark scheme answer with all creditworthy points"
    }}
  ]
}}

Rules by page type:

QUESTION PAGES (page_type "questions" or "both"):
- questions[]: extract each numbered question and sub-question
  - question_ref: normalise to plain lowercase form — "1a" not "1 (a)" or "Question 1a" or "1a."
  - question: copy the question text EXACTLY and VERBATIM — do not paraphrase or reword
  - answer: use visible mark scheme text if on same page; otherwise use your {subject} GCSE/A-Level knowledge to provide all key mark-scheme points
  - marks: integer if shown (e.g. "[2 marks]" → 2), null if not shown
  - type: factual, definition, calculation, extended_writing, or diagram-based
  - difficulty: 1=basic recall (1-2 marks), 2=application/understanding (3-4 marks), 3=evaluation/extended (5+ marks)

MARK SCHEME PAGES (page_type "mark_scheme" or "both"):
- answers[]: extract every question reference and its accepted answer(s)
  - question_ref: normalise exactly the same way as above ("1a" not "1 (a)")
  - answer: include ALL creditworthy points; separate points with " / " or semicolons; for extended writing include all bullet points
- questions[]: leave as [] for pure mark scheme pages

COVER / INSTRUCTIONS PAGES (page_type "cover"):
- Extract exam metadata from the cover page and return:
  {{"page_type": "cover", "questions": [], "answers": [],
    "exam_board": "AQA|Edexcel|OCR|WJEC|CCEA|Cambridge|Other|null",
    "exam_year": 2023,
    "paper_number": "Paper 1|Paper 2|Unit 1|etc|null",
    "tier": "Foundation|Higher|null"}}
- Set a field to null if it is not visible on this page

General rules:
- Only extract actual exam questions — skip rubric text, page numbers, examiner use boxes
- Normalise question_ref consistently: remove spaces, brackets, dots — "2b" not "2 (b)" or "2b."
- Return ONLY valid JSON, no markdown code fences or other text"""
