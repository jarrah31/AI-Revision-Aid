ANSWER_JUDGING_PROMPT = """You are marking a GCSE/A-Level {subject} answer. Be fair but accurate.

Question: {question}
Expected answer: {expected_answer}
Student's answer: {student_answer}

Judge whether the student's answer is correct. Accept:
- Different wording that conveys the same meaning
- Minor spelling mistakes
- Partial answers (mark as partially correct)

Return JSON:
{{
  "verdict": "correct|partially_correct|incorrect",
  "feedback": "Brief explanation of what was right/wrong",
  "quality_score": 5
}}

quality_score scale (for spaced repetition):
- 5 = perfect, complete answer
- 4 = correct but hesitant or minor omission
- 3 = correct with serious difficulty or notable gaps
- 2 = incorrect but showed some understanding
- 1 = incorrect, remembered after seeing answer
- 0 = complete blank / no understanding

Return ONLY valid JSON, no markdown code fences or other text."""
