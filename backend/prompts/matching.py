MATCHING_PROMPT = """You are matching questions from a knowledge organiser (KO) to equivalent questions from GCSE/A-Level past exam papers.

KO Questions (AI-extracted summaries of knowledge organiser content):
{ko_list}

Past Paper Questions (verbatim from real exam papers):
{pp_list}

Task: For each KO question, find the BEST matching past paper question that tests the SAME specific knowledge point.

Match criteria (ALL must be true):
- The same specific fact, concept, or skill is being tested
- The past paper question is a genuine exam-quality equivalent
- The answers are consistent (they would be marked the same way)

Do NOT match based on superficial word similarity if the knowledge content differs.
Each past paper question can only be used for ONE KO question (no duplicates).

Return ONLY valid JSON: {{"matches": [{{"ko_question_id": 123, "past_paper_question_id": 456}}]}}
Return an empty matches array if no genuine matches exist."""
