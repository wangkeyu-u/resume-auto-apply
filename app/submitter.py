"""Submission module.

DESIGN PRINCIPLE
----------------
Actual delivery to a *third-party job board* (Boss直聘 / 智联 / LinkedIn / ...)
by unattended automation routinely violates those sites' Terms of Service and
risks account bans. So the system ships with two compliant delivery paths:

  1. EMAIL outreach (SMTP) — fully automated, but gated behind a human-approved
     queue (you click "批准并发送"). This is the recommended, safe path.
  2. SITE connector TEMPLATE — a clearly-marked stub you can extend for your OWN
     accounts, with manual confirmation. It is intentionally NOT implemented as
     a ToS-evading bot. See `apply_via_site` for the contract.

Nothing here bypasses captchas or anti-bot measures.
"""
from __future__ import annotations

import email.encoders
import mimetypes
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Optional

from . import database as db
from .profile import ensure_resume


def _sent_in_last_hour() -> int:
    rows = db._rows(db.get_conn(),
                    "SELECT sent_at FROM applications WHERE status='sent' AND sent_at IS NOT NULL")
    now = datetime.now(timezone.utc)
    n = 0
    for r in rows:
        try:
            t = datetime.fromisoformat(r["sent_at"])
            if (now - t).total_seconds() < 3600:
                n += 1
        except ValueError:
            continue
    return n


def count_sent_today() -> int:
    """How many applications were sent since local midnight (for daily cap)."""
    rows = db._rows(db.get_conn(),
                    "SELECT sent_at FROM applications WHERE status='sent' AND sent_at IS NOT NULL")
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    n = 0
    for r in rows:
        try:
            t = datetime.fromisoformat(r["sent_at"])
            if t >= today_start:
                n += 1
        except ValueError:
            continue
    return n


def _build_message(smtp_cfg: dict, profile: dict, app: dict, resume_html: str) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = app.get("email_subject") or "求职申请"
    msg["From"] = f"{smtp_cfg.get('from_name') or profile.get('name')} <{smtp_cfg.get('from_address') or smtp_cfg.get('username')}>"
    msg["To"] = app.get("email_to") or ""
    msg["Reply-To"] = profile.get("email") or smtp_cfg.get("from_address") or ""

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(app.get("cover_letter") or "", "plain", "utf-8"))
    msg.attach(alt)

    # attach resume as an .html file
    part = MIMEApplication(resume_html.encode("utf-8"), Name="resume.html")
    part["Content-Disposition"] = 'attachment; filename="resume.html"'
    msg.attach(part)
    return msg


def send_email(app_id: int, force: bool = False) -> dict:
    """Send one approved application by email. Returns a result dict."""
    settings = db.get_settings()
    smtp = settings.get("smtp", {})
    safety = settings.get("safety", {})
    app = db.get_application(app_id)
    if not app:
        return {"ok": False, "error": "申请不存在"}

    # Safety gate FIRST: never send without explicit approval (unless forced).
    if safety.get("require_human_approval", True) and not force and app.get("status") != "approved":
        return {"ok": False, "error": "该申请尚未「批准」，无法发送（安全策略要求人工确认）。"}

    if not smtp.get("enabled"):
        return {"ok": False, "error": "SMTP 未启用，请在「设置」中配置邮箱。"}

    max_per_hour = int(safety.get("max_send_per_hour", 10))
    if _sent_in_last_hour() >= max_per_hour:
        return {"ok": False, "error": f"已达到每小时发送上限（{max_per_hour}），请稍后再试。"}

    profile = db.get_profile()
    resume_html = ensure_resume(profile)
    try:
        msg = _build_message(smtp, profile, app, resume_html)
        context = ssl.create_default_context()
        if smtp.get("use_tls"):
            with smtplib.SMTP_SSL(smtp["host"], int(smtp["port"]), context=context) as s:
                s.login(smtp["username"], smtp["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp["host"], int(smtp["port"])) as s:
                s.starttls(context=context)
                s.login(smtp["username"], smtp["password"])
                s.send_message(msg)
        db.update_application_status(app_id, "sent", {"sent_at": datetime.now(timezone.utc).isoformat()})
        return {"ok": True, "to": app.get("email_to")}
    except Exception as e:
        db.update_application_status(app_id, "sent_failed", {"notes": str(e)})
        return {"ok": False, "error": f"发送失败：{e}"}


def send_batch(app_ids: list[int]) -> list[dict]:
    results = []
    for aid in app_ids:
        results.append({"id": aid, **send_email(aid)})
    return results


def apply_via_site(job: dict, profile: dict) -> dict:
    """SITE CONNECTOR STUB — extend for your own accounts with manual confirmation.

    This is intentionally NOT a black-box auto-apply bot. To wire it up safely:
      1. Only automate platforms whose ToS permit it, or your own employer portal.
      2. Require the user to log in manually (store NO passwords in plaintext).
      3. Keep a human confirmation step before each submission.
      4. Respect robots.txt and rate limits (see settings.safety).

    Return {"ok": False, "error": "not implemented"} until you provide a
    compliant implementation. Do not add captcha-bypass / anti-bot evasion.
    """
    return {
        "ok": False,
        "error": "站点自动投递未实现（按设计保留为需人工确认的扩展点）。"
                 "推荐先使用「邮件投递」通道，或在 settings 中按要求扩展 apply_via_site。",
    }
