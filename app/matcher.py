"""Job <-> profile matcher.

Produces a 0-100 fit score plus a human-readable list of strengths so the user
can decide what to pursue. Transparent and explainable (no black box).
"""
from __future__ import annotations

import re
from typing import Optional

from . import database as db

WEIGHTS = {
    "skills": 45,      # keyword overlap between profile skills and JD
    "title": 20,       # profile target title appears in job title
    "location": 15,    # location / remote compatibility
    "salary": 15,      # JD salary meets expectation
    "seniority": 5,    # years of experience vs implied seniority
}


def _tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    # keep CJK runs and ascii words
    toks = set(re.findall(r"[\u4e00-\u9fff]+|[a-z0-9%+#.\-]+", text))
    # also keep individual CJK bigrams for fuzzy CJK overlap
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for i in range(len(cjk) - 1):
        toks.add(cjk[i:i + 2])
    return {t for t in toks if len(t) > 1}


def _seniority_from_title(title: str) -> int:
    t = (title or "").lower()
    if any(k in t for k in ["资深", "高级", "senior", "staff", "principal", "专家", "lead", "架构"]):
        return 5
    if any(k in t for k in ["中级", "mid", "engineer"]):
        return 3
    if any(k in t for k in ["初级", "junior", "实习", "intern", "助理"]):
        return 1
    return 2


def score_job(profile: dict, job: dict) -> tuple[float, list[str], list[str]]:
    """Return (score 0-100, strengths, gaps)."""
    skills = [str(s).lower() for s in (profile.get("skills") or [])]
    profile_text = " ".join(skills) + " " + (profile.get("title") or "") + " " + (profile.get("summary") or "")
    jd_text = (job.get("title", "") + " " + job.get("description", "") + " " + job.get("company", ""))

    strengths: list[str] = []
    gaps: list[str] = []

    # --- skills ---
    if skills:
        jd_tokens = _tokenize(jd_text)
        prof_tokens = _tokenize(profile_text)
        hit = jd_tokens & prof_tokens
        ratio = len(hit) / max(len(prof_tokens), 1)
        skills_score = min(1.0, ratio * 1.6) * WEIGHTS["skills"]
        if hit:
            matched = sorted({h for h in hit if any(h in s for s in skills)})[:6]
            strengths.append(f"技能匹配 {len(hit)} 项（如：{', '.join(matched)}）")
        else:
            gaps.append("职位描述中未命中你的核心技能")
    else:
        skills_score = 0.0
        gaps.append("个人资料缺少技能标签，无法评估匹配度")

    # --- title ---
    ptitle = (profile.get("title") or "").lower()
    jtitle = (job.get("title") or "").lower()
    if ptitle and (ptitle in jtitle or jtitle in ptitle or _tokenize(ptitle) & _tokenize(jtitle)):
        title_score = WEIGHTS["title"]
        strengths.append("目标职位与岗位标题高度相关")
    else:
        title_score = WEIGHTS["title"] * 0.3
        gaps.append("岗位标题与目标职位不完全一致")

    # --- location ---
    ploc = (profile.get("location") or "").lower()
    jloc = (job.get("location") or "").lower()
    if not ploc:
        loc_score = WEIGHTS["location"] * 0.5
        gaps.append("个人资料未设置城市偏好")
    elif "远程" in jloc or "remote" in jloc:
        loc_score = WEIGHTS["location"]
        strengths.append("支持远程，地点无限制")
    elif ploc in jloc or jloc in ploc:
        loc_score = WEIGHTS["location"]
        strengths.append(f"工作地点匹配：{job.get('location')}")
    else:
        loc_score = WEIGHTS["location"] * 0.2
        gaps.append(f"地点可能不符：期望 {profile.get('location')}，岗位 {job.get('location')}")

    # --- salary ---
    sm, sx = job.get("salary_min"), job.get("salary_max")
    exp_min, exp_max = profile.get("salary_min"), profile.get("salary_max")
    if sm and exp_min:
        if sm >= exp_min:
            salary_score = WEIGHTS["salary"]
            strengths.append("薪资达到或超过期望下限")
        elif sm >= exp_min * 0.8:
            salary_score = WEIGHTS["salary"] * 0.6
            gaps.append("薪资略低于期望下限")
        else:
            salary_score = WEIGHTS["salary"] * 0.2
            gaps.append("薪资明显低于期望")
    else:
        salary_score = WEIGHTS["salary"] * 0.5
        if not sm:
            gaps.append("职位未提供薪资信息")

    # --- seniority ---
    p_years = int(profile.get("years_experience") or 0)
    need = _seniority_from_title(job.get("title", ""))
    if p_years >= need:
        sen_score = WEIGHTS["seniority"]
    elif p_years >= need - 2:
        sen_score = WEIGHTS["seniority"] * 0.6
    else:
        sen_score = WEIGHTS["seniority"] * 0.2
        gaps.append("年限可能不足该岗位级别")

    score = skills_score + title_score + loc_score + salary_score + sen_score
    return round(score, 1), strengths, gaps


def retailor_existing(statuses: tuple[str, ...] = ("drafted", "approved")) -> dict:
    """Re-run scoring + tailoring on applications already in the pipeline.

    Needed because match_and_store only touches jobs with no application or a
    'new' one — so when the user *improves their profile*, the drafts they have
    already generated would keep a stale, low-coverage resume. This keeps every
    non-sent draft in sync with the current profile.
    """
    profile = db.get_profile()
    conn = db.get_conn()
    placeholders = ",".join("?" * len(statuses))
    apps = db._rows(
        conn,
        f"SELECT id, job_id, status FROM applications WHERE status IN ({placeholders})",
        tuple(statuses),
    )
    refreshed = 0
    for a in apps:
        job = db.get_job(a["job_id"])
        if not job:
            continue
        from . import generator, tailor as _tailor
        score, strengths, gaps = score_job(profile, job)
        cl = generator.make_cover_letter(profile, job, strengths)
        tb = _tailor.tailor(profile, job, use_llm=False)
        db.upsert_application(a["job_id"], {
            "match_score": score,
            "strengths": "; ".join(strengths),
            "cover_letter": cl,
            "email_subject": generator.make_subject(profile, job),
            "resume_variant": tb["resume_html"],
            "screening_report": tb["screening"],
        }, status=a["status"])
        refreshed += 1
    return {"refreshed": refreshed}


def match_and_store(min_score: float = 60, auto_draft: bool = False,
                    use_llm: bool | None = None) -> dict:
    """Score candidate jobs and store matches.

    A "candidate" is any job that has not yet been actively pursued: either it has
    no application row yet, or its application is still in the 'new' state (e.g. it
    scored below threshold on a previous run, or your profile changed). Jobs you
    have already drafted/approved/sent/rejected are left untouched.

    ``use_llm`` controls content generation for drafts: True/False force it,
    None (default) uses the LLM only if one is configured (so a single click
    produces model-written cover letters + tailored resumes when available).
    """
    from . import generator, tailor
    profile = db.get_profile()
    if use_llm is None:
        use_llm = generator._llm_available() is not None
    conn = db.get_conn()
    jobs = db._rows(
        conn,
        """SELECT j.* FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE a.id IS NULL OR a.status = 'new'
           ORDER BY j.created_at DESC LIMIT 2000""",
    )
    results = []
    skipped_blacklist = 0
    for job in jobs:
        # skip companies on the blacklist (auto-accumulated from 已读不回/不匹配/拒绝)
        company = job.get("company", "")
        if company and db.is_company_blacklisted(company):
            skipped_blacklist += 1
            continue
        score, strengths, gaps = score_job(profile, job)
        status = "matched" if score >= min_score else "new"
        data = {"match_score": score, "strengths": "; ".join(strengths)}
        if status == "matched" and auto_draft:
            subject, cl = generator.generate(profile, job, strengths, use_llm=use_llm)
            data["cover_letter"] = cl
            data["email_subject"] = subject
            tb = tailor.tailor(profile, job, use_llm=use_llm)
            data["resume_variant"] = tb["resume_html"]
            data["screening_report"] = tb["screening"]
            status = "drafted"
        app = db.upsert_application(job["id"], data, status=status)
        results.append({"job_id": job["id"], "title": job["title"],
                        "score": score, "status": status})
    return {"evaluated": len(results), "matched": sum(1 for r in results if r["status"] in ("matched", "drafted")),
            "skipped_blacklist": skipped_blacklist,
            "results": results}
