import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE = Path(__file__).resolve().parent
USAGE_FILE = BASE / "ai_usage.json"
RESULTS_FILE = BASE / "ai_results.json"
load_dotenv(BASE / ".env")

MODEL = "gpt-5.6-luna"
INPUT_PRICE_PER_MILLION = 0.20
OUTPUT_PRICE_PER_MILLION = 1.20
CLASSIFY_MAX_OUTPUT = 450
DRAFT_MAX_OUTPUT = 750


def current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def get_usage_state():
    month = current_month()
    data = load_json(USAGE_FILE, {})
    if data.get("month") != month:
        data = {
            "month": month,
            "spent_usd": 0.0,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "history": [],
        }
    else:
        # Backward compatibility with V2 usage files. V2 stored the running
        # cost as `estimated_cost` and event history as `events`. Preserve
        # those values rather than resetting the user's recorded usage.
        if "spent_usd" not in data:
            data["spent_usd"] = float(data.get("estimated_cost", 0.0) or 0.0)
        if "history" not in data:
            data["history"] = data.get("events", []) or []
        data.setdefault("calls", 0)
        data.setdefault("input_tokens", 0)
        data.setdefault("output_tokens", 0)
    save_json(USAGE_FILE, data)
    return data


def api_key_configured():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def post_key(post: dict):
    raw = f"{post.get('subreddit','')}\n{post.get('title','')}\n{post.get('body','')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def get_saved_result(post: dict):
    return load_json(RESULTS_FILE, {}).get(post_key(post))


def save_result(post: dict, result: dict):
    all_results = load_json(RESULTS_FILE, {})
    all_results[post_key(post)] = result
    save_json(RESULTS_FILE, all_results)


def calculate_cost(input_tokens: int, output_tokens: int):
    return (input_tokens / 1_000_000 * INPUT_PRICE_PER_MILLION) + (output_tokens / 1_000_000 * OUTPUT_PRICE_PER_MILLION)


def estimate_input_tokens(text: str):
    # Deliberately conservative for budget gating. English prose is often ~4 chars/token;
    # 3 chars/token plus overhead gives us a safety cushion.
    return max(1, int(len(text) / 3) + 250)


def projected_call_cost(prompt_text: str, max_output_tokens: int):
    estimated_input = estimate_input_tokens(prompt_text)
    return calculate_cost(estimated_input, max_output_tokens) * 1.15


def budget_status(monthly_budget: float):
    usage = get_usage_state()
    budget = max(float(monthly_budget or 0), 0.0)
    spent = float(usage.get("spent_usd", 0.0))
    remaining = max(budget - spent, 0.0)
    pct = (spent / budget * 100) if budget > 0 else 100.0
    return {
        **usage,
        "budget_usd": budget,
        "remaining_usd": remaining,
        "percent_used": min(pct, 100.0),
        "warning": budget > 0 and pct >= 80 and spent < budget,
        "hard_stopped": budget <= 0 or spent >= budget,
        "model": MODEL,
        "input_price": INPUT_PRICE_PER_MILLION,
        "output_price": OUTPUT_PRICE_PER_MILLION,
    }


def ensure_budget(monthly_budget: float, prompt_text: str, max_output_tokens: int):
    status = budget_status(monthly_budget)
    projected = projected_call_cost(prompt_text, max_output_tokens)
    if status["hard_stopped"]:
        raise RuntimeError("AI budget hard stop is active. Increase the monthly budget or wait for next month's automatic reset.")
    if projected > status["remaining_usd"]:
        raise RuntimeError(
            f"AI call blocked by budget protection. Remaining budget is ${status['remaining_usd']:.4f}; "
            f"this call is conservatively projected at up to ${projected:.4f}."
        )
    return projected


def record_usage(response, kind: str):
    usage_state = get_usage_state()
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost = calculate_cost(input_tokens, output_tokens)
    usage_state["spent_usd"] = round(float(usage_state.get("spent_usd", 0.0)) + cost, 8)
    usage_state["calls"] = int(usage_state.get("calls", 0)) + 1
    usage_state["input_tokens"] = int(usage_state.get("input_tokens", 0)) + input_tokens
    usage_state["output_tokens"] = int(usage_state.get("output_tokens", 0)) + output_tokens
    history = usage_state.setdefault("history", [])
    history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "model": MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 8),
    })
    usage_state["history"] = history[-100:]
    save_json(USAGE_FILE, usage_state)
    return cost, input_tokens, output_tokens


def parse_json_output(text: str):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI did not return a JSON object.")
    return json.loads(cleaned[start:end + 1])


def compact_catalog(procedures: list[dict]):
    rows = []
    for p in procedures:
        rows.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "category": p.get("category_name"),
            "keywords": p.get("keywords", []),
            "summary": p.get("summary", ""),
        })
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def classify_post(post: dict, procedures: list[dict], monthly_budget: float):
    if not api_key_configured():
        raise RuntimeError("OpenAI API key is not configured. Add OPENAI_API_KEY to the .env file first.")

    allowed_ids = {p["id"] for p in procedures}
    catalog = compact_catalog(procedures)
    prompt = f"""Reddit post:\nSubreddit: {post.get('subreddit','')}\nTitle: {post.get('title','')}\nBody: {post.get('body','')}\n\nVERIFIED DentalClarity procedure catalog:\n{catalog}\n\nReturn ONLY JSON with exactly these fields:\n{{\"procedure_id\":\"verified id or NONE\",\"confidence\":0,\"reason\":\"short explanation\",\"link_useful\":false}}\n\nRules:\n- Choose ONLY an id appearing in the verified catalog, or NONE.\n- Do not invent a procedure, page, URL, diagnosis, or medical fact.\n- Match what the Reddit user is actually asking about, not just a word overlap.\n- confidence is an integer 0-100.\n- link_useful should be true only if that verified DentalClarity procedure would directly help answer the post.\n"""
    ensure_budget(monthly_budget, prompt, CLASSIFY_MAX_OUTPUT)
    client = OpenAI()
    response = client.responses.create(
        model=MODEL,
        instructions=(
            "You are the classification layer for DentalClarity's Reddit assistant. "
            "The provided verified catalog is authoritative. Never claim a DentalClarity resource exists unless its id appears there."
        ),
        input=prompt,
        max_output_tokens=CLASSIFY_MAX_OUTPUT,
    )
    cost, in_tok, out_tok = record_usage(response, "classification")
    data = parse_json_output(response.output_text)
    proc_id = str(data.get("procedure_id", "NONE")).strip()
    if proc_id not in allowed_ids:
        proc_id = "NONE"
    try:
        confidence = max(0, min(int(data.get("confidence", 0)), 100))
    except Exception:
        confidence = 0
    return {
        "procedure_id": proc_id,
        "confidence": confidence,
        "reason": str(data.get("reason", ""))[:500],
        "link_useful": bool(data.get("link_useful", False)) and proc_id != "NONE",
        "classification_cost": cost,
        "classification_input_tokens": in_tok,
        "classification_output_tokens": out_tok,
    }


def procedure_grounding_text(p: dict):
    return json.dumps({
        "id": p.get("id"),
        "name": p.get("name"),
        "category": p.get("category_name"),
        "summary": p.get("summary"),
        "duration": p.get("duration"),
        "visits": p.get("visits"),
        "overview": p.get("overview"),
        "whyNeeded": p.get("whyNeeded"),
        "steps": p.get("steps", []),
        "comfort": p.get("comfort"),
        "benefits": p.get("benefits", []),
        "risks": p.get("risks"),
        "aftercare": p.get("aftercare", {}),
        "url": p.get("url"),
    }, ensure_ascii=False)


LINK_ROTATION_FILE = BASE / "link_rotation.json"
LINK_SENTENCES = [
    "If you're trying to picture what actually happens, this might help: {url} (DentalClarity is my site).",
    "If you want a little more detail, this explains the process pretty simply: {url} (DentalClarity is my site).",
    "More detail here if you need it: {url} (DentalClarity is my site).",
    "If you want to read a little more about it: {url} (DentalClarity is my site).",
]


def next_link_sentence(url: str) -> str:
    """Rotate locally between approved link sentences. No AI call/cost is used."""
    state = load_json(LINK_ROTATION_FILE, {"next_index": 0})
    try:
        idx = int(state.get("next_index", 0)) % len(LINK_SENTENCES)
    except Exception:
        idx = 0
    state["next_index"] = (idx + 1) % len(LINK_SENTENCES)
    save_json(LINK_ROTATION_FILE, state)
    return LINK_SENTENCES[idx].format(url=url)


def draft_reply(post: dict, procedure: dict, monthly_budget: float):
    grounding = procedure_grounding_text(procedure)
    prompt = f"""Reddit post:\nSubreddit: {post.get('subreddit','')}\nTitle: {post.get('title','')}\nBody: {post.get('body','')}\n\nVERIFIED DentalClarity content for the one matched procedure:\n{grounding}\n\nWrite ONLY the helpful answer portion of a short, natural Reddit reply, usually 25-55 words. Use fewer words when the question is simple.
Rules:
- Answer the person's actual question directly and naturally.
- Use ONLY medical/procedure facts supported by the verified DentalClarity content above.
- Do not diagnose the Reddit user or claim to know what treatment they personally need.
- Do not invent statistics, recovery times, prices, risks, alternatives, or site pages.
- Sound like a normal helpful Reddit comment: concise, conversational, plain language.
- DO NOT mention DentalClarity, affiliation, websites, URLs, links, guides, resources, or say 'more detail'. The application adds the approved link sentence separately at zero AI cost.
- Do not add a generic 'ask your dentist' ending unless it is genuinely necessary for safety.
Return ONLY JSON: {{"reply":"...","grounded":true}}
"""
    ensure_budget(monthly_budget, prompt, DRAFT_MAX_OUTPUT)
    client = OpenAI()
    response = client.responses.create(
        model=MODEL,
        instructions=(
            "You draft concise, non-diagnostic dental education replies grounded strictly in the supplied DentalClarity record. "
            "Write only the answer itself; never mention or include a website or link."
        ),
        input=prompt,
        max_output_tokens=DRAFT_MAX_OUTPUT,
    )
    cost, in_tok, out_tok = record_usage(response, "draft")
    data = parse_json_output(response.output_text)
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise ValueError("AI returned an empty draft.")

    # Defense in depth: the model is not allowed to supply the promotional/link portion.
    # If it unexpectedly emits a URL, keep only text before the first URL.
    for marker in ("https://", "http://", "www.dentalclarity.org", "dentalclarity.org"):
        pos = reply.lower().find(marker.lower())
        if pos >= 0:
            reply = reply[:pos].rstrip(" :-–—\n")
    if not reply:
        raise ValueError("AI draft contained no usable answer after link sanitization.")

    link_line = next_link_sentence(procedure.get("url", ""))
    reply = reply.rstrip() + "\n\n" + link_line
    return {
        "reply": reply,
        "draft_cost": cost,
        "draft_input_tokens": in_tok,
        "draft_output_tokens": out_tok,
        "link_sentence": link_line,
    }

def analyze_post(post: dict, procedures: list[dict], monthly_budget: float):
    classification = classify_post(post, procedures, monthly_budget)
    result = {
        **classification,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "reply": "",
        "procedure_name": None,
        "procedure_url": None,
        "total_cost": classification.get("classification_cost", 0.0),
    }
    if classification["procedure_id"] != "NONE" and classification["link_useful"]:
        procedure = next((p for p in procedures if p.get("id") == classification["procedure_id"]), None)
        if procedure:
            result["procedure_name"] = procedure.get("name")
            result["procedure_url"] = procedure.get("url")
            try:
                draft = draft_reply(post, procedure, monthly_budget)
                result.update(draft)
                result["total_cost"] = result.get("classification_cost", 0.0) + draft.get("draft_cost", 0.0)
            except RuntimeError as exc:
                # Classification remains useful even if the second AI call is blocked by the budget.
                result["draft_error"] = str(exc)
    save_result(post, result)
    return result
