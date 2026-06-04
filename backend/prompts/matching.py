MATCHING_PROMPT = """You are matching questions from a knowledge organiser (KO) to equivalent questions from GCSE/A-Level past exam papers.

KO Questions (AI-extracted summaries of knowledge organiser content):
{ko_list}

Past Paper Questions (verbatim from real exam papers):
{pp_list}

Task: For each KO question, find ALL past paper questions (up to 3) that test the SAME specific knowledge point.

Match criteria (ALL must be true for every match):
- The same specific fact, concept, or skill is being tested
- The past paper question is a genuine exam-quality equivalent
- The answers are consistent (they would be marked the same way)

Keeping multiple matches:
- Include more than one past paper question for the same KO point ONLY when they ask for that knowledge in a genuinely different way (different phrasing, context, or question style) — this gives the student useful reinforcement.
- Do NOT include verbatim or near-duplicate questions that merely repeat the same wording.
- Return at most 3 past paper questions per KO question.

Do NOT match based on superficial word similarity if the knowledge content differs.
Each past paper question can only be used for ONE KO question (no duplicates across KO questions).

Return ONLY valid JSON. List one object per (KO question, past paper question) pair; the same ko_question_id may appear multiple times:
{{"matches": [{{"ko_question_id": 123, "past_paper_question_id": 456}}, {{"ko_question_id": 123, "past_paper_question_id": 789}}]}}
Return an empty matches array if no genuine matches exist."""
