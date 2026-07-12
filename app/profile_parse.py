"""Resume parsing: turn free-form resume text into a structured profile.

The single biggest "hand-writing" burden in the app was typing the whole
profile + structured ``resume_data`` JSON by hand. This module removes it:
paste a resume (or any bio text) and we extract a structured candidate profile
via the configured LLM, with a regex heuristic fallback when no LLM is set.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import database as db, llm as llm_client, prompts

SYSTEM_PROMPT = prompts.P_SYSTEM_PROFILE_PARSER


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    # strip code fences if present
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    if not t.startswith("{"):
        s = t.find("{")
        e = t.rfind("}")
        if s == -1 or e == -1 or e <= s:
            return None
        t = t[s:e + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def _normalize(data: dict) -> dict:
    """Coerce the LLM output into the profile schema the rest of the app uses."""
    rd = data.get("resume_data") or {}
    if not isinstance(rd, dict):
        rd = {}
    for key in ("educations", "experiences", "projects", "certifications", "languages"):
        if key not in rd or rd[key] is None:
            rd[key] = [] if key != "links" else {}
    if "links" not in rd or not isinstance(rd["links"], dict):
        rd["links"] = {}

    skills = data.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in re.split(r"[,，、/]", skills) if s.strip()]

    try:
        years = int(data.get("years_experience") or 0)
    except (TypeError, ValueError):
        years = 0

    try:
        seniority = int(data.get("seniority") or 0)
    except (TypeError, ValueError):
        seniority = 0
    if seniority < 1 or seniority > 5:
        seniority = 0

    return {
        "name": (data.get("name") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "location": (data.get("location") or "").strip(),
        "years_experience": years,
        "seniority": seniority,
        "intent": (data.get("intent") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "email": (data.get("email") or "").strip(),
        "salary_min": _to_int(data.get("salary_min")),
        "salary_max": _to_int(data.get("salary_max")),
        "summary": (data.get("summary") or "").strip(),
        "skills": skills,
        "resume_data": rd,
    }


def _to_int(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        m = re.search(r"\d+", str(v))
        return int(m.group()) if m else None


def _heuristic(text: str) -> dict:
    """Regex fallback when no LLM is configured."""
    out: dict = {"name": "", "title": "", "location": "", "years_experience": 0,
                 "seniority": 0, "intent": "", "phone": "", "email": "",
                 "salary_min": None, "salary_max": None,
                 "summary": "", "skills": [], "resume_data": {
                     "educations": [], "experiences": [], "projects": [],
                     "certifications": [], "languages": [], "links": {}}}
    em = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if em:
        out["email"] = em.group(0)
    pm = re.search(r"(?:(?:\+?86)?1[3-9]\d{9})", text)
    if pm:
        out["phone"] = pm.group(0)
    # crude skill harvest from common tokens
    common = ["Python", "Go", "Java", "C\\+\\+", "Rust", "TypeScript", "JavaScript",
              "React", "Vue", "Kubernetes", "Docker", "gRPC", "MySQL", "Redis",
              "PostgreSQL", "Kafka", "TensorFlow", "PyTorch", "LLM", "NLP"]
    found = []
    for c in common:
        if re.search(c, text, re.I):
            found.append(c.replace("\\", ""))
    out["skills"] = found
    # first non-empty line as a guess for name/title
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) < 30 and not out["name"]:
            out["name"] = line
            break
    return out


def parse_resume(text: str, settings: dict | None = None) -> dict:
    """Return a normalized structured profile dict.

    Uses the configured LLM when available; otherwise a regex heuristic.
    Always returns a dict (possibly partial) so the UI can pre-fill the form.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "简历文本为空"}
    if llm_client._resolve_config(settings):
        user = f"以下是简历文本：\n\n{text}\n\n请按系统要求输出 JSON。"
        raw = llm_client.chat_simple(SYSTEM_PROMPT, user, temperature=0.2, max_tokens=2500)
        data = _extract_json(raw) if raw else None
        if data:
            return _normalize(data)
        # fall through to heuristic if the model returned junk
    return _heuristic(text)
