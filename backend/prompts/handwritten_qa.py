HANDWRITTEN_QA_PROMPT = """You are analysing GCSE/A-Level {subject} revision notes that have been transcribed from handwritten cards.
Extract question-and-answer pairs that test recall of the knowledge in these notes.

Notes:
{text_content}

Return ONLY valid JSON with this exact structure:
{{
  "questions": [
    {{
      "question": "What is...?",
      "answer": "...",
      "source_quote": "Verbatim phrase from the notes this Q&A is drawn from.",
      "type": "factual",
      "difficulty": 1
    }}
  ]
}}

Rules:
- Generate as many questions as the content warrants — one question per distinct fact, definition, concept, or process in the notes. Do not artificially limit or pad the count; let the density of the content determine it.
- Every question and answer must be directly based on text that is explicitly present in the notes above. Do not infer, extrapolate, or use outside knowledge.
- Questions must be specific and unambiguous — one clear question, one clear answer.
- Do not create multiple questions with the same answer.
- type: one of factual, definition, process, comparison.
- difficulty: 1=basic recall, 2=understanding, 3=application/analysis.
- source_quote: copy the exact word-for-word phrase (1–2 sentences) from the notes that this Q&A is based on.
- Return ONLY valid JSON, no markdown code fences or other text."""
