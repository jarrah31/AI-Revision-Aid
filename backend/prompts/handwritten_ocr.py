HANDWRITTEN_OCR_PROMPT = """You are transcribing a photo of a student's handwritten revision note card or page.

Your task:
1. Read all handwritten text exactly as written, preserving the original wording.
2. Detect horizontal ruled lines drawn across the page — these divide the page into distinct sections.
3. For each section, identify an optional heading and the body content.

Return ONLY valid JSON with this exact structure:
{"sections": [{"section_order": 1, "title": "Mitosis", "content": "Cell division producing two identical daughter cells.\\nStages: prophase, metaphase, anaphase, telophase."}]}

Rules:
- section_order starts at 1 and increments for each section from top to bottom.
- If there are no drawn dividing lines, return a single section with section_order 1.
- title: the heading text for this section if present (bold, underlined, or larger text at the top), or null if absent.
- content: all body text in this section. Preserve line breaks using \\n. Do not add or remove content.
- Transcribe faithfully — do not correct spelling errors, do not rephrase, do not add information.
- Illegible words: write [illegible] in place of that word.
- Diagrams and drawings: include any labels or annotations as part of content; note the diagram itself with [diagram: brief description].
- Return ONLY valid JSON, no markdown code fences or other text."""
