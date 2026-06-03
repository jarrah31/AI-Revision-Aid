MULTIPLE_RESPONSE_DETECTION_PROMPT = """You are reviewing extracted {subject} GCSE/A-Level exam questions.

Some exam questions present a list of candidate statements and ask the student to
tick a FIXED NUMBER of boxes — e.g. "Which two sentences describe X? Tick two boxes."
followed by several candidate sentences. Your job is to identify ONLY those
fixed-count multiple-response questions and structure them.

You are given a JSON array of questions, each with an id, the full question text
(stem and options may be run together), and the mark-scheme answer text.

For EACH input question return one result object. Return results in the SAME ORDER
as the input. Use this exact structure:

{{
  "results": [
    {{
      "question_id": 12,
      "is_multiple_response": true,
      "stem": "The question/instruction WITHOUT the option sentences",
      "select_count": 2,
      "options": [
        {{"text": "First candidate statement, verbatim", "is_correct": false}},
        {{"text": "Second candidate statement, verbatim", "is_correct": true}}
      ]
    }},
    {{
      "question_id": 13,
      "is_multiple_response": false
    }}
  ]
}}

Rules:
- A question is multiple-response ONLY if it lists candidate statements/options AND
  asks for a fixed number of ticks (two boxes, three boxes, etc.). The count may be
  written as a word ("two") or digit ("2").
- stem: the instruction text only (e.g. "Which two sentences describe malignant
  tumours?"). Remove the option sentences from it. Keep any lead-in context sentence.
- options[].text: copy each candidate statement VERBATIM. Do not paraphrase.
- options[].is_correct: use the mark-scheme answer text to decide which options are
  correct. If the answer text is missing or unclear, use your {subject} knowledge.
- select_count must equal the number of options marked is_correct=true, and must be
  the number the question asks the student to tick.
- For anything that is NOT a fixed-count multiple-response question (ordinary
  short-answer, calculation, extended writing, single-best-answer with one blank,
  etc.) return {{"question_id": <id>, "is_multiple_response": false}}.
- Return ONLY valid JSON, no markdown code fences or other text.

Questions:
{questions_json}"""
