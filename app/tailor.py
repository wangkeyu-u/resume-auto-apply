"""Resume tailoring engine.

Given the candidate's FULL profile (skills + structured resume data: education,
work experience, projects, certifications, languages) and a target job, this
module produces a *job-specific* resume optimised to pass automated / recruiter
screening (ATS):

  * extracts the job's hard requirements from the JD text
  * re-orders work experience & projects by relevance to the JD
  * pushes JD-matched skills to the front (ATS keyword placement)
  * rewrites the summary to lead with role + matched keywords
  * emits a "screening report": what the JD wants vs what the candidate has,
    plus a coverage % and the exact field-by-field "what to fill in".

Default generation is offline (template). An OpenAI-compatible LLM can be used
to write a more natural tailored summary when enabled in settings.
"""
from __future__ import annotations

import html
import json
import re
from typing import Optional

from . import database as db, prompts
from .matcher import _tokenize, _seniority_from_title

# A broad keyword lexicon used to detect requirements inside a JD. Extend freely.
LEXICON = [
    # engineering / languages
    "python", "java", "go", "golang", "c++", "c#", "javascript", "typescript", "rust", "php", "swift", "kotlin",
    "sql", "mysql", "postgresql", "mongodb", "redis", "elasticsearch", "clickhouse", "hive", "hbase",
    # frontend / mobile
    "react", "vue", "angular", "node.js", "nodejs", "webpack", "flutter", "android", "ios", "小程序", "uniapp",
    # backend / infra
    "微服务", "分布式", "高并发", "kubernetes", "k8s", "docker", "容器", "linux", "nginx", "grpc", "rpc",
    "消息队列", "kafka", "rabbitmq", "rocketmq", "spring", "spring boot", "django", "flask", "gin", "fastapi",
    "devops", "ci/cd", "terraform", "ansible", "prometheus", "grafana", "云原生", "service mesh", "可观测性",
    # data / ai
    "机器学习", "深度学习", "nlp", "自然语言处理", "计算机视觉", "cv", "大模型", "llm", "ai", "算法",
    "pytorch", "tensorflow", "spark", "flink", "hadoop", "数仓", "数据仓库", "etl", "数据建模", "ab实验",
    "推荐系统", "风控", "特征工程", "数据治理",
    # product / design / ops
    "需求分析", "产品设计", "原型", "axure", "figma", "用户体验", "交互设计", "项目管理", "pmp",
    "敏捷", "scrum", "okr", "数据分析", "增长", "运营", "商业化", "b端", "c端", "saas",
    # soft / process
    "沟通", "协作", "抗压", "领导力", "团队管理", "跨部门", "复盘", "owner意识", "结果导向",
]


def _load_kb_keywords():
    """Pull every keyword / requirement term from the (large, web-scraped)
    job-requirement knowledge base so JD extraction stays grounded in real
    market data instead of this hand-written seed list alone."""
    try:
        import json as _json
        from pathlib import Path as _Path
        p = _Path(__file__).resolve().parent.parent / "data" / "jd_knowledge.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        kws, seen = [], set()
        for role in data.get("roles", []):
            for fld in ("keywords", "must_have", "nice_to_have"):
                for k in role.get(fld, []):
                    nk = (k or "").lower().strip()
                    if nk and nk not in seen:
                        seen.add(nk)
                        kws.append(nk)
        return kws
    except Exception:
        return []


_KB_KEYWORDS = _load_kb_keywords()
_BASE_LEXICON = LEXICON
LEXICON = _BASE_LEXICON + [k for k in _KB_KEYWORDS if k not in {x.lower() for x in _BASE_LEXICON}]

EMP_TYPES = ["全职", "实习", "兼职", "校招", "社招", "远程"]


def _norm(s: str) -> str:
    return (s or "").lower()


def _kw_hit(kw: str, low: str) -> bool:
    """Boundary-aware keyword match that strongly reduces false positives.

    * CJK keywords: plain substring (CJK has no accidental sub-word problem).
    * ASCII keywords: word-boundary match with optional internal separators
      (``.`` ``-`` ``/`` space) so ``node.js`` matches ``nodejs``/``node js``,
      ``ci/cd`` matches ``cicd``, and a bare ``vi`` will NOT match inside
      ``visual`` / ``service``.
    """
    k = (kw or "").strip().lower()
    if not k:
        return False
    if re.search(r"[\u4e00-\u9fff]", k):
        return k in low
    segs = re.split(r"([.\-/\s]+)", k)
    inner = r"[.\-/\s]*".join(re.escape(s) for s in segs if s)
    if not inner:
        return False
    pat = r"(?<![a-z0-9])" + inner + r"(?![a-z0-9])"
    return re.search(pat, low) is not None


def analyze_jd(job: dict, use_llm: bool = True) -> dict:
    """Extract structured requirements from a JD.

    Offline path: boundary-aware lexicon scan over the JD text (no network, no
    API key). When an LLM is configured, it is *enriched* with a structured
    semantic analysis (must/nice, keywords, responsibilities, seniority, summary)
    that is merged on top of the lexicon hits. Either way the result always has
    the same shape, so the rest of the app is unaffected.
    """
    text = " ".join([job.get("title", ""), job.get("company", ""), job.get("description", "")])
    low = _norm(text)
    requirements = [kw for kw in LEXICON if _kw_hit(kw, low)]
    # add the candidate's own skills that appear in the JD (strong signal)
    prof = db.get_profile()
    for s in (prof.get("skills") or []):
        if _norm(s) in low and s.lower() not in [r.lower() for r in requirements]:
            requirements.append(s)
    emp = [e for e in EMP_TYPES if e in job.get("description", "")]

    result = {
        "requirements": requirements,
        "tokens": _tokenize(text),
        "seniority": _seniority_from_title(job.get("title", "")),
        "employment": emp,
        "raw": text,
        "summary": "",
        "must_have": [],
        "nice_to_have": [],
        "responsibilities": [],
        "source": "lexicon",
    }

    if use_llm:
        enriched = _llm_analyze_jd(job)
        if enriched:
            result["summary"] = enriched.get("summary", "") or ""
            if enriched.get("seniority"):
                result["seniority"] = enriched["seniority"]
            result["must_have"] = enriched.get("must_have", []) or []
            result["nice_to_have"] = enriched.get("nice_to_have", []) or []
            result["responsibilities"] = enriched.get("responsibilities", []) or []
            merged, seen = [], set()
            for k in (enriched.get("keywords", []) + requirements):
                if k and _norm(k) not in seen:
                    merged.append(k)
                    seen.add(_norm(k))
            result["requirements"] = merged[:40]
            result["source"] = "lexicon+llm"
    return result


def _safe_json(text):
    if not text:
        return None
    t = text.strip()
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
    except Exception:
        return None


def _llm_analyze_jd(job: dict) -> Optional[dict]:
    """Optional LLM structured JD analysis. Returns None on any failure."""
    try:
        from . import llm as llm_client
        if not llm_client._resolve_config():
            return None
        user = (
            f"岗位标题：{job.get('title')}\n公司：{job.get('company') or '（未填）'}\n"
            f"岗位描述：\n{(job.get('description') or '（无详细描述）')[:4000]}\n\n请按系统要求输出 JSON。"
        )
        raw = llm_client.chat_simple(prompts.P_SYSTEM_JD_ANALYST, user,
                                     temperature=0.2, max_tokens=1400)
        return _safe_json(raw)
    except Exception:
        return None


def _corpus_text(profile: dict) -> str:
    parts = [profile.get("summary") or "", " ".join(profile.get("skills") or [])]
    rd = profile.get("resume_data") or {}
    for e in rd.get("educations", []):
        parts.append(" ".join([e.get("school", ""), e.get("major", ""), e.get("degree", "")]))
    for x in rd.get("experiences", []):
        parts.append(x.get("title", "") + " " + x.get("company", "") + " " + " ".join(x.get("bullets", [])))
    for p in rd.get("projects", []):
        parts.append(p.get("name", "") + " " + p.get("role", "") + " " + " ".join(p.get("bullets", [])))
    parts += rd.get("certifications", [])
    parts += rd.get("languages", [])
    return " ".join(parts)


def _relevance(item_text: str, jd_tokens: set) -> int:
    return len(_tokenize(item_text) & jd_tokens)


def tailor(profile: dict, job: dict, use_llm: bool = True) -> dict:
    analysis = analyze_jd(job)
    jd_tokens = analysis["tokens"]
    corpus = _norm(_corpus_text(profile))
    skill_set = {_norm(s) for s in (profile.get("skills") or [])}
    rd = profile.get("resume_data") or {}

    # ---- ordered experiences / projects by relevance ----
    exps = list(rd.get("experiences", []))
    for e in exps:
        e["_rel"] = _relevance(" ".join([e.get("title", ""), e.get("company", ""), " ".join(e.get("bullets", []))]), jd_tokens)
    exps_sorted = sorted(exps, key=lambda e: -e["_rel"])

    projs = list(rd.get("projects", []))
    for p in projs:
        p["_rel"] = _relevance(" ".join([p.get("name", ""), p.get("role", ""), " ".join(p.get("bullets", []))]), jd_tokens)
    projs_sorted = sorted(projs, key=lambda p: -p["_rel"])

    # ---- skills ordered: JD-matched first ----
    req_norm = {_norm(r) for r in analysis["requirements"]}
    skills = profile.get("skills") or []
    skills_ordered = sorted(skills, key=lambda s: (0 if _norm(s) in req_norm or _norm(s) in jd_tokens else 1, s))

    # ---- coverage ----
    matched, gaps = [], []
    for r in analysis["requirements"]:
        rl = _norm(r)
        if rl in skill_set or rl in corpus or rl in jd_tokens and rl in corpus:
            matched.append(r)
        else:
            gaps.append(r)
    total = len(analysis["requirements"]) or 1
    coverage = round(len(matched) / total * 100)

    # ---- tailored summary ----
    summary = profile.get("summary") or ""
    top_keys = skills_ordered[:8]
    if use_llm:
        llm_summary = _llm_summary(profile, job, analysis, matched)
        if llm_summary:
            summary_tail = llm_summary
        else:
            summary_tail = _template_summary(profile, job, summary, top_keys, matched)
    else:
        summary_tail = _template_summary(profile, job, summary, top_keys, matched)

    screening = {
        "coverage_pct": coverage,
        "matched": matched,
        "gaps": gaps,
        "requirements": analysis["requirements"],
        "employment": analysis["employment"],
        "seniority": analysis["seniority"],
        "top_keywords": top_keys,
    }

    fill = _fill_suggestions(profile, job, summary_tail, skills_ordered, exps_sorted, projs_sorted, rd)
    html_resume = _render_resume(profile, job, summary_tail, skills_ordered, exps_sorted, projs_sorted, rd)

    return {
        "summary_tailored": summary_tail,
        "skills_ordered": skills_ordered,
        "experiences_ordered": [ {k: v for k, v in e.items() if k != "_rel"} for e in exps_sorted],
        "projects_ordered": [ {k: v for k, v in p.items() if k != "_rel"} for p in projs_sorted],
        "screening": screening,
        "fill": fill,
        "resume_html": html_resume,
    }


def _template_summary(profile, job, base, top_keys, matched) -> str:
    keys_line = "、".join(top_keys[:8]) if top_keys else "相关技术栈"
    return (f"{base} 应聘{job.get('title')}岗位，熟悉{keys_line}。"
            f"具备与岗位高度相关的实战经验，可快速胜任工作要求。")


def _llm_summary(profile, job, analysis, matched) -> Optional[str]:
    try:
        from . import generator, llm as llm_client
        if not generator._llm_available():
            return None
        reqs = "、".join(analysis["requirements"][:12])
        user = (
            f"候选人：{profile.get('name')}，{profile.get('title')}，{profile.get('years_experience')}年经验。\n"
            f"原自我介绍：{profile.get('summary')}\n技能：{', '.join(profile.get('skills') or [])}\n\n"
            f"目标岗位：{job.get('title')} @ {job.get('company')}\n岗位要求关键词：{reqs}\n\n"
            f"请写一段 60-90 字、自然真诚、突出与岗位匹配点的自我介绍（用于简历顶部，面向 ATS 与招聘官）。"
        )
        out = llm_client.chat_simple(prompts.P_SYSTEM_RESUME_TAILOR, user, temperature=0.7)
        return out.strip() if out else None
    except Exception:
        return None


def _fill_suggestions(profile, job, summary, skills_ordered, exps, projs, rd) -> dict:
    exp_lines = []
    for e in exps[:3]:
        bullets = "；".join(e.get("bullets", [])[:3])
        exp_lines.append(f"{e.get('title')} · {e.get('company')}（{e.get('start','')}–{e.get('end','')}）：{bullets}")
    proj_lines = [f"{p.get('name')}：{('；'.join(p.get('bullets', [])[:2]))}" for p in projs[:2]]
    edu = rd.get("educations", [])
    edu_line = "；".join([f"{e.get('school')} {e.get('major')} {e.get('degree')}" for e in edu]) or "（未填写）"
    return {
        "求职意向": job.get("title", ""),
        "期望城市": profile.get("location", ""),
        "自我评价": summary,
        "核心技能": "、".join(skills_ordered[:12]),
        "工作经历": "\n".join(exp_lines) or "（未填写）",
        "项目经历": "\n".join(proj_lines) or "（未填写）",
        "教育背景": edu_line,
    }


def _render_resume(profile, job, summary, skills_ordered, exps, projs, rd) -> str:
    esc = html.escape
    skills_html = "".join(f"<li>{esc(s)}</li>" for s in skills_ordered) or "<li>（未填写技能）</li>"
    exp_html = ""
    for e in exps:
        bullets = "".join(f"<li>{esc(b)}</li>" for b in (e.get("bullets", []) or []))
        exp_html += (f"<div class='exp'><b>{esc(e.get('title',''))}</b> · {esc(e.get('company',''))} "
                     f"<span class='meta'>{esc(e.get('start',''))} – {esc(e.get('end',''))}</span>"
                     f"<ul>{bullets}</ul></div>")
    proj_html = ""
    for p in projs:
        bullets = "".join(f"<li>{esc(b)}</li>" for b in (p.get("bullets", []) or []))
        proj_html += f"<div class='exp'><b>{esc(p.get('name',''))}</b> <span class='meta'>{esc(p.get('role',''))}</span><ul>{bullets}</ul></div>"
    edu_html = ""
    for e in rd.get("educations", []):
        edu_html += f"<div class='exp'>{esc(e.get('school',''))} · {esc(e.get('major',''))} · {esc(e.get('degree',''))} <span class='meta'>{esc(e.get('start',''))}–{esc(e.get('end',''))}</span></div>"
    certs = "".join(f"<li>{esc(c)}</li>" for c in rd.get("certifications", [])) or ""
    links = rd.get("links", {}) or {}
    links_html = " ".join(f"<a href='{esc(v)}'>{esc(k)}</a>" for k, v in links.items() if v)
    contact = " · ".join(filter(None, [esc(profile.get("email") or ""), esc(profile.get("phone") or ""), esc(profile.get("location") or "")]))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,"Segoe UI","PingFang SC",Arial,sans-serif;color:#1a1a1a;max-width:820px;margin:28px auto;padding:0 24px;line-height:1.55}}
 h1{{margin:0;font-size:24px}} .sub{{color:#555;margin:2px 0}} .contact{{color:#666;font-size:13px;margin-bottom:10px}}
 .tag{{display:inline-block;background:#2563eb;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;vertical-align:middle}}
 h2{{font-size:15px;border-bottom:2px solid #2563eb;padding-bottom:4px;margin-top:18px;color:#1e3a8a}}
 .skills{{display:flex;flex-wrap:wrap;gap:7px;list-style:none;padding:0;margin:6px 0}}
 .skills li{{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;border-radius:14px;padding:2px 11px;font-size:13px}}
 .exp{{margin:8px 0}} .meta{{color:#888;font-size:12px}} ul{{margin:4px 0 0;padding-left:18px}} li{{margin:2px 0}}
 .links a{{color:#2563eb;margin-right:12px;font-size:13px}}
</style></head>
<body>
 <h1>{esc(profile.get('name') or '你的名字')}<span class="tag">应聘 {esc(job.get('title',''))}</span></h1>
 <div class="sub">{esc(profile.get('title') or '')} · 期望薪资 {_sal(job)}</div>
 <div class="contact">{contact} {('· '+links_html) if links_html else ''}</div>
 <h2>自我评价</h2><p>{esc(summary)}</p>
 <h2>核心技能（按岗位匹配排序）</h2><ul class="skills">{skills_html}</ul>
 {f'<h2>工作经历</h2>{exp_html}' if exp_html else ''}
 {f'<h2>项目经历</h2>{proj_html}' if proj_html else ''}
 {f'<h2>教育背景</h2>{edu_html}' if edu_html else ''}
 {f'<h2>证书</h2><ul>{certs}</ul>' if certs else ''}
</body></html>"""


def _sal(job):
    sm, sx = job.get("salary_min"), job.get("salary_max")
    if sm and sx:
        return f"¥{sm//1000}k-{sx//1000}k"
    return ""


def tailor_for_job(job_id: int, use_llm: bool = True) -> dict:
    """Convenience: load profile + job and return a tailored resume bundle."""
    profile = db.get_profile()
    job = db.get_job(job_id)
    if not job:
        raise ValueError("job not found")
    return tailor(profile, job, use_llm=use_llm)
