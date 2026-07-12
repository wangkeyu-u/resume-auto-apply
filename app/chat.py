"""Chat-assist engine for recruiter conversations (BOSS 直聘 / LinkedIn / generic).

Design principle (same as the rest of this tool):
  * The *thinking* is fully automatic — given an inbound recruiter message we draft
    a context-aware, job-tailored reply using the candidate's tailored resume.
  * The *sending* stays human-in-the-loop. There is no stealth, no captcha solving,
    no 24/7 impersonation. Messages are typed into the platform by a local browser
    extension the user runs on their OWN account, and the user confirms each send.
    BOSS 直聘 has no public API; the only honest integration is a user-operated
    bridge (see connector/boss-zhipin-extension).

Reply generation is offline (template) by default, with an optional OpenAI-compatible
LLM for more natural phrasing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from . import database as db
from . import tailor
from . import generator, prompts

# --------------------------------------------------------------------------- #
# intent detection
# --------------------------------------------------------------------------- #
INTENT_PATTERNS = {
    "salary": ["薪资", "待遇", "工资", "薪酬", "多少钱", "月薪", "k ", "k/月", "预算", "报价"],
    "experience": ["经验", "几年", "做过", "负责", "项目背景", "技术栈", "熟不熟", "会不会", "能不能做", "上家", "之前"],
    "resume": ["简历", "发简历", "附件", "资料", "详细介绍", "作品", "pdf"],
    "availability": ["到岗", "入职", "离职", "什么时候", "能来", "到岗时间", "几月", "空窗"],
    "education": ["学历", "学校", "毕业", "专业", "统招", "本科", "硕士", "学历要求"],
    "interview": ["面试", "电话", "视频", "约个", "聊一下", "进一步", "什么时候方便"],
    "greeting": ["你好", "在吗", "在么", "hi", "hello", "方便", "介绍一下", "自我介绍", "聊聊"],
}
INTENT_ORDER = ["salary", "experience", "resume", "availability", "education", "interview", "greeting"]


def detect_intent(text: str) -> str:
    low = (text or "").lower()
    # 1) specific functional intents take priority
    for intent in ["salary", "experience", "availability", "education", "interview"]:
        if any(k in low for k in INTENT_PATTERNS[intent]):
            return intent
    # 2) clear conversation-openers (even if they mention 简历) -> greeting pitch
    if any(o in low for o in ["你好", "在吗", "在么", "hi ", "hello"]) and \
       ("聊聊" in low or "方便" in low or "介绍" in low or len(text) < 30):
        return "greeting"
    # 3) explicit resume / attachment request
    if any(k in low for k in INTENT_PATTERNS["resume"]):
        return "resume"
    # 4) generic greeting
    if any(k in low for k in INTENT_PATTERNS["greeting"]):
        return "greeting"
    return "default"


# --------------------------------------------------------------------------- #
# context builder
# --------------------------------------------------------------------------- #
def _highlights(conv: dict) -> dict:
    """Pull tailored highlights for this conversation's job (if linked)."""
    job_id = conv.get("job_id")
    if not job_id:
        return {}
    try:
        tb = tailor.tailor_for_job(job_id, use_llm=False)
    except Exception:
        return {}
    scr = tb.get("screening", {})
    exps = tb.get("experiences_ordered", []) or []
    top_exp = exps[0] if exps else {}
    return {
        "job_title": (db.get_job(job_id) or {}).get("title", ""),
        "company": (db.get_job(job_id) or {}).get("company", ""),
        "top_skills": tb.get("skills_ordered", [])[:4],
        "top_keywords": scr.get("top_keywords", [])[:8],
        "coverage": scr.get("coverage_pct", 0),
        "exp_company": top_exp.get("company", ""),
        "exp_title": top_exp.get("title", ""),
        "exp_bullet": (top_exp.get("bullets", []) or [""])[0],
    }


# --------------------------------------------------------------------------- #
# reply templates (offline, BOSS-style: short & conversational)
# --------------------------------------------------------------------------- #
def _template_reply(intent: str, profile: dict, conv: dict, hl: dict, inbound: str) -> str:
    name = profile.get("name") or "我"
    years = profile.get("years_experience") or 0
    title = profile.get("title") or "工程师"
    skills = "、".join(hl.get("top_skills", []) or profile.get("skills", [])[:3]) or "相关技术栈"
    job_title = hl.get("job_title") or conv.get("job_title") or "这个"
    exp_company = hl.get("exp_company", "")
    exp_bullet = hl.get("exp_bullet", "")

    if intent == "greeting":
        lead = f"您好，我是{name}，{years}年{title}经验，对贵司「{job_title}」岗位很感兴趣。"
        if exp_company:
            lead += f"我熟悉{skills}，之前在{exp_company}负责{exp_bullet}。"
        lead += "方便的话想和您进一步沟通，谢谢！"
        return lead

    if intent == "experience":
        s = f"我有{years}年{title}经验，主要方向是{skills}。"
        if exp_company:
            s += f"最近在{exp_company}负责{exp_bullet}。"
        cov = hl.get("coverage", 0)
        if cov:
            s += f"整体和咱们岗位要求契合度挺高的（约{cov}% 匹配）。"
        return s

    if intent == "salary":
        sm, sx = profile.get("salary_min"), profile.get("salary_max")
        if sm and sx:
            return (f"我期望薪资大概在 {sm//1000}k–{sx//1000}k/月，"
                    f"具体可以结合岗位职责和团队情况再聊，比较灵活。")
        if sm:
            return f"我期望薪资 {sm//1000}k 以上/月，具体好商量。"
        return "薪资这块比较好聊，主要看岗位匹配度和团队，您这边预算范围方便说一下吗？"

    if intent == "resume":
        cov = hl.get("coverage", 0)
        s = "好的，简历稍后发您～ 先补充一下："
        s += f"我{years}年经验，核心技能是{skills}。"
        if cov:
            s += f"和岗位匹配度约 {cov}%。"
        s += "如果有更具体的业务背景您也可以直接告诉我，我针对性介绍。"
        return s

    if intent == "availability":
        return ("我目前可以尽快到岗（约 2–4 周内），具体时间可以协商，"
                "不影响入职节奏。")

    if intent == "education":
        rd = profile.get("resume_data", {}) or {}
        edus = rd.get("educations", []) or []
        if edus:
            e = edus[0]
            return f"我是{e.get('school','')}{e.get('major','')}{e.get('degree','')}毕业的。"
        return f"我的学历背景在简历里有详细写明，稍后发您完整版～"

    if intent == "interview":
        return ("可以的，您定个时间就行，电话或视频都方便。"
                "我这边随时可以配合，谢谢您给的机会！")

    # default: polite pitch + next step
    s = f"您好，我是{name}，{years}年{title}经验，对「{job_title}」很感兴趣。"
    if exp_company:
        s += f"我熟悉{skills}，之前在{exp_company}做过相关方向。"
    s += "方便的话想多了解下团队和业务，期待进一步沟通～"
    return s


def _llm_reply(intent: str, profile: dict, conv: dict, hl: dict, inbound: str) -> Optional[str]:
    try:
        if not generator._llm_available():
            return None
        from . import llm as llm_client
        job_title = hl.get("job_title") or conv.get("job_title") or "该岗位"
        ctx = (
            f"你是求职者{profile.get('name')}，{profile.get('title')}，{profile.get('years_experience')}年经验。\n"
            f"应聘岗位：{job_title} @ {hl.get('company') or conv.get('company')}\n"
            f"你的核心技能：{', '.join(hl.get('top_skills', []) or profile.get('skills', [])[:6])}\n"
            f"你的经历亮点：{hl.get('exp_company','')} — {hl.get('exp_bullet','')}\n"
            f"岗位匹配度：{hl.get('coverage',0)}%\n\n"
            f"招聘方刚发来消息：「{inbound}」\n"
            f"请拟一条中文回复（BOSS直聘风格：简短、口语化、真诚，2–4 句，不要寒暄过多）。"
            f"意图疑似：{intent}。只输出回复正文。"
        )
        out = llm_client.chat_simple(prompts.P_SYSTEM_CHAT_REPLIER, ctx, temperature=0.6)
        if out:
            return out.strip().strip('"')
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def generate_reply(conv: dict, inbound_text: str, use_llm: bool = True) -> dict:
    """Return a drafted reply dict for an inbound recruiter message."""
    profile = db.get_profile()
    hl = _highlights(conv)
    intent = detect_intent(inbound_text)

    text = None
    if use_llm:
        text = _llm_reply(intent, profile, conv, hl, inbound_text)
    if not text:
        text = _template_reply(intent, profile, conv, hl, inbound_text)

    # confidence: higher when we have a linked job + decent coverage
    confidence = 0.6
    if conv.get("job_id"):
        confidence = 0.8
        if hl.get("coverage", 0) >= 80:
            confidence = 0.95
    return {
        "text": text,
        "intent": intent,
        "confidence": confidence,
        "highlights": hl,
    }


def auto_approve_allowed(settings: dict) -> bool:
    chat_cfg = settings.get("chat", {})
    return bool(chat_cfg.get("assisted_auto_reply", False))


# --------------------------------------------------------------------------- #
# follow-up (已读不回提醒 + 复聊话术)
# --------------------------------------------------------------------------- #
FOLLOWUP_DAYS = 3


def conversations_needing_followup(days: int = FOLLOWUP_DAYS) -> list[dict]:
    """Conversations where the HR's last message is unanswered by us for >= `days`.

    This is the '已读不回' nudge: the recruiter wrote something, we haven't replied
    since, and enough time has passed that a polite follow-up is appropriate.
    """
    out = []
    for conv in db.list_conversations(status="active"):
        msgs = db.list_messages(conv["id"])
        if not msgs:
            continue
        last = msgs[-1]
        if last["direction"] != "inbound":
            continue  # we replied most recently -> not waiting on us
        has_out_after = any(m["direction"] == "outbound" and m["id"] > last["id"] for m in msgs)
        if has_out_after:
            continue
        try:
            t = datetime.fromisoformat(last["created_at"])
            age_days = (datetime.now(timezone.utc) - t).days
        except Exception:
            age_days = 999
        if age_days >= days:
            out.append({**conv, "last_inbound_at": last["created_at"],
                        "age_days": age_days, "last_inbound": last["text"]})
    return out


def _llm_followup(profile: dict, conv: dict, hl: dict) -> Optional[str]:
    try:
        if not generator._llm_available():
            return None
        from . import llm as llm_client
        job_title = hl.get("job_title") or conv.get("job_title") or "该岗位"
        ctx = (
            f"你是求职者{profile.get('name')}，之前和招聘方在 BOSS 直聘沟通过「{job_title}」岗位，"
            f"但对方最近一条消息后你还没回复（已过去几天）。请拟一条礼貌的跟进/复聊消息（中文、口语化、"
            f"2–3 句、不纠缠、留好退路）：既回应上次的话题，又表达持续兴趣，并委婉询问进展。只输出正文。"
        )
        out = llm_client.chat_simple(prompts.P_SYSTEM_CHAT_FOLLOWUP, ctx, temperature=0.6)
        if out:
            return out.strip().strip('"')
        return None
    except Exception:
        return None


def generate_followup(conv: dict, use_llm: bool = True) -> dict:
    """Draft a polite follow-up for an unanswered recruiter message."""
    profile = db.get_profile()
    hl = _highlights(conv)
    name = profile.get("name") or "我"
    job_title = hl.get("job_title") or conv.get("job_title") or "这个"
    company = hl.get("company") or conv.get("company") or ""
    text = None
    if use_llm:
        text = _llm_followup(profile, conv, hl)
    if not text:
        text = (
            f"您好{name}，之前和您聊过「{job_title}」"
            + (f" @ {company}" if company else "")
            + "岗位，想跟进一下进展～如果职位还在招、我也仍然很感兴趣，方便的话希望能进一步沟通，谢谢！"
        )
    return {"text": text, "intent": "followup", "highlights": hl}
