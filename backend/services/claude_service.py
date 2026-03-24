import json
import re
import anthropic

from backend.config import settings
from backend.database import get_setting
from backend.prompts.qa_extraction import QA_EXTRACTION_PROMPT
from backend.prompts.mcq_generation import MCQ_GENERATION_PROMPT
from backend.prompts.answer_judging import ANSWER_JUDGING_PROMPT
from backend.prompts.past_paper_extraction import PAST_PAPER_EXTRACTION_PROMPT
from backend.prompts.paper_type_detection import PAPER_TYPE_DETECTION_PROMPT
from backend.prompts.fact_check import FACT_CHECK_PROMPT
from backend.prompts.matching import MATCHING_PROMPT
from backend.prompts.handwritten_ocr import HANDWRITTEN_OCR_PROMPT
from backend.prompts.handwritten_qa import HANDWRITTEN_QA_PROMPT

# ── Default models ─────────────────────────────────────────────────────────────
# These are the hardcoded defaults; admins can override any of them via the
# AI Settings panel (stored in the `settings` DB table).

EXTRACTION_MODEL       = "claude-sonnet-4-6"   # Vision — PDF page images
QUIZ_MODEL             = "claude-haiku-4-5"    # Text-only — MCQ, judging, matching
FACT_CHECK_MODEL       = "claude-sonnet-4-6"   # Needs web-search tool
HANDWRITTEN_OCR_MODEL  = "claude-sonnet-4-6"   # Vision — handwritten image OCR
HANDWRITTEN_QA_MODEL   = "claude-haiku-4-5"    # Text-only — Q&A from confirmed text

# Per-model pricing (cost per million tokens) — source: platform.claude.com/docs/en/about-claude/pricing
MODEL_PRICING: dict[str, dict[str, float]] = {
    # ── Claude 4.6 (latest) ─────────────────────────────────────────────────
    "claude-opus-4-6":             {"input":  5.00 / 1_000_000, "output": 25.00 / 1_000_000},
    "claude-sonnet-4-6":           {"input":  3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    # ── Claude 4.5 ──────────────────────────────────────────────────────────
    "claude-haiku-4-5":            {"input":  1.00 / 1_000_000, "output":  5.00 / 1_000_000},
    "claude-sonnet-4-5":           {"input":  3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "claude-opus-4-5":             {"input":  5.00 / 1_000_000, "output": 25.00 / 1_000_000},
    # ── Claude 4.1 / 4 (first release) ─────────────────────────────────────
    "claude-opus-4-1":             {"input": 15.00 / 1_000_000, "output": 75.00 / 1_000_000},
    "claude-sonnet-4-20250514":    {"input":  3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "claude-opus-4-20250514":      {"input": 15.00 / 1_000_000, "output": 75.00 / 1_000_000},
    # ── Claude 3.x ──────────────────────────────────────────────────────────
    "claude-3-7-sonnet-20250219":  {"input":  3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "claude-3-5-sonnet-20241022":  {"input":  3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "claude-3-5-haiku-20241022":   {"input":  0.80 / 1_000_000, "output":  4.00 / 1_000_000},
    "claude-3-opus-20240229":      {"input": 15.00 / 1_000_000, "output": 75.00 / 1_000_000},
}
_FALLBACK_PRICING = MODEL_PRICING["claude-sonnet-4-6"]

WEB_SEARCH_COST = 10.0 / 1_000  # $10.00 / 1,000 searches

# ── Setting key registry ────────────────────────────────────────────────────────
# Maps each DB setting key → its default value.  Used by claude_service to
# resolve the live value and by admin.py to expose metadata.

AI_SETTING_DEFAULTS: dict[str, str] = {
    # Models
    "ai_model_ko_extraction":       EXTRACTION_MODEL,
    "ai_model_past_paper_extraction": EXTRACTION_MODEL,
    "ai_model_mcq":                 QUIZ_MODEL,
    "ai_model_judging":             QUIZ_MODEL,
    "ai_model_fact_check":          FACT_CHECK_MODEL,
    "ai_model_matching":            QUIZ_MODEL,
    # Prompts
    "ai_prompt_ko_extraction":      QA_EXTRACTION_PROMPT,
    "ai_prompt_past_paper_extraction": PAST_PAPER_EXTRACTION_PROMPT,
    "ai_prompt_mcq":                MCQ_GENERATION_PROMPT,
    "ai_prompt_judging":            ANSWER_JUDGING_PROMPT,
    "ai_prompt_fact_check":         FACT_CHECK_PROMPT,
    "ai_prompt_matching":           MATCHING_PROMPT,
    # Handwritten notes
    "ai_model_handwritten_ocr":     HANDWRITTEN_OCR_MODEL,
    "ai_model_handwritten_qa":      HANDWRITTEN_QA_MODEL,
    "ai_prompt_handwritten_ocr":    HANDWRITTEN_OCR_PROMPT,
    "ai_prompt_handwritten_qa":     HANDWRITTEN_QA_PROMPT,
}


def _get_ai_setting(key: str) -> str:
    """Return the live DB value for an AI setting, falling back to the default."""
    return get_setting(key) or AI_SETTING_DEFAULTS[key]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def get_client() -> anthropic.Anthropic:
    """Return an Anthropic client using the API key from DB, falling back to env."""
    api_key = get_setting("anthropic_api_key") or settings.anthropic_api_key
    return anthropic.Anthropic(api_key=api_key)


def validate_api_key(key: str) -> tuple[bool, str]:
    """Test an API key by listing models — minimal auth check, no tokens consumed."""
    try:
        client = anthropic.Anthropic(api_key=key)
        client.models.list(limit=1)
        return True, "API key is valid"
    except anthropic.AuthenticationError:
        return False, "Invalid API key — authentication failed"
    except anthropic.PermissionDeniedError:
        return False, "API key lacks required permissions"
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


def _calc_usage(message, model: str = EXTRACTION_MODEL) -> dict:
    """Extract token counts and compute cost from an API response."""
    pricing = MODEL_PRICING.get(model, _FALLBACK_PRICING)
    input_tokens  = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost_usd = (input_tokens * pricing["input"]) + (output_tokens * pricing["output"])
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd}


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from a response string."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text


# ── Extraction functions ────────────────────────────────────────────────────────

def extract_qa_from_page(image_b64: str, subject: str) -> tuple[dict, dict]:
    """Send a knowledge organiser page image to Claude and extract Q&A pairs.
    Model and prompt are read from DB settings (admin-configurable).
    Returns (result_dict, usage_dict).
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_ko_extraction")
    prompt = _get_ai_setting("ai_prompt_ko_extraction").format(subject=subject)

    message = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text",  "text": prompt},
            ],
        }],
    )
    if message.stop_reason == "max_tokens":
        raise ValueError("Claude response was truncated (page too dense — try splitting into smaller page ranges)")
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


def detect_paper_type(image_b64: str) -> tuple[dict, dict]:
    """Send the first page of a PDF to Claude to detect paper type and metadata.
    Returns (result_dict, usage_dict) where result has keys:
      paper_type, exam_board, exam_year, paper_number, tier, subject
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_past_paper_extraction")  # reuse same vision model

    message = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text",  "text": PAPER_TYPE_DETECTION_PROMPT},
            ],
        }],
    )
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


def extract_qa_from_past_paper(image_b64: str, subject: str) -> tuple[dict, dict]:
    """Send a past paper page image to Claude and extract verbatim Q&A pairs.
    Model and prompt are read from DB settings (admin-configurable).
    Returns (result_dict, usage_dict).
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_past_paper_extraction")
    prompt = _get_ai_setting("ai_prompt_past_paper_extraction").format(subject=subject)

    message = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text",  "text": prompt},
            ],
        }],
    )
    if message.stop_reason == "max_tokens":
        raise ValueError("Claude response was truncated (page too dense — try splitting into smaller page ranges)")
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


_SECTION_DETECT_PROMPT = """Identify the distinct visual sections on this knowledge organiser page.
A section is a self-contained region with its own heading or topic area.

Return JSON:
{"sections": [{"bbox_x_pct": 0.0, "bbox_y_pct": 0.0, "bbox_w_pct": 100.0, "bbox_h_pct": 45.0}]}

Rules:
- bbox values are percentages of full page dimensions (0–100)
- aim for 2–6 non-overlapping sections that follow the visible layout
- if the page has only one topic, return a single entry covering the full page
- return ONLY valid JSON"""

_HALF_SPLIT = [
    {"bbox_x_pct": 0, "bbox_y_pct":  0, "bbox_w_pct": 100, "bbox_h_pct": 50},
    {"bbox_x_pct": 0, "bbox_y_pct": 50, "bbox_w_pct": 100, "bbox_h_pct": 50},
]


def detect_page_sections(image_b64: str) -> list[dict]:
    """Detect distinct visual sections on a KO page. Returns list of bbox dicts."""
    client = get_client()
    message = client.messages.create(
        model=QUIZ_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            {"type": "text", "text": _SECTION_DETECT_PROMPT},
        ]}],
    )
    try:
        return json.loads(_strip_fences(message.content[0].text)).get("sections", [])
    except Exception:
        return []


def extract_qa_from_page_with_fallback(png_bytes: bytes, subject: str) -> tuple[dict, dict]:
    """Extract Q&A from a KO page, auto-splitting into sections if the response is truncated."""
    from backend.services.pdf_processor import png_to_base64, crop_section_to_bytes

    image_b64 = png_to_base64(png_bytes)
    try:
        return extract_qa_from_page(image_b64, subject)
    except (ValueError, json.JSONDecodeError):
        pass  # response truncated or malformed — try section splitting

    sections = detect_page_sections(image_b64)
    if len(sections) <= 1:
        sections = _HALF_SPLIT  # simple fallback if detection finds nothing useful

    all_questions: list = []
    all_images: list = []
    total_usage: dict = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    for sec in sections:
        sec_png = crop_section_to_bytes(
            png_bytes,
            sec["bbox_x_pct"], sec["bbox_y_pct"],
            sec["bbox_w_pct"], sec["bbox_h_pct"],
        )
        sec_b64 = png_to_base64(sec_png)
        try:
            sec_result, sec_usage = extract_qa_from_page(sec_b64, subject)
        except (ValueError, json.JSONDecodeError):
            continue  # skip sections that still fail
        all_questions.extend(sec_result.get("questions", []))
        all_images.extend(sec_result.get("images", []))
        total_usage["input_tokens"] += sec_usage["input_tokens"]
        total_usage["output_tokens"] += sec_usage["output_tokens"]
        total_usage["cost_usd"] += sec_usage["cost_usd"]

    return {"questions": all_questions, "images": all_images}, total_usage


def generate_mcq_distractors(questions: list[dict], subject: str) -> tuple[list, dict]:
    """Generate MCQ wrong answers for a batch of questions.
    Model and prompt are read from DB settings (admin-configurable).
    Returns (results_list, usage_dict).
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_mcq")

    questions_for_prompt = [
        {"question_id": q["id"], "question": q["question_text"], "correct_answer": q["answer_text"]}
        for q in questions
    ]
    prompt = _get_ai_setting("ai_prompt_mcq").format(
        subject=subject,
        questions_json=json.dumps(questions_for_prompt, indent=2),
    )

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


def judge_typed_answer(
    question: str, expected_answer: str, student_answer: str, subject: str
) -> tuple[dict, dict]:
    """Ask Claude to judge a student's typed answer.
    Model and prompt are read from DB settings (admin-configurable).
    Returns (result_dict, usage_dict).
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_judging")
    prompt = _get_ai_setting("ai_prompt_judging").format(
        subject=subject,
        question=question,
        expected_answer=expected_answer,
        student_answer=student_answer,
    )

    message = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


def match_ko_to_past_papers(
    ko_questions: list[dict], past_paper_questions: list[dict]
) -> list[dict]:
    """Match knowledge organiser questions to equivalent past paper questions.
    Model and prompt are read from DB settings (admin-configurable).
    Returns list of {"ko_question_id": int, "past_paper_question_id": int}.
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_matching")

    ko_list = json.dumps(
        [{"id": q["id"], "question": q["question_text"], "answer": q["answer_text"]} for q in ko_questions],
        indent=2,
    )
    pp_list = json.dumps(
        [{"id": q["id"], "question": q["question_text"], "answer": q["answer_text"]} for q in past_paper_questions],
        indent=2,
    )
    prompt = _get_ai_setting("ai_prompt_matching").format(ko_list=ko_list, pp_list=pp_list)

    message = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    result = json.loads(_strip_fences(message.content[0].text))
    return result.get("matches", [])


def extract_sections_from_handwritten(image_b64: str) -> tuple[list, dict]:
    """Send a handwritten notes image to Claude for OCR and section detection.
    Returns (sections_list, usage_dict).
    Each section dict has keys: section_order, title, content.
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_handwritten_ocr")
    prompt = _get_ai_setting("ai_prompt_handwritten_ocr")

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text",  "text": prompt},
            ],
        }],
    )
    if message.stop_reason == "max_tokens":
        raise ValueError("OCR response truncated — image may contain too much text")
    result = json.loads(_strip_fences(message.content[0].text))
    sections = result.get("sections", [])
    if not sections:
        sections = [{"section_order": 1, "title": None, "content": ""}]
    return sections, _calc_usage(message, model)


def extract_qa_from_text(text_content: str, subject: str) -> tuple[dict, dict]:
    """Generate Q&A pairs from confirmed OCR text (no image re-processing).
    Model and prompt are read from DB settings (admin-configurable).
    Returns (result_dict, usage_dict).
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_handwritten_qa")
    prompt = _get_ai_setting("ai_prompt_handwritten_qa").format(
        subject=subject,
        text_content=text_content,
    )

    message = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    if message.stop_reason == "max_tokens":
        raise ValueError("Q&A response truncated — notes may be too long")
    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message, model)


def fact_check_question(question: str, answer: str, subject: str) -> tuple[dict, dict]:
    """Use Claude with live web search to fact-check a question/answer pair.
    Model and prompt are read from DB settings (admin-configurable).

    Returns (result_dict, usage_dict).
      result_dict keys: verdict, explanation, sources, searches_performed
      usage_dict keys:  input_tokens, output_tokens, cost_usd, search_requests, search_cost_usd
    """
    client = get_client()
    model  = _get_ai_setting("ai_model_fact_check")
    prompt = _get_ai_setting("ai_prompt_fact_check").format(
        subject=subject,
        question=question,
        answer=answer,
    )

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Collect full text and deduplicated citations from all text blocks
    text_parts: list[str] = []
    sources:    list[dict] = []
    seen_urls:  set[str]   = set()

    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
            citations = getattr(block, "citations", None) or []
            for c in citations:
                url = getattr(c, "url", None) or ""
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "title":   getattr(c, "title", None) or url,
                        "url":     url,
                        "snippet": (getattr(c, "cited_text", None) or "")[:300],
                    })

    full_text = "\n".join(text_parts).strip()

    # Parse VERDICT line
    verdict = "uncertain"
    for line in full_text.splitlines()[:4]:
        m = re.match(r"VERDICT:\s*(CORRECT|INCORRECT|UNCERTAIN)", line.strip(), re.IGNORECASE)
        if m:
            verdict = m.group(1).lower()
            break

    explanation = re.sub(
        r"^VERDICT:\s*(CORRECT|INCORRECT|UNCERTAIN)\s*\n?", "", full_text, flags=re.IGNORECASE
    ).strip()

    # Calculate costs: tokens + web searches
    fc_pricing    = MODEL_PRICING.get(model, _FALLBACK_PRICING)
    input_tokens  = message.usage.input_tokens
    output_tokens = message.usage.output_tokens

    search_requests = 0
    stm = getattr(message.usage, "server_tool_use", None)
    if stm is not None:
        search_requests = (
            stm.get("web_search_requests", 0)
            if isinstance(stm, dict)
            else getattr(stm, "web_search_requests", 0)
        )

    token_cost  = (input_tokens * fc_pricing["input"]) + (output_tokens * fc_pricing["output"])
    search_cost = search_requests * WEB_SEARCH_COST

    result = {
        "verdict":            verdict,
        "explanation":        explanation,
        "sources":            sources,
        "searches_performed": search_requests,
    }
    usage = {
        "input_tokens":    input_tokens,
        "output_tokens":   output_tokens,
        "cost_usd":        token_cost + search_cost,
        "search_requests": search_requests,
        "search_cost_usd": search_cost,
    }
    return result, usage
