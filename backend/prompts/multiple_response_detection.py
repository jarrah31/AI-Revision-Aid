MULTIPLE_RESPONSE_DETECTION_PROMPT = """You are reviewing extracted {subject} GCSE/A-Level exam questions.

Many exam questions present a printed list of candidate options and ask the student
to tick a FIXED NUMBER of boxes. This includes BOTH:
  - single-answer questions — e.g. "Which part of a plant is the largest? Tick (✓)
    one box." followed by several options, exactly one of which is correct; and
  - multiple-answer questions — e.g. "Which two sentences describe X? Tick two
    boxes." followed by several options, two (or more) of which are correct.

Your job is to identify EVERY such tick-the-box question (one box, two boxes, three
boxes — any fixed count) and structure it.

You are given a JSON array of questions, each with an id, the full question text
(stem and options may be run together), and the mark-scheme answer text.

For EACH input question return one result object. Return results in the SAME ORDER
as the input. Use this exact structure:

{{
  "results": [
    {{
      "question_id": 12,
      "is_multiple_response": true,
      "stem": "The question/instruction WITHOUT the option lines",
      "select_count": 1,
      "options": [
        {{"text": "First option, verbatim", "is_correct": true}},
        {{"text": "Second option, verbatim", "is_correct": false}},
        {{"text": "Third option, verbatim", "is_correct": false}}
      ]
    }},
    {{
      "question_id": 13,
      "is_multiple_response": false
    }}
  ]
}}

Rules:
- A question qualifies ONLY if it prints a list of candidate options AND tells the
  student to tick a fixed number of boxes (one, two, three, ...). The count may be
  written as a word ("one", "two") or a digit ("1", "2"). "Tick (✓) one box" DOES
  qualify — treat single-answer tick-box questions exactly like multi-answer ones.
- stem: the instruction text only (e.g. "Which part of a plant is the largest?").
  Remove the option lines from it. Keep any lead-in context sentence(s).
- options[].text: copy each printed option VERBATIM. Do not paraphrase, renumber, or
  drop option letters/labels that are part of the printed text.
- options[].is_correct: use the mark-scheme answer text to decide which options are
  correct. If the answer text is missing or unclear, use your {subject} knowledge.
- select_count must equal the number of options marked is_correct=true, and must match
  the number of boxes the question tells the student to tick (1 for "tick one box").
- Return is_multiple_response=false ONLY for questions that do NOT present a printed
  list of tick-box options — e.g. free-text short answer, fill-in-the-blank,
  calculations, "describe/explain/evaluate" extended writing, or labelling diagrams.
- Return ONLY valid JSON, no markdown code fences or other text.

Questions:
{questions_json}"""
