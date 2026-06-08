GROUNDING_PROMPT = """You are checking whether a Knowledge Organiser (KO) revision page genuinely supports a set of candidate exam questions that have been matched to one KO point.

You are given an IMAGE of the KO page, and JSON:

{payload}

The JSON has:
- "ko_point": the KO revision point ("ko_question", "ko_answer").
- "candidates": exam questions matched to it, each with a unique "id", "question", "answer".

For EACH candidate, look at the KO page image and decide whether the page genuinely contains the knowledge needed to answer that exam question — the same specific fact/concept, consistent with the candidate's answer.

For each candidate return:
- "id": the candidate id (unchanged).
- "supported": true only if the KO page genuinely contains the supporting knowledge; false otherwise. Be strict — superficial topic overlap is NOT support.
- "reasoning": one or two sentences, student-friendly, naming where on the page the answer is found (e.g. "The 'Levels of Organisation' box defines an organ as a group of tissues."). For unsupported candidates, briefly say why not.
- "snippet": the exact short phrase quoted from the KO page that supports it (empty string if unsupported).
- "bbox_pct": the approximate rectangle on the page containing that snippet, as percentages of the page: {{"x": <left%>, "y": <top%>, "w": <width%>, "h": <height%>}}. A generous, approximate box is fine. Use null if unsupported or you cannot locate it.

Return ONLY valid JSON, no prose:
{{"results": [{{"id": 456, "supported": true, "reasoning": "...", "snippet": "...", "bbox_pct": {{"x": 5, "y": 10, "w": 40, "h": 12}}}}]}}
"""
