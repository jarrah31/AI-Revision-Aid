QA_EXTRACTION_PROMPT = """You are analysing a GCSE/A-Level {subject} knowledge organiser page.
Extract question-and-answer pairs from ALL the content on this page.

For each piece of information, create a specific question that tests recall of that knowledge.

Also identify any diagrams, images, charts, tables, or visual elements that are important
for understanding. For each image region, provide approximate bounding box coordinates
as percentages of the page dimensions (x%, y%, width%, height%).

Return your response as JSON with this exact structure:
{{
  "questions": [
    {{
      "question": "What is...?",
      "answer": "...",
      "source_quote": "Verbatim sentence or phrase from the page this Q&A is drawn from.",
      "type": "factual|definition|process|diagram-based|comparison",
      "difficulty": 1,
      "related_image_index": null
    }}
  ],
  "images": [
    {{
      "description": "Diagram showing...",
      "bbox_x_pct": 10.0,
      "bbox_y_pct": 30.0,
      "bbox_w_pct": 45.0,
      "bbox_h_pct": 40.0
    }}
  ]
}}

Rules:
- Generate up to 30 questions per page. If there are more than 30 facts, select the 30 most important and clearly-stated ones — prioritise key definitions, core facts, and named concepts.
- Every question and answer must be directly based on text that is explicitly written on the page. Do not infer, extrapolate, or use outside knowledge — if it is not stated on the page, do not create a question about it.
- Questions must be simple, specific, and unambiguous. A student should immediately understand what fact is being asked. Avoid wordy, compound, or vague phrasing — one clear question, one clear answer.
- Do not create multiple questions that have the same answer.
- For tables, create questions about specific rows/values.
- For diagrams, create questions that require understanding the visual. Set related_image_index to the 0-based index in the images array.
- For definitions, create "What is...?" style questions.
- For processes, create sequencing or explanation questions.
- Difficulty: 1=basic recall, 2=understanding, 3=application/analysis.
- The "type" field should categorise the question appropriately.
- source_quote: copy the exact word-for-word text from the page (1-2 sentences) that this Q&A is based on. For diagram-based questions use the caption or label text; if there is none write a brief description of the visual element.
- Only include image regions for meaningful diagrams/charts/illustrations, not decorative elements.
- Return ONLY valid JSON, no markdown code fences or other text."""
