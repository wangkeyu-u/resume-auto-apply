"""Resume optimizer.

The user-facing flow this powers:

  1. paste your resume text
  2. type the target company + position (optionally paste the JD)
  3. the app figures out *what that role actually requires* — grounded in a
     knowledge base built from real, web-scraped recruitment JDs
     (``data/jd_knowledge.json``) plus any JD text you paste plus, when
     configured, an LLM — and then
  4. rewrites your resume to hit those requirements, and tells you exactly
     what it changed and what to add.

Everything degrades gracefully: with no LLM configured it still produces a
solid keyword-grounded analysis and a restructured resume from the knowledge
base alone.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Optional

from . import database as db
from . import llm as llm_client, prompts
from .matcher import _tokenize
from .tailor import LEXICON

BASE = Path(__file__).resolve().parent.parent
KB_PATH = BASE / "data" / "jd_knowledge.json"

_KB_CACHE: Optional[dict] = None


def _load_kb() -> dict:
    global _KB_CACHE
    if _KB_CACHE is None:
        try:
            _KB_CACHE = json.loads(KB_PATH.read_text(encoding="utf-8"))
        except Exception:
            _KB_CACHE = {"roles": []}
    return _KB_CACHE


def _norm(s: str) -> str:
    return (s or "").lower().strip()


# --------------------------------------------------------------------------- #
# 1. figure out which role we're dealing with
# --------------------------------------------------------------------------- #
def match_role(title: str, jd_text: str = "") -> Optional[dict]:
    """Pick the best knowledge-base role for a job title / JD."""
    kb = _load_kb()
    roles = kb.get("roles", [])
    if not roles:
        return None
    hay = _norm(title) + " " + _norm(jd_text)
    best, best_score = None, 0
    for r in roles:
        score = 0
        for alias in [r.get("label", "")] + r.get("aliases", []):
            a = _norm(alias)
            if not a:
                continue
            if a in _norm(title):
                score += 5           # title hit is strong
            elif a in hay:
                score += 2
        # keyword overlap as a tiebreaker
        for kw in r.get("keywords", []) + r.get("must_have", []):
            if _norm(kw) and _norm(kw) in hay:
                score += 1
        if score > best_score:
            best, best_score = r, score
    return best if best_score > 0 else None


# --------------------------------------------------------------------------- #
# 2. analyse requirements (KB + JD text + optional LLM)
# --------------------------------------------------------------------------- #
def _extract_from_jd(jd_text: str) -> list[str]:
    """Harvest concrete tech/keywords that literally appear in a pasted JD."""
    low = _norm(jd_text)
    found = []
    for kw in LEXICON:
        if kw in low and kw not in found:
            found.append(kw)
    return found


def research_requirements(title: str, company: str = "", jd_text: str = "",
                          use_llm: bool = True) -> dict:
    """Return a structured requirement profile for a target role.

    {
      "role_label", "matched_role", "source",
      "must_have": [...], "nice_to_have": [...],
      "keywords": [...], "responsibilities": [...], "soft_skills": [...],
      "from_jd": [...]        # extracted straight from the pasted JD
    }
    """
    role = match_role(title, jd_text)
    must_have, nice_to_have, keywords, responsibilities, soft = [], [], [], [], []
    role_label = title or "目标岗位"
    if role:
        role_label = role.get("label", role_label)
        must_have = list(role.get("must_have", []))
        nice_to_have = list(role.get("nice_to_have", []))
        keywords = list(role.get("keywords", []))
        responsibilities = list(role.get("responsibilities", []))
        soft = list(role.get("soft_skills", []))

    from_jd = _extract_from_jd(jd_text)
    # JD-extracted skills the KB didn't list -> treat as must-have (it's literally asked for)
    known = {_norm(x) for x in must_have + nice_to_have}
    for k in from_jd:
        if _norm(k) not in known:
            must_have.append(k)
            known.add(_norm(k))

    source = "knowledge_base" if role else "jd_only"

    # ---- optional LLM enrichment: reconcile + fill gaps grounded in real data
    if use_llm and llm_client._resolve_config():
        enriched = _llm_requirements(title, company, jd_text, must_have, nice_to_have, role_label)
        if enriched:
            source = "knowledge_base+llm" if role else "llm"
            # merge, keeping KB/JD items first, de-duped
            def _merge(a, b):
                out, seen = [], set()
                for x in a + b:
                    if x and _norm(x) not in seen:
                        out.append(x)
                        seen.add(_norm(x))
                return out
            must_have = _merge(must_have, enriched.get("must_have", []))
            nice_to_have = _merge(nice_to_have, enriched.get("nice_to_have", []))
            keywords = _merge(keywords, enriched.get("keywords", []))
            if enriched.get("responsibilities"):
                responsibilities = _merge(responsibilities, enriched.get("responsibilities", []))

    return {
        "role_label": role_label,
        "matched_role": role.get("role") if role else None,
        "source": source,
        "company": company,
        "must_have": must_have[:20],
        "nice_to_have": nice_to_have[:20],
        "keywords": keywords[:16],
        "responsibilities": responsibilities[:8],
        "soft_skills": soft[:8],
        "from_jd": from_jd[:20],
    }


def _llm_requirements(title, company, jd_text, kb_must, kb_nice, role_label="") -> Optional[dict]:
    system = prompts.P_SYSTEM_OPTIMIZER_REQ
    ref = "、".join((kb_must + kb_nice)[:20])
    role_line = f"知识库匹配到的岗位画像：{role_label}\n" if role_label else ""
    user = (
        f"目标岗位：{title}\n公司：{company or '（未填）'}\n"
        f"{role_line}"
        f"参考要求（知识库已知，可补充/修正）：{ref or '（无）'}\n"
        f"岗位JD原文（若有）：\n{(jd_text or '（未提供，请按岗位通用要求）')[:3000]}\n\n"
        "请输出 JSON。"
    )
    raw = llm_client.chat_simple(system, user, temperature=0.3, max_tokens=1200)
    return _safe_json(raw)


# --------------------------------------------------------------------------- #
# 3. score the resume against the requirements
# --------------------------------------------------------------------------- #
_ALIASES = {
    "html5": ["html"], "css3": ["css"], "es6": ["es6", "es2015", "javascript", "js"],
    "javascript": ["js"], "typescript": ["ts"], "node.js": ["nodejs", "node"],
    "nodejs": ["node.js", "node"], "kubernetes": ["k8s"], "k8s": ["kubernetes"],
    "vue": ["vue2", "vue3", "vue.js"], "react": ["react.js", "reactjs"],
    "golang": ["go"], "go": ["golang"], "spring boot": ["springboot"],
    "spring cloud": ["springcloud"], "restful api": ["restful", "rest api", "rest"],
    "postgresql": ["postgres", "pgsql"], "对齐": ["rlhf", "dpo"],
}


def _variants(item: str) -> list[str]:
    i = _norm(item)
    out = {i}
    # drop trailing version digits: html5 -> html, vue3 -> vue
    base = re.sub(r"\d+$", "", i).strip()
    if base:
        out.add(base)
    # drop dots/spaces: node.js -> nodejs, spring boot -> springboot
    out.add(i.replace(".", "").replace(" ", ""))
    for a in _ALIASES.get(i, []):
        out.add(_norm(a))
    return [v for v in out if v]


def _coverage(resume_text: str, requirements: dict) -> dict:
    corpus = _norm(resume_text).replace(".", "").replace(" ", "")
    corpus_raw = _norm(resume_text)
    tokens = _tokenize(resume_text)

    def _hit(item: str) -> bool:
        for v in _variants(item):
            if len(v) < 2:
                continue
            if v in corpus or v in corpus_raw or v in tokens:
                return True
        return False

    must = requirements.get("must_have", [])
    nice = requirements.get("nice_to_have", [])
    matched = [m for m in must if _hit(m)]
    gaps = [m for m in must if not _hit(m)]
    nice_matched = [n for n in nice if _hit(n)]
    nice_gaps = [n for n in nice if not _hit(n)]

    total = len(must) or 1
    coverage_pct = round(len(matched) / total * 100)
    # small bonus for nice-to-haves
    bonus = min(10, len(nice_matched) * 2)
    score = min(100, coverage_pct + bonus if coverage_pct < 100 else 100)
    return {
        "coverage_pct": coverage_pct,
        "score": score,
        "matched": matched,
        "gaps": gaps,
        "nice_matched": nice_matched,
        "nice_gaps": nice_gaps,
    }


# --------------------------------------------------------------------------- #
# 4. rewrite the resume
# --------------------------------------------------------------------------- #
def optimize_resume(resume_text: str, title: str, company: str = "",
                    jd_text: str = "", use_llm: bool = True) -> dict:
    resume_text = (resume_text or "").strip()
    if not resume_text:
        return {"ok": False, "error": "请先粘贴你的简历文本"}
    if not (title or "").strip():
        return {"ok": False, "error": "请填写目标岗位名称"}

    reqs = research_requirements(title, company, jd_text, use_llm=use_llm)
    cov = _coverage(resume_text, reqs)

    optimized_text, changes, suggestions, method = "", [], [], "template"
    if use_llm and llm_client._resolve_config():
        llm_out = _llm_rewrite(resume_text, title, company, jd_text, reqs, cov)
        if llm_out:
            optimized_text = llm_out.get("optimized_resume", "").strip()
            changes = [c for c in llm_out.get("changes", []) if c]
            suggestions = [s for s in llm_out.get("suggestions", []) if s]
            method = "llm"
    if not optimized_text:
        optimized_text, changes, suggestions = _template_rewrite(
            resume_text, title, company, reqs, cov)
        method = "template"

    # always add concrete, deterministic gap advice on top
    if cov["gaps"]:
        suggestions.insert(0, "补充/突出这些岗位硬性要求（简历里暂未体现）：" + "、".join(cov["gaps"][:12]))
    if cov["nice_gaps"]:
        suggestions.append("有条件可补充这些加分项：" + "、".join(cov["nice_gaps"][:10]))

    return {
        "ok": True,
        "method": method,
        "requirements": reqs,
        "match": cov,
        "fit_score": cov.get("score", cov.get("coverage_pct", 0)),
        "missing_keywords": (cov.get("gaps", []) + cov.get("nice_gaps", []))[:15],
        "optimized_text": optimized_text,
        "optimized_html": _to_html(title, company, optimized_text),
        "changes": changes[:20],
        "suggestions": suggestions[:20],
    }


def _llm_rewrite(resume_text, title, company, jd_text, reqs, cov) -> Optional[dict]:
    system = prompts.P_SYSTEM_OPTIMIZER_REWRITE
    must = "、".join(reqs.get("must_have", [])[:16])
    nice = "、".join(reqs.get("nice_to_have", [])[:12])
    gaps = "、".join(cov.get("gaps", [])[:12])
    resp = "、".join(reqs.get("responsibilities", [])[:6])
    user = (
        f"目标岗位：{title}"
        + (f" @ {company}" if company else "")
        + f"\n岗位硬性要求：{must}\n加分项：{nice}\n"
        + (f"岗位核心职责参考：{resp}\n" if resp else "")
        + f"当前简历尚未体现的缺口：{gaps or '无'}\n"
        + (f"岗位JD原文：\n{jd_text[:2000]}\n" if jd_text else "")
        + f"\n候选人原简历：\n{resume_text[:5000]}\n\n请输出 JSON。"
    )
    raw = llm_client.chat_simple(system, user, temperature=0.5, max_tokens=3000)
    return _safe_json(raw)


def _template_rewrite(resume_text, title, company, reqs, cov):
    """Offline rewrite: prepend a targeted header + keyword block, keep body."""
    matched = cov.get("matched", [])
    gaps = cov.get("gaps", [])
    keys = (matched + reqs.get("must_have", []))
    # de-dup preserving order
    seen, ordered_keys = set(), []
    for k in keys:
        if _norm(k) not in seen:
            ordered_keys.append(k)
            seen.add(_norm(k))

    header = f"【求职意向】{title}" + (f"（{company}）" if company else "")
    keyword_line = "【核心技能（按岗位匹配排序）】" + "、".join(ordered_keys[:16])
    summary = (
        f"【自我评价】应聘 {title} 岗位，具备"
        + "、".join(matched[:6]) if matched else f"【自我评价】应聘 {title} 岗位"
    )
    summary += " 等岗位核心能力，能快速胜任相关工作要求。"

    optimized = "\n\n".join([header, summary, keyword_line,
                             "【原简历内容（已保留，建议按上面顺序调整重点）】", resume_text])

    changes = [
        f"顶部新增「求职意向：{title}」，让招聘官/ATS 第一眼看到岗位匹配。",
        "把岗位命中的核心技能提到简历前部（ATS 关键词前置）。",
        "重写自我评价，直接对齐该岗位的核心能力要求。",
    ]
    if matched:
        changes.append("突出你已具备且岗位需要的：" + "、".join(matched[:8]))
    suggestions = []
    if gaps:
        suggestions.append("重点补强缺口关键词，若确有相关经历务必写进简历：" + "、".join(gaps[:10]))
    suggestions.append("每条工作/项目经历尽量用「动词+做了什么+量化结果」，如“QPS 提升 3 倍”。")
    return optimized, changes, suggestions


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _safe_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    t = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    if not t.startswith("{"):
        s, e = t.find("{"), t.rfind("}")
        if s == -1 or e <= s:
            return None
        t = t[s:e + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def _to_html(title: str, company: str, text: str) -> str:
    esc = html.escape
    blocks = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        # a block that starts with 【..】 becomes a section header + body
        m = re.match(r"^【([^】]+)】\s*(.*)$", para, re.S)
        if m:
            headline, body = m.group(1), m.group(2).strip()
            lines = [l.strip() for l in body.splitlines() if l.strip()]
            if len(lines) > 1:
                items = "".join(f"<li>{esc(l.lstrip('-• '))}</li>" for l in lines)
                blocks.append(f"<h2>{esc(headline)}</h2><ul>{items}</ul>")
            else:
                blocks.append(f"<h2>{esc(headline)}</h2><p>{esc(body)}</p>")
        else:
            lines = [l.strip() for l in para.splitlines() if l.strip()]
            if len(lines) > 1 and all(l.startswith(("-", "•", "·")) for l in lines):
                items = "".join(f"<li>{esc(l.lstrip('-•· '))}</li>" for l in lines)
                blocks.append(f"<ul>{items}</ul>")
            else:
                blocks.append(f"<p>{esc(para)}</p>")
    body_html = "".join(blocks)
    tag = esc(title) + (f" @ {esc(company)}" if company else "")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,"PingFang SC","Segoe UI",Arial,sans-serif;color:#1a1a1a;max-width:820px;margin:28px auto;padding:0 24px;line-height:1.6}}
 h1{{font-size:22px;margin:0 0 4px}} .tag{{display:inline-block;background:#2563eb;color:#fff;font-size:12px;padding:2px 10px;border-radius:12px}}
 h2{{font-size:15px;border-bottom:2px solid #2563eb;color:#1e3a8a;padding-bottom:4px;margin:18px 0 6px}}
 ul{{margin:4px 0;padding-left:20px}} li{{margin:3px 0}} p{{margin:6px 0}}
</style></head><body>
<h1>优化后简历 <span class="tag">目标：{tag}</span></h1>
{body_html}
</body></html>"""
