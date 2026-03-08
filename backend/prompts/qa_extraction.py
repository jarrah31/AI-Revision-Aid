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
- Generate 5-15 questions per page depending on content density.
- For tables, create questions about specific rows/values.
- For diagrams, create questions that require understanding the visual. Set related_image_index to the 0-based index in the images array.
- For definitions, create "What is...?" style questions.
- For processes, create sequencing or explanation questions.
- Difficulty: 1=basic recall, 2=understanding, 3=application/analysis.
- The "type" field should categorise the question appropriately.
- source_quote: copy the exact word-for-word text from the page (1-2 sentences) that this Q&A is based on. For diagram-based questions use the caption or label text; if there is none write a brief description of the visual element.
- Only include image regions for meaningful diagrams/charts/illustrations, not decorative elements.
- Return ONLY valid JSON, no markdown code fences or other text."""
