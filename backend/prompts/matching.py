MATCHING_PROMPT = """You are matching knowledge-organiser (KO) revision points to equivalent GCSE/A-Level past exam questions.

You are given a batch as JSON with two parts:
- "exam_questions": a dictionary of candidate exam questions, each with a unique id (listed once).
- "ko_points": the knowledge-organiser points to match. Each KO point lists "candidate_ids" — the ONLY exam questions it may be matched to (these have been pre-filtered for topical relevance).

{payload}

For each KO point, decide which of ITS candidate exam questions (looked up by id in "exam_questions") genuinely test the SAME specific knowledge.

A candidate matches its KO point only when ALL of these hold:
- The same specific fact, concept, or skill is being tested.
- The candidate is a genuine exam-quality equivalent of the KO point.
- The answers are consistent (they would be marked the same way).

Rules:
- Match each KO point only against the ids in its own "candidate_ids" list. Never use an id outside that list, and never invent ids.
- Return at most 3 matches per KO point. Prefer genuinely different phrasings, contexts, or question styles over near-duplicates.
- Do not match on superficial word overlap when the knowledge content differs.
- If a KO point has no genuine match, omit it.

Return ONLY valid JSON. List one object per (KO point, exam question) pair; the same ko_question_id may appear up to three times:
{{"matches": [{{"ko_question_id": 123, "past_paper_question_id": 456}}, {{"ko_question_id": 123, "past_paper_question_id": 789}}]}}
Return {{"matches": []}} if nothing matches."""
