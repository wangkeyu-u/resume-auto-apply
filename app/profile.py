"""Profile helpers: completeness scoring and default resume rendering."""
from __future__ import annotations

import html
from typing import Optional

from . import database as db


def completeness(profile: Optional[dict] = None) -> dict:
    """Return a 0-100 completeness score plus missing fields."""
    p = profile or db.get_profile()
    rd = p.get("resume_data") or {}
    checks = {
        "name": bool(p.get("name")),
        "title": bool(p.get("title")),
        "email": bool(p.get("email")),
        "phone": bool(p.get("phone")),
        "location": bool(p.get("location")),
        "summary": bool((p.get("summary") or "").strip()),
        "skills": bool(p.get("skills")),
        "years_experience": (p.get("years_experience") or 0) > 0,
        "resume_data": bool(rd.get("educations") or rd.get("experiences")),
    }
    done = sum(1 for v in checks.values() if v)
    score = int(done / len(checks) * 100)
    missing = [k for k, v in checks.items() if not v]
    return {"score": score, "missing": missing, "checks": checks}


def default_resume_html(profile: Optional[dict] = None) -> str:
    """Render a clean, printable resume HTML from the profile + structured data."""
    p = profile or db.get_profile()
    esc = html.escape
    rd = p.get("resume_data") or {}
    skills = p.get("skills") or []
    skills_html = "".join(f"<li>{esc(s)}</li>" for s in skills) or "<li>（未填写技能）</li>"
    exp_html = ""
    for e in rd.get("experiences", []):
        bullets = "".join(f"<li>{esc(b)}</li>" for b in (e.get("bullets", []) or []))
        exp_html += f"<div><b>{esc(e.get('title',''))}</b> · {esc(e.get('company',''))} <span style='color:#888;font-size:12px'>{esc(e.get('start',''))}–{esc(e.get('end',''))}</span><ul>{bullets}</ul></div>"
    edu_html = ""
    for e in rd.get("educations", []):
        edu_html += f"<div>{esc(e.get('school',''))} · {esc(e.get('major',''))} · {esc(e.get('degree',''))} <span style='color:#888;font-size:12px'>{esc(e.get('start',''))}–{esc(e.get('end',''))}</span></div>"
    salary = ""
    sm, sx = p.get("salary_min"), p.get("salary_max")
    if sm and sx:
        salary = f"期望薪资：¥{sm:,} - ¥{sx:,} / 月"
    elif sm:
        salary = f"期望薪资：¥{sm:,}+ / 月"
    contact = " · ".join(filter(None, [
        esc(p.get("email") or ""), esc(p.get("phone") or ""), esc(p.get("location") or ""),
    ]))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,"Segoe UI",Arial,sans-serif;color:#1a1a1a;max-width:820px;margin:32px auto;padding:0 24px;line-height:1.6}}
 h1{{margin:0;font-size:26px}} .sub{{color:#555;margin:4px 0 2px}} .contact{{color:#666;font-size:13px;margin-bottom:18px}}
 h2{{font-size:16px;border-bottom:2px solid #2563eb;padding-bottom:4px;margin-top:22px;color:#1e3a8a}}
 .skills{{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:8px 0}}
 .skills li{{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;border-radius:14px;padding:3px 12px;font-size:13px}}
 .summary{{margin:8px 0}} ul{{margin:4px 0;padding-left:18px}}
</style></head>
<body>
 <h1>{esc(p.get('name') or '你的名字')}</h1>
 <div class="sub">{esc(p.get('title') or '目标职位')}{(' · ' + salary) if salary else ''}</div>
 <div class="contact">{contact}</div>
 <h2>个人简介</h2>
 <p class="summary">{esc(p.get('summary') or '（请在「个人资料」中填写一段个人简介）')}</p>
 <h2>技能标签</h2>
 <ul class="skills">{skills_html}</ul>
 {('<h2>工作经历</h2>' + exp_html) if exp_html else ''}
 {('<h2>教育背景</h2>' + edu_html) if edu_html else ''}
</body></html>"""


def ensure_resume(profile: Optional[dict] = None) -> str:
    """Return stored resume_html or generate a default one."""
    p = profile or db.get_profile()
    return p.get("resume_html") or default_resume_html(p)
