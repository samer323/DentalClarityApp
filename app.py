import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import json5
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_engine import analyze_post, api_key_configured, budget_status, get_saved_result

BASE = Path(__file__).resolve().parent
SOURCE_DIR = BASE / "source"
SOURCE_FILE = SOURCE_DIR / "index.html"
KNOWLEDGE_FILE = BASE / "knowledge.json"
SETTINGS_FILE = BASE / "settings.json"
TEST_POSTS_FILE = BASE / "test_posts.json"

app = FastAPI(title="DentalClarity Reddit Assistant")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_js_array(source: str, variable_name: str):
    match = re.search(rf"const\s+{re.escape(variable_name)}\s*=\s*\[", source)
    if not match:
        raise ValueError(f"Could not find JavaScript array: {variable_name}")

    start = match.end() - 1
    depth = 0
    quote = None
    escaped = False

    for i in range(start, len(source)):
        ch = source[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue

        if ch in ("'", '"', '`'):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]

    raise ValueError(f"Unclosed JavaScript array: {variable_name}")


def import_dentalclarity_source(path: Path) -> dict:
    source = path.read_text(encoding="utf-8", errors="replace")
    categories = json5.loads(extract_js_array(source, "CATEGORIES"))
    procedures = json5.loads(extract_js_array(source, "PROCEDURES"))

    category_by_id = {c.get("id"): c for c in categories}
    imported = []
    seen_ids = set()

    for raw in procedures:
        proc_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        if not proc_id or not name:
            continue
        if proc_id in seen_ids:
            raise ValueError(f"Duplicate procedure id found: {proc_id}")
        seen_ids.add(proc_id)

        cat_id = raw.get("category")
        cat = category_by_id.get(cat_id, {})
        imported.append({
            "id": proc_id,
            "name": name,
            "category": cat_id,
            "category_name": cat.get("name", cat_id or "Uncategorized"),
            "icon": raw.get("icon", "🦷"),
            "summary": raw.get("summary", ""),
            "duration": raw.get("duration", ""),
            "visits": raw.get("visits", ""),
            "painLevel": raw.get("painLevel"),
            "keywords": raw.get("keywords", []) or [],
            "overview": raw.get("overview", ""),
            "whyNeeded": raw.get("whyNeeded", ""),
            "steps": raw.get("steps", []) or [],
            "comfort": raw.get("comfort", ""),
            "benefits": raw.get("benefits", []) or [],
            "risks": raw.get("risks", ""),
            "aftercare": raw.get("aftercare", {}) or {},
            "url": f"https://www.dentalclarity.org/#procedure/{proc_id}",
            "verified": True,
        })

    if not imported:
        raise ValueError("No DentalClarity procedures were found in this index.html file.")

    category_counts = Counter(p["category_name"] for p in imported)
    result = {
        "source_filename": path.name,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "procedure_count": len(imported),
        "category_count": len(categories),
        "category_counts": dict(sorted(category_counts.items())),
        "procedures": imported,
        "import_method": "local-index-html",
        "netlify_requests": 0,
    }
    save_json(KNOWLEDGE_FILE, result)
    return result


def ensure_initial_knowledge():
    existing = load_json(KNOWLEDGE_FILE, {})
    if existing.get("procedure_count"):
        return
    if SOURCE_FILE.exists():
        try:
            import_dentalclarity_source(SOURCE_FILE)
        except Exception:
            pass


def procedure_search_blob(p: dict) -> str:
    parts = [
        p.get("name", ""), p.get("id", ""), p.get("summary", ""),
        p.get("overview", ""), p.get("whyNeeded", ""), p.get("category_name", "")
    ]
    parts.extend(p.get("keywords", []) or [])
    return " ".join(str(x) for x in parts).lower()


def local_match(post: dict, procedures: list[dict]):
    text = f"{post.get('title','')} {post.get('body','')}".lower()
    normalized_words = set(re.findall(r"[a-z0-9]+", text))
    best = None
    best_score = 0
    evidence = []

    for p in procedures:
        score = 0
        hits = []
        name = p.get("name", "").lower()
        proc_id_phrase = p.get("id", "").replace("-", " ").lower()
        keywords = [str(k).lower() for k in p.get("keywords", [])]

        if name and name in text:
            score += 65
            hits.append(f'name "{p.get("name")}"')
        if proc_id_phrase and proc_id_phrase in text and proc_id_phrase != name:
            score += 45
            hits.append(f'id phrase "{proc_id_phrase}"')

        for kw in keywords:
            if len(kw) >= 4 and kw in text:
                score += 22 if " " in kw else 12
                hits.append(f'keyword "{kw}"')

        # Small fallback based on meaningful word overlap. This is deliberately weak;
        # V2 AI will eventually replace this local heuristic.
        search_words = set(re.findall(r"[a-z0-9]+", procedure_search_blob(p)))
        common = normalized_words & search_words
        meaningful = [w for w in common if len(w) >= 5 and w not in {
            "dental", "tooth", "teeth", "dentist", "procedure", "treatment", "normal"
        }]
        score += min(len(meaningful) * 2, 14)

        score = min(score, 99)
        if score > best_score:
            best_score = score
            best = p
            evidence = hits[:4]

    if best_score < 20:
        return {"score": 0, "procedure": None, "evidence": []}
    return {"score": best_score, "procedure": best, "evidence": evidence}


ensure_initial_knowledge()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, message: str = "", error: str = ""):
    settings = load_json(SETTINGS_FILE, {"mode": "MONITOR", "monthly_ai_budget": 5.0, "allow_auto": False})
    knowledge = load_json(KNOWLEDGE_FILE, {})
    procedures = knowledge.get("procedures", [])
    test_posts = load_json(TEST_POSTS_FILE, [])
    opportunities = []
    for post in test_posts:
        match = local_match(post, procedures)
        opportunities.append({"post": post, "match": match, "ai": get_saved_result(post)})

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "settings": settings,
            "knowledge": knowledge,
            "procedures": procedures,
            "opportunities": opportunities,
            "message": message,
            "error": error,
            "ai_configured": api_key_configured(),
            "budget": budget_status(settings.get("monthly_ai_budget", 5.0)),
        },
    )


@app.post("/knowledge/import")
async def import_knowledge(source_file: UploadFile = File(...)):
    filename = (source_file.filename or "").lower()
    if not filename.endswith((".html", ".htm")):
        return RedirectResponse(url="/?error=Please+choose+the+DentalClarity+index.html+file.", status_code=303)

    SOURCE_DIR.mkdir(exist_ok=True)
    temp = SOURCE_DIR / "index-upload.tmp"
    try:
        with temp.open("wb") as f:
            shutil.copyfileobj(source_file.file, f)
        result = import_dentalclarity_source(temp)
        temp.replace(SOURCE_FILE)
        return RedirectResponse(
            url=f"/?message=Imported+{result['procedure_count']}+verified+DentalClarity+procedures+locally.+Netlify+requests:+0",
            status_code=303,
        )
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        safe = re.sub(r"[^A-Za-z0-9 ._:-]", " ", str(exc))[:220].replace(" ", "+")
        return RedirectResponse(url=f"/?error=Import+failed:+{safe}", status_code=303)


@app.post("/settings")
def save_settings(
    mode: str = Form("MONITOR"),
    monthly_ai_budget: float = Form(5.0),
    allow_auto: str | None = Form(None),
):
    mode = mode.upper()
    if mode not in {"MONITOR", "REVIEW", "AUTO"}:
        mode = "MONITOR"
    budget = max(0.0, min(float(monthly_ai_budget), 1000.0))
    settings = {
        "mode": mode,
        "monthly_ai_budget": budget,
        "allow_auto": allow_auto == "on",
    }
    save_json(SETTINGS_FILE, settings)
    return RedirectResponse(url="/?message=Settings+saved.", status_code=303)


@app.post("/ai/analyze/{post_index}")
def ai_analyze(post_index: int):
    settings = load_json(SETTINGS_FILE, {"mode": "MONITOR", "monthly_ai_budget": 5.0, "allow_auto": False})
    knowledge = load_json(KNOWLEDGE_FILE, {})
    procedures = knowledge.get("procedures", [])
    test_posts = load_json(TEST_POSTS_FILE, [])
    if post_index < 0 or post_index >= len(test_posts):
        return RedirectResponse(url="/?error=Test+post+not+found.", status_code=303)
    if not procedures:
        return RedirectResponse(url="/?error=No+verified+DentalClarity+knowledge+is+loaded.", status_code=303)
    try:
        result = analyze_post(test_posts[post_index], procedures, settings.get("monthly_ai_budget", 5.0))
        proc = result.get("procedure_name") or "No verified match"
        safe = re.sub(r"[^A-Za-z0-9 ._:-]", " ", proc)[:100].replace(" ", "+")
        return RedirectResponse(url=f"/?message=AI+analysis+complete:+{safe}", status_code=303)
    except Exception as exc:
        safe = re.sub(r"[^A-Za-z0-9 $.,:_-]", " ", str(exc))[:260].replace(" ", "+")
        return RedirectResponse(url=f"/?error=AI+analysis+failed:+{safe}", status_code=303)


@app.get("/procedure/{procedure_id}", response_class=HTMLResponse)
def procedure_detail(request: Request, procedure_id: str):
    knowledge = load_json(KNOWLEDGE_FILE, {})
    proc = next((p for p in knowledge.get("procedures", []) if p.get("id") == procedure_id), None)
    if not proc:
        return RedirectResponse(url="/?error=Procedure+not+found+in+the+verified+local+knowledge+base.", status_code=303)
    return templates.TemplateResponse("procedure.html", {"request": request, "p": proc})
