import json
import re
import anthropic

from backend.config import settings
from backend.database import get_setting
from backend.prompts.qa_extraction import QA_EXTRACTION_PROMPT
from backend.prompts.mcq_generation import MCQ_GENERATION_PROMPT
from backend.prompts.answer_judging import ANSWER_JUDGING_PROMPT

# Claude Sonnet pricing (per token)
INPUT_COST_PER_TOKEN = 3.0 / 1_000_000   # $3.00 / 1M input tokens
OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000  # $15.00 / 1M output tokens
WEB_SEARCH_COST = 10.0 / 1_000            # $10.00 / 1,000 searches


def get_client() -> anthropic.Anthropic:
    """Return an Anthropic client using the API key from DB (admin-managed),
    falling back to config/env if the DB value is not set."""
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


def _calc_usage(message) -> dict:
    """Extract token counts and compute cost from an API response."""
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost_usd = (input_tokens * INPUT_COST_PER_TOKEN) + (output_tokens * OUTPUT_COST_PER_TOKEN)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from a response string."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text


def extract_qa_from_page(image_b64: str, subject: str) -> tuple[dict, dict]:
    """Send a page image to Claude and extract Q&A pairs + image regions.
    Returns (result_dict, usage_dict).
    """
    client = get_client()
    prompt = QA_EXTRACTION_PROMPT.format(subject=subject)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message)


def generate_mcq_distractors(questions: list[dict], subject: str) -> tuple[list, dict]:
    """Generate MCQ wrong answers for a batch of questions.
    Returns (results_list, usage_dict).
    """
    client = get_client()

    questions_for_prompt = [
        {"question_id": q["id"], "question": q["question_text"], "correct_answer": q["answer_text"]}
        for q in questions
    ]

    prompt = MCQ_GENERATION_PROMPT.format(
        subject=subject, questions_json=json.dumps(questions_for_prompt, indent=2)
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message)


def judge_typed_answer(
    question: str, expected_answer: str, student_answer: str, subject: str
) -> tuple[dict, dict]:
    """Ask Claude to judge a student's typed answer.
    Returns (result_dict, usage_dict).
    """
    client = get_client()

    prompt = ANSWER_JUDGING_PROMPT.format(
        subject=subject,
        question=question,
        expected_answer=expected_answer,
        student_answer=student_answer,
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    return json.loads(_strip_fences(message.content[0].text)), _calc_usage(message)


def fact_check_question(question: str, answer: str, subject: str) -> tuple[dict, dict]:
    """Use Claude with live web search to fact-check a question/answer pair.

    Returns (result_dict, usage_dict).
      result_dict keys: verdict ("correct"|"incorrect"|"uncertain"),
                        explanation (str), sources (list of {title,url,snippet}),
                        searches_performed (int)
      usage_dict keys:  input_tokens, output_tokens, cost_usd,
                        search_requests, search_cost_usd
    """
    client = get_client()

    prompt = (
        f"You are a fact-checker for GCSE/A-Level {subject} educational content.\n\n"
        f"Verify the following question and answer extracted from a student revision resource:\n\n"
        f"**Question:** {question}\n"
        f"**Answer:** {answer}\n\n"
        f"Search the web to check whether this information is factually accurate at "
        f"GCSE/A-Level {subject} standard.\n\n"
        f"Start your response with EXACTLY one of these lines:\n"
        f"VERDICT: CORRECT\n"
        f"VERDICT: INCORRECT\n"
        f"VERDICT: UNCERTAIN\n\n"
        f"Then provide a 2–3 sentence explanation citing the sources you found."
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Collect full text and deduplicated citations from all text blocks
    text_parts: list[str] = []
    sources: list[dict] = []
    seen_urls: set[str] = set()

    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
            citations = getattr(block, "citations", None) or []
            for c in citations:
                url = getattr(c, "url", None) or ""
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "title": getattr(c, "title", None) or url,
                        "url": url,
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

    # Remove the VERDICT line from the displayed explanation
    explanation = re.sub(
        r"^VERDICT:\s*(CORRECT|INCORRECT|UNCERTAIN)\s*\n?", "", full_text, flags=re.IGNORECASE
    ).strip()

    # Calculate costs: tokens + web searches
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens

    search_requests = 0
    stm = getattr(message.usage, "server_tool_use", None)
    if stm is not None:
        search_requests = (
            stm.get("web_search_requests", 0)
            if isinstance(stm, dict)
            else getattr(stm, "web_search_requests", 0)
        )

    token_cost = (input_tokens * INPUT_COST_PER_TOKEN) + (output_tokens * OUTPUT_COST_PER_TOKEN)
    search_cost = search_requests * WEB_SEARCH_COST

    result = {
        "verdict": verdict,
        "explanation": explanation,
        "sources": sources,
        "searches_performed": search_requests,
    }

    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": token_cost + search_cost,
        "search_requests": search_requests,
        "search_cost_usd": search_cost,
    }

    return result, usage
