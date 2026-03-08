MCQ_GENERATION_PROMPT = """Generate 3 plausible but incorrect answers for each of these GCSE/A-Level {subject} questions.
The wrong answers should be:
- Similar in format and length to the correct answer
- Plausible enough to test real understanding
- Not obviously wrong
- Different from each other

Questions:
{questions_json}

Return as JSON array, one entry per question:
[
  {{
    "question_id": 1,
    "distractors": ["wrong answer 1", "wrong answer 2", "wrong answer 3"]
  }}
]

Return ONLY valid JSON, no markdown code fences or other text."""
