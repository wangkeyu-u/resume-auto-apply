"""Cover-letter / resume-variant generator.

Default: transparent template rendering (no external calls, fully offline).
Optional: a provider LLM (via app.llm) for richer, personalised drafts when
enabled in settings. The call is best-effort and falls back to the template.
"""
from __future__ import annotations

from . import database as db, llm as llm_client, prompts

SYSTEM_PROMPT = prompts.P_SYSTEM_COVER_LETTER


def make_subject(profile: dict, job: dict) -> str:
    name = profile.get("name") or "候选人"
    title = job.get("title") or "相关岗位"
    return f"应聘{title} - {name}"


def make_cover_letter(profile: dict, job: dict, strengths: list[str] | None = None) -> str:
    name = profile.get("name") or "（你的名字）"
    title = profile.get("title") or "（你的目标职位）"
    jtitle = job.get("title") or "该岗位"
    company = job.get("company") or "贵公司"
    summary = profile.get("summary") or "我专注于高质量交付与持续学习。"
    skills = profile.get("skills") or []
    skill_line = "、".join(skills[:8]) if skills else "相关技术栈"
    matched = "；".join(strengths or [])
    matched_line = f"我与岗位的匹配点包括：{matched}。" if matched else ""

    return (
        f"尊敬的人力资源负责人：\n\n"
        f"您好！我是{name}，一名{title}。看到{company}正在招聘{jtitle}，"
        f"我认为自己的背景与该岗位高度契合，特此投递简历。\n\n"
        f"{summary}\n\n"
        f"我的核心技能包括：{skill_line}。{matched_line}\n\n"
        f"我希望能有机会进一步沟通，详细介绍我如何为{company}的{jtitle}岗位创造价值。"
        f"我的简历附后，期待您的回复。\n\n"
        f"此致\n敬礼\n{name}\n{profile.get('phone') or ''}  {profile.get('email') or ''}"
    )


def _llm_available(settings: dict | None = None) -> dict | None:
    """Back-compat shim: returns a truthy config dict when an LLM is usable."""
    s = settings or db.get_settings()
    llm = (s.get("llm") or {}) if s else {}
    if llm.get("enabled") and llm.get("api_key"):
        return llm
    return None


def generate_via_llm(profile: dict, job: dict, strengths: list[str] | None = None) -> str | None:
    """Best-effort LLM generation; returns None on any failure (caller falls back)."""
    if not _llm_available():
        return None
    jd = (job.get("title", "") + "\n" + (job.get("description") or ""))
    user = (
        f"候选人资料：\n姓名：{profile.get('name')}\n目标职位：{profile.get('title')}\n"
        f"城市：{profile.get('location')}\n年限：{profile.get('years_experience')}年\n"
        f"技能：{', '.join(profile.get('skills') or [])}\n简介：{profile.get('summary')}\n\n"
        f"目标岗位：{job.get('title')} @ {job.get('company')}（{job.get('location')}）\n"
        f"岗位描述：\n{jd}\n\n请撰写一封中文求职信。"
    )
    return llm_client.chat_simple(SYSTEM_PROMPT, user, temperature=0.7)


def generate(profile: dict, job: dict, strengths: list[str] | None = None,
             use_llm: bool = True) -> tuple[str, str]:
    """Return (subject, body). Uses LLM when available & requested, else template."""
    subject = make_subject(profile, job)
    body = None
    if use_llm:
        body = generate_via_llm(profile, job, strengths)
    if not body:
        body = make_cover_letter(profile, job, strengths)
    return subject, body
