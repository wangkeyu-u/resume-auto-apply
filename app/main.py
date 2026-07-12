"""FastAPI app: serves the dashboard and exposes the automation API."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import database as db
from . import aggregator
from . import matcher
from . import generator
from . import submitter
from . import tailor
from . import profile as profile_mod
from . import chat
from . import llm as llm_client
from . import optimizer
from .scheduler import scheduler

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"

app = FastAPI(title="简历自动投递助手", version="1.0.0")

app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    db.init_db_settings()
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((FRONTEND / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    s = db.get_settings()
    llm = s.get("llm") or {}
    ap = s.get("autopilot") or {}
    return {"ok": True, "jobs": db.job_count(),
            "status_counts": db.application_count_by_status(),
            "llm": {"enabled": bool(llm.get("enabled") and llm.get("api_key")),
                    "provider": llm.get("provider"), "model": llm.get("model")},
            "autopilot": {"enabled": bool(ap.get("enabled")),
                          "auto_send": bool(ap.get("auto_send"))}}


# ----------------------------- profile --------------------------------------
@app.get("/api/profile")
def get_profile() -> dict:
    return db.get_profile()


@app.post("/api/profile")
async def post_profile(request: Request) -> dict:
    data = await request.json()
    db.upsert_profile(data)
    # keep already-drafted applications in sync with the improved profile
    try:
        from . import matcher as _matcher
        _matcher.retailor_existing()
    except Exception:
        pass
    return db.get_profile()


@app.get("/api/profile/completeness")
def profile_completeness() -> dict:
    return profile_mod.completeness()


@app.get("/api/profile/resume")
def profile_resume() -> HTMLResponse:
    return HTMLResponse(profile_mod.ensure_resume())


@app.post("/api/profile/parse")
async def parse_profile(request: Request) -> dict:
    """Paste resume text -> structured profile (LLM extract, regex fallback)."""
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "简历文本为空"}
    from . import profile_parse
    result = profile_parse.parse_resume(text)
    if "error" in result:
        return {"ok": False, "error": result["error"]}
    return {"ok": True, "profile": result, "source": "llm" if profile_parse.llm_client._resolve_config() else "heuristic"}


# ------------------------------- jobs ----------------------------------------
@app.get("/api/jobs")
def list_jobs(only_unapplied: bool = False, limit: int = 200) -> dict:
    return {"jobs": db.list_jobs(limit=limit, only_unapplied=only_unapplied)}


@app.post("/api/jobs/refresh")
def refresh_jobs() -> dict:
    return aggregator.run_all(db.get_settings())


@app.post("/api/jobs/import")
async def import_job(request: Request) -> dict:
    job = await request.json()
    jid = db.insert_job(job)
    if jid is None:
        return {"ok": False, "error": "该职位已存在（source+source_id 重复）"}
    return {"ok": True, "id": jid}


@app.post("/api/jobs/import-bulk")
async def import_jobs_bulk(request: Request) -> dict:
    """Receive jobs scraped by the browser extension from a real job board page.
    Auto-matches right after import so the dashboard is populated with zero manual work.
    """
    body = await request.json()
    jobs = body.get("jobs", [])
    auto_match = bool(body.get("auto_match", True))
    imported = skipped = 0
    for j in jobs:
        url = (j.get("url") or "").strip()
        source_id = j.get("source_id") or url or f"ext-{abs(hash((j.get('title',''), j.get('company',''))))}"
        jid = db.insert_job({
            "source": "extension",
            "source_id": source_id,
            "title": (j.get("title") or "").strip(),
            "company": (j.get("company") or "").strip(),
            "location": (j.get("location") or "").strip(),
            "url": url,
            "description": j.get("description", ""),
            "salary_text": j.get("salary_text", ""),
            "raw": json.dumps(j, ensure_ascii=False),
        })
        if jid is None:
            skipped += 1
        else:
            imported += 1
    if auto_match and imported:
        settings = db.get_settings()
        sch = settings.get("schedule", {})
        res = matcher.match_and_store(
            min_score=float(sch.get("min_match_score", 60)),
            auto_draft=bool(sch.get("auto_draft", True)),
        )
    else:
        res = {"evaluated": imported, "matched": 0}
    return {"ok": True, "imported": imported, "skipped": skipped, "matched": res.get("matched", 0)}


@app.get("/api/jobs/by-url")
def job_by_url(url: str) -> dict:
    job = db.find_job_by_url(url)
    if not job:
        return {"found": False}
    return {"found": True, "job_id": job["id"], "title": job["title"], "company": job["company"]}


@app.get("/api/jobs/find")
def job_find(title: str = "", company: str = "") -> dict:
    """Extension: resolve a job when the apply-page url differs from the listing url."""
    job = db.find_job_by_text(title, company)
    if not job:
        return {"found": False}
    return {"found": True, "job_id": job["id"], "title": job["title"], "company": job["company"]}


@app.get("/api/jobs/{job_id}/fill")
def job_fill(job_id: int) -> dict:
    """Extension: job-specific field values to auto-fill an application form."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    tb = tailor.tailor_for_job(job_id, use_llm=False)
    return tb["fill"]


@app.get("/api/config/export")
def config_export() -> dict:
    """Extension bootstrap: everything the popup needs, no manual setup."""
    settings = db.get_settings()
    ext = settings.get("extension", {}) or {}
    return {
        "api_base": "http://127.0.0.1:8011",
        "api_token": ext.get("api_token", ""),
        "version": app.version,
        "auto_tailor": (settings.get("schedule", {}) or {}).get("auto_draft", True),
    }


# ------------------------------ matching -------------------------------------
@app.post("/api/match")
async def run_match(request: Request = None) -> dict:
    body = await request.json() if request else {}
    settings = db.get_settings()
    sch = settings.get("schedule", {})
    return matcher.match_and_store(
        min_score=float(body.get("min_score", sch.get("min_match_score", 60))),
        auto_draft=bool(body.get("auto_draft", sch.get("auto_draft", True))),
    )


# --------------------------- applications ------------------------------------
@app.get("/api/applications")
def list_apps(status: str | None = None) -> dict:
    return {"applications": db.list_applications(status=status)}


@app.get("/api/applications/{app_id}")
def get_app(app_id: int) -> dict:
    app = db.get_application(app_id)
    if not app:
        raise HTTPException(404, "not found")
    return app


@app.post("/api/applications/{job_id}/draft")
async def draft_application(job_id: int, request: Request) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    body = await request.json()
    use_llm = bool(body.get("use_llm", True))
    profile = db.get_profile()
    score, strengths, gaps = matcher.score_job(profile, job)
    subject, body_text = generator.generate(profile, job, strengths, use_llm=use_llm)
    tb = tailor.tailor(profile, job, use_llm=use_llm)
    app = db.upsert_application(job_id, {
        "match_score": score,
        "strengths": "; ".join(strengths),
        "cover_letter": body_text,
        "email_subject": subject,
        "resume_variant": tb["resume_html"],
        "screening_report": tb["screening"],
        "email_to": body.get("email_to", ""),
    }, status="drafted")
    return app


@app.get("/api/applications/{app_id}/resume")
def application_resume(app_id: int) -> HTMLResponse:
    app = db.get_application(app_id)
    if not app:
        raise HTTPException(404, "not found")
    if app.get("resume_variant"):
        return HTMLResponse(app["resume_variant"])
    # generate on the fly if missing
    tb = tailor.tailor_for_job(app["job_id"], use_llm=False)
    return HTMLResponse(tb["resume_html"])


@app.get("/api/applications/{app_id}/fill")
def application_fill(app_id: int) -> dict:
    app = db.get_application(app_id)
    if not app:
        raise HTTPException(404, "not found")
    tb = tailor.tailor_for_job(app["job_id"], use_llm=False)
    return tb["fill"]


@app.post("/api/applications/{app_id}/approve")
def approve_app(app_id: int) -> dict:
    app = db.get_application(app_id)
    if not app:
        raise HTTPException(404, "not found")
    db.update_application_status(app_id, "approved")
    return {"ok": True, "status": "approved"}


@app.post("/api/applications/batch-approve")
async def batch_approve(request: Request) -> dict:
    """One-click approve many applications (still human-in-the-loop).

    Body: {"ids": [1,2,3]} to approve specific cards, OR
          {"min_score": 80} to approve every matched/drafted card at/above score.
    Returns count approved.
    """
    body = await request.json()
    ids = body.get("ids")
    approved = 0
    if ids:
        for aid in ids:
            try:
                db.update_application_status(int(aid), "approved")
                approved += 1
            except Exception:
                pass
    else:
        min_score = float(body.get("min_score", 0) or 0)
        apps = db.list_applications(status="drafted") + db.list_applications(status="matched")
        for a in apps:
            if (a.get("match_score") or 0) >= min_score:
                db.update_application_status(a["id"], "approved")
                approved += 1
    return {"ok": True, "approved": approved}


@app.post("/api/applications/smart-draft")
async def smart_draft(request: Request = None) -> dict:
    """Smart mode: match ALL jobs, auto-tailor, and draft. Collapses the
    manual 'match -> generate' steps into one call."""
    settings = db.get_settings()
    sch = settings.get("schedule", {})
    res = matcher.match_and_store(
        min_score=float(sch.get("min_match_score", 60)),
        auto_draft=True,
    )
    return {"ok": True, **res}


@app.post("/api/applications/{app_id}/send")
async def send_app(app_id: int, request: Request) -> dict:
    body = await request.json()
    if (await request.body()):
        pass
    force = bool(body.get("force", False))
    if body.get("email_to"):
        db.upsert_application(db.get_application(app_id)["job_id"], {"email_to": body["email_to"]})
    return submitter.send_email(app_id, force=force)


@app.post("/api/applications/batch/send")
async def send_batch(request: Request) -> dict:
    body = await request.json()
    ids = body.get("ids", [])
    return {"results": submitter.send_batch(ids)}


@app.post("/api/applications/{app_id}/status")
async def set_status(app_id: int, request: Request) -> dict:
    body = await request.json()
    status = body.get("status")
    if not status:
        raise HTTPException(400, "status required")
    extra = {k: v for k, v in body.items() if k in ("notes", "email_to", "sent_at")}
    app = db.update_application_status(app_id, status, extra)
    if not app:
        raise HTTPException(404, "not found")
    return app


# ------------------------------- chat ----------------------------------------
@app.get("/api/chat/conversations")
def list_convs(status: str | None = None) -> dict:
    return {"conversations": db.list_conversations(status=status)}


@app.get("/api/chat/conversations/{conv_id}")
def get_conv(conv_id: int) -> dict:
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    return {"conversation": conv, "messages": db.list_messages(conv_id)}


@app.post("/api/chat/inbound")
async def inbound(request: Request) -> dict:
    """Recruiter message arrives (via BOSS extension bridge or manual test)."""
    b = await request.json()
    platform = b.get("platform", "boss")
    recruiter = b.get("recruiter_name", "HR")
    job_title = b.get("job_title", "")
    company = b.get("company", "")
    job_id = b.get("job_id")
    text = (b.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    conv = db.get_or_create_conversation(platform, recruiter, job_title, company, job_id)
    msg = db.add_message(conv["id"], "inbound", text, status="received")
    return {"conversation": conv, "message": msg}


@app.post("/api/chat/{conv_id}/draft")
async def draft_reply(conv_id: int, request: Request) -> dict:
    """Generate a suggested reply (NOT stored). Frontend shows it for review."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    # pick the latest inbound message as context if none given
    inbound = b.get("text")
    if not inbound:
        msgs = db.list_messages(conv_id)
        inbound = next((m["text"] for m in reversed(msgs) if m["direction"] == "inbound"), "")
    use_llm = bool(b.get("use_llm", True))
    return chat.generate_reply(conv, inbound, use_llm=use_llm)


@app.post("/api/chat/{conv_id}/message")
async def store_outbound(conv_id: int, request: Request) -> dict:
    """Store an outbound reply draft (status drafted/approved)."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    text = (b.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    status = b.get("status", "drafted")
    reply_to = b.get("reply_to")
    meta = b.get("meta", {})
    return db.add_message(conv_id, "outbound", text, status=status, reply_to=reply_to, meta=meta)


@app.post("/api/chat/{conv_id}/approve")
async def approve_reply(conv_id: int, request: Request) -> dict:
    """Approve a stored outbound draft so the bridge can send it."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    msg_id = b.get("message_id")
    if msg_id:
        db.update_message(msg_id, {"status": "approved"})
    else:
        # approve the latest outbound draft
        msgs = db.list_messages(conv_id)
        for m in reversed(msgs):
            if m["direction"] == "outbound" and m["status"] in ("drafted", "received"):
                db.update_message(m["id"], {"status": "approved"})
                msg_id = m["id"]
                break
    return {"ok": True, "message_id": msg_id, "status": "approved"}


@app.post("/api/chat/{conv_id}/adopt-all")
async def adopt_all(conv_id: int) -> dict:
    """Approve every pending outbound draft in a conversation at once
    (still human-in-the-loop: each becomes 'approved' and waits for the
    user to confirm the actual send in BOSS via the extension)."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    msgs = db.list_messages(conv_id)
    approved = 0
    for m in msgs:
        if m["direction"] == "outbound" and m["status"] in ("drafted", "received"):
            db.update_message(m["id"], {"status": "approved"})
            approved += 1
    return {"ok": True, "approved": approved}


@app.post("/api/chat/{conv_id}/send")
async def send_reply(conv_id: int, request: Request) -> dict:
    """Approve (if needed) and hand the message to the BOSS bridge for typing.

    NOTE: this tool never sends directly to BOSS (no API, ToS). The approved text
    is picked up by the user-operated browser extension, typed into the chat box,
    and the user confirms the send. We only mark it ready here.
    """
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    msg_id = b.get("message_id")
    if msg_id:
        db.update_message(msg_id, {"status": "approved"})
    else:
        msgs = db.list_messages(conv_id)
        for m in reversed(msgs):
            if m["direction"] == "outbound" and m["status"] in ("drafted", "received", "approved"):
                db.update_message(m["id"], {"status": "approved"})
                msg_id = m["id"]
                break
    if not msg_id:
        return {"ok": False, "error": "没有可发送的回复草稿，请先「采用」一条回复。"}
    return {"ok": True, "mode": "bridge", "message_id": msg_id,
            "note": "已就绪，请在 BOSS 直聘页面用扩展把这条消息发出去（人工确认）。"}


@app.get("/api/chat/{conv_id}/outbound")
def pending_outbound(conv_id: int) -> dict:
    """Bridge endpoint: the BOSS extension polls this for an approved message to type."""
    msg = db.pending_outbound(conv_id)
    if not msg:
        return {"pending": False}
    return {"pending": True, "message_id": msg["id"], "text": msg["text"]}


@app.post("/api/chat/{conv_id}/sent")
async def mark_sent(conv_id: int, request: Request) -> dict:
    """Bridge endpoint: extension reports the message was actually sent by the user."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    msg_id = b.get("message_id")
    if msg_id:
        db.update_message(msg_id, {"status": "sent"})
    # mark the inbound it answered as read
    return {"ok": True, "status": "sent"}


@app.post("/api/chat/{conv_id}/simulate")
async def simulate_inbound(conv_id: int, request: Request) -> dict:
    """Test helper: drop a canned recruiter message into a conversation."""
    b = await request.json()
    text = b.get("text", "你好，看到你的简历，方便聊聊吗？")
    msg = db.add_message(conv_id, "inbound", text, status="received")
    return {"ok": True, "message": msg}


@app.get("/api/chat/needs-followup")
def needs_followup() -> dict:
    """List conversations where the recruiter's last message is still unanswered."""
    return {"conversations": chat.conversations_needing_followup()}


@app.post("/api/chat/{conv_id}/followup")
async def followup(conv_id: int, request: Request) -> dict:
    """Generate a polite follow-up reply for an unanswered recruiter message."""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "not found")
    b = await request.json()
    return chat.generate_followup(conv, use_llm=bool(b.get("use_llm", True)))


# ------------------------------ settings -------------------------------------
@app.get("/api/settings")
def get_settings() -> dict:
    return db.get_settings()


@app.post("/api/settings")
async def post_settings(request: Request) -> dict:
    body = await request.json()
    for k, v in body.items():
        db.set_setting(k, v)
    # If the user just turned autopilot on, run the hands-off pipeline immediately
    # so they don't have to wait for the next scheduled tick.
    if "autopilot" in body:
        ap = body["autopilot"]
        if isinstance(ap, dict) and ap.get("enabled"):
            scheduler.kick()
    return db.get_settings()


# ----------------------------- autopilot ------------------------------------ #
@app.get("/api/autopilot/status")
def autopilot_status() -> dict:
    ap = db.get_settings().get("autopilot", {})
    return {
        "enabled": bool(ap.get("enabled")),
        "auto_approve": bool(ap.get("auto_approve", True)),
        "auto_send": bool(ap.get("auto_send", False)),
        "auto_chat_reply": bool(ap.get("auto_chat_reply", True)),
        "min_score": ap.get("min_score", 75),
        "max_send_per_day": ap.get("max_send_per_day", 30),
        "last_run": scheduler.last_autopilot,
    }


@app.post("/api/autopilot/run")
def autopilot_run() -> dict:
    """Manually trigger one autopilot pass (used by the '立即运行一次' button)."""
    return {"ok": True, "report": scheduler._autopilot()}


# --------------------------- resume optimizer ------------------------------ #
@app.post("/api/optimize/research")
async def optimize_research(request: Request) -> dict:
    """Analyze what a target role requires (knowledge base + JD + optional LLM)."""
    b = await request.json()
    title = (b.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "请填写目标岗位名称"}
    reqs = optimizer.research_requirements(
        title, b.get("company", ""), b.get("jd_text", ""),
        use_llm=bool(b.get("use_llm", True)),
    )
    return {"ok": True, "requirements": reqs}


@app.post("/api/optimize/run")
async def optimize_run(request: Request) -> dict:
    """Analyze the job + rewrite the pasted resume to fit it."""
    b = await request.json()
    return optimizer.optimize_resume(
        resume_text=b.get("resume_text", ""),
        title=(b.get("title") or "").strip(),
        company=b.get("company", ""),
        jd_text=b.get("jd_text", ""),
        use_llm=bool(b.get("use_llm", True)),
    )


@app.post("/api/optimize/save-profile")
async def optimize_save_profile(request: Request) -> dict:
    """Take an optimized resume text and store it as the profile's resume_html,
    so the rest of the pipeline (matching / auto-apply) uses the improved one."""
    b = await request.json()
    html_resume = (b.get("optimized_html") or "").strip()
    if not html_resume:
        return {"ok": False, "error": "没有可保存的优化简历"}
    prof = db.get_profile()
    prof["resume_html"] = html_resume
    db.upsert_profile(prof)
    return {"ok": True}


# --------------------------- dashboard / stats ---------------------------- #
@app.get("/api/stats")
def stats() -> dict:
    """Aggregate funnel + kanban + reply/interview rates for the dashboard."""
    return db.application_stats()


# ------------------------------ blacklist ---------------------------------- #
@app.get("/api/blacklist")
def get_blacklist() -> dict:
    return {"blacklist": db.list_blacklist(active_only=True)}


@app.post("/api/blacklist")
async def add_blacklist(request: Request) -> dict:
    b = await request.json()
    kind = (b.get("kind") or "company").strip() or "company"
    value = (b.get("value") or "").strip()
    if not value:
        return {"ok": False, "error": "请填写要拉黑的内容（公司名 / 关键词）"}
    entry = db.add_blacklist(kind, value, reason=(b.get("reason") or "").strip(),
                             source="manual", until_days=b.get("until_days"))
    return {"ok": True, "entry": entry}


@app.post("/api/blacklist/auto")
async def auto_blacklist(request: Request) -> dict:
    """Auto-accumulate a blacklist entry (from 已读不回 / 不匹配 / 拒绝).

    Called by the UI when the user rejects an application or marks a conversation
    as '已读不回'. Keeps the rule engine honest: blacklisting is always explicit
    (user-triggered), never silent.
    """
    b = await request.json()
    kind = (b.get("kind") or "company").strip() or "company"
    value = (b.get("value") or "").strip()
    if not value:
        return {"ok": False, "error": "缺少拉黑内容"}
    reason = (b.get("reason") or "自动拉黑（已读不回/不匹配/拒绝）").strip()
    entry = db.add_blacklist(kind, value, reason=reason, source="auto",
                             until_days=b.get("until_days", 30))
    return {"ok": True, "entry": entry}


@app.delete("/api/blacklist/{bl_id}")
async def del_blacklist(bl_id: int) -> dict:
    db.remove_blacklist(bl_id)
    return {"ok": True}


# --------------------------- llm providers --------------------------------- #
@app.get("/api/llm/providers")
def llm_providers() -> dict:
    """List all supported model providers (presets + model ids, no secrets)."""
    return {"providers": llm_client.list_providers()}


@app.post("/api/llm/test")
async def llm_test(request: Request) -> dict:
    """Test connectivity for the LLM config currently in settings (or payload)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    # allow testing a config that isn't saved yet
    settings = {"llm": body.get("llm")} if body.get("llm") else None
    if settings is None:
        settings = db.get_settings()
    return llm_client.test_connection(settings)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8011)
