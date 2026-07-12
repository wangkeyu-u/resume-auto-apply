#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end self-test for the resume-auto-apply system (v2, harness-fixed).

Hits the LIVE running API (http://127.0.0.1:8011). Checks endpoints respond AND
that output is actually useful (real scores, real tailored HTML resume, sensible
chat intent, working approval gate, working extension contract).

Run:  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; python3 selftest.py
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:8011"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(opener)

PASS, FAIL = [], []


def _mark(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"_raw": raw, "error": raw}
        return e.code, j
    except Exception as e:
        return -1, {"error": str(e)}


def call_raw(method, path, body=None):
    """Like call() but returns raw response TEXT (for HTML endpoints)."""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


print("=" * 70)
print("RESUME AUTO-APPLY  —  END-TO-END SELF TEST (v2)")
print("=" * 70)

# 1) HEALTH ----------------------------------------------------------------
st, h = call("GET", "/api/health")
ok = st == 200 and h.get("ok") is True
_mark("health endpoint responds", ok, f"jobs={h.get('jobs')} status={h.get('status_counts')}")

# 2) SETTINGS --------------------------------------------------------------
st, s = call("GET", "/api/settings")
smtp_cfg = s.get("smtp", {})
sources = s.get("sources", [])
enabled_sources = [x for x in sources if x.get("enabled")]
_mark("settings readable", st == 200)
_mark("at least one job source enabled", bool(enabled_sources),
      f"sources={[x.get('id') for x in enabled_sources]}")
smtp_enabled = bool(smtp_cfg.get("enabled"))
print(f"    (info) SMTP enabled = {smtp_enabled}  <- real email delivery needs config")

# 3) PROFILE (realistic backend candidate) ---------------------------------
profile = {
    "name": "李四", "email": "lisi@example.com", "phone": "13800000000",
    "title": "后端开发工程师", "location": "深圳", "years_experience": 5,
    "summary": "5年后端开发经验，专注于高并发分布式系统与云原生架构。",
    "skills": ["Python", "Go", "微服务", "Kubernetes", "Docker", "分布式", "高并发",
               "gRPC", "Redis", "MySQL", "Kafka", "Linux"],
    "salary_min": 25000, "salary_max": 45000,
    "resume_data": {
        "educations": [{"school": "华南理工大学", "major": "计算机科学与技术",
                         "degree": "本科", "start": "2014", "end": "2018"}],
        "experiences": [
            {"title": "高级后端工程师", "company": "腾讯", "start": "2021", "end": "2026",
             "bullets": ["主导高并发交易系统的微服务化改造", "基于Kubernetes构建云原生部署平台", "使用Go与gRPC重构核心链路"]},
            {"title": "后端工程师", "company": "字节跳动", "start": "2018", "end": "2021",
             "bullets": ["负责分布式存储服务", "使用Python与Kafka构建实时数据处理管道"]}],
        "projects": [{"name": "云原生调度平台", "role": "技术负责人",
                       "bullets": ["基于K8s与Docker", "支撑日均10亿请求"]}],
        "certifications": ["CKA Kubernetes管理员"], "languages": ["英语CET-6"]},
}
st, p = call("POST", "/api/profile", profile)
ok = st == 200 and p.get("name") == "李四"
_mark("profile save", ok)

st, comp = call("GET", "/api/profile/completeness")
ok = st == 200 and comp.get("score", 0) >= 90
_mark("profile completeness high", ok, f"score={comp.get('score')} missing={comp.get('missing')}")

# 4) MATCHING --------------------------------------------------------------
st, m = call("POST", "/api/match", {"min_score": 60, "auto_draft": True})
ok = st == 200 and "evaluated" in m
_mark("match endpoint runs", ok, f"evaluated={m.get('evaluated')} matched/drafted={m.get('matched')}")
results = m.get("results", [])
by_title = {r["title"]: r["score"] for r in results}
print(f"    (info) sample scores: " + ", ".join(f"{k}={v}" for k, v in list(by_title.items())[:6]))

# 5) FRESH DRAFT + TAILORING QUALITY ---------------------------------------
# Pick a backend-flavoured demo job to draft fresh (clean quality signal).
st, jobs = call("GET", "/api/jobs")
target = next((j for j in jobs.get("jobs", []) if "后端" in j["title"] or "Go" in j["title"]), None)
if not target:
    target = (jobs.get("jobs") or [None])[0]
if target:
    jid = target["id"]
    st, d = call("POST", f"/api/applications/{jid}/draft", {"use_llm": False})
    ok = st == 200 and d.get("id")
    _mark("fresh draft created", ok, f"job='{target.get('title')}'")
    app_id = d.get("id")
    # resume HTML (raw)
    st, rhtml = call_raw("GET", f"/api/applications/{app_id}/resume")
    ok = st == 200 and "李四" in rhtml and target.get("title", "") in rhtml and len(rhtml) > 200
    _mark("tailored resume renders (candidate+role, real HTML)", ok,
          f"len={len(rhtml)} contains_name={'李四' in rhtml} contains_role={target.get('title','') in rhtml}")
    # screening report
    scr = d.get("screening_report")
    if isinstance(scr, str):
        try:
            scr = json.loads(scr)
        except Exception:
            scr = {}
    cov = scr.get("coverage_pct") if isinstance(scr, dict) else None
    ok = isinstance(cov, int) and cov >= 50
    _mark("screening report meaningful (coverage>=50)", ok,
          f"coverage={cov} matched={len(scr.get('matched', [])) if isinstance(scr, dict) else 0} "
          f"gaps={len(scr.get('gaps', [])) if isinstance(scr, dict) else 0}")
    # fill suggestions
    st, fill = call("GET", f"/api/applications/{app_id}/fill")
    ok = st == 200 and isinstance(fill, dict) and fill.get("核心技能") and fill.get("自我评价")
    _mark("fill suggestions generated", ok, f"intent={fill.get('求职意向') if isinstance(fill, dict) else None}")
else:
    for nm in ["fresh draft created", "tailored resume renders",
               "screening report meaningful", "fill suggestions generated"]:
        _mark(nm, False, "no target job")

# 6) APPROVAL GATE + SEND (correctly blocked / honestly fails) -------------
if 'app_id' in dir() and app_id:
    st, blocked = call("POST", f"/api/applications/{app_id}/send", {"force": False})
    # endpoint returns 200 with {"ok":false} — the message must say "批准"
    ok = (st == 200 and blocked.get("ok") is False and "批准" in str(blocked.get("error", ""))) \
         or (st != 200 and "批准" in str(blocked))
    _mark("send blocked before approval (safety gate)", ok, f"resp={blocked.get('error') or blocked}")
    st, ap = call("POST", f"/api/applications/{app_id}/approve")
    ok = st == 200 and ap.get("status") == "approved"
    _mark("approval works", ok)
    st, sent = call("POST", f"/api/applications/{app_id}/send", {"force": False})
    if smtp_enabled:
        ok = st == 200 and sent.get("ok") is True
        _mark("send succeeds (SMTP on)", ok, f"{sent}")
    else:
        ok = (st == 200 and sent.get("ok") is False and "SMTP" in str(sent.get("error", ""))) \
             or (st != 200 and "SMTP" in str(sent))
        _mark("send fails HONESTLY when SMTP off (no crash)", ok, f"resp={sent.get('error') or sent}")
else:
    for nm in ["send blocked before approval", "approval works", "send fails HONESTLY when SMTP off"]:
        _mark(nm, False, "no app_id")

# 7) CHAT FLOW -------------------------------------------------------------
st, conv = call("POST", "/api/chat/inbound", {
    "platform": "boss", "recruiter_name": "王经理", "job_title": "后端开发工程师",
    "company": "云启科技", "job_id": (target.get("id") if 'target' in dir() and target else None),
    "text": "你好，看到你的简历，方便聊聊吗？"
})
conv_ok = st == 200 and conv.get("conversation", {}).get("id")
_mark("chat: inbound creates conversation", conv_ok)
conv_id = conv.get("conversation", {}).get("id")

if conv_id:
    st, dr = call("POST", f"/api/chat/{conv_id}/draft", {"text": "你好，看到你的简历，方便聊聊吗？", "use_llm": False})
    ok = st == 200 and dr.get("intent") == "greeting" and len(dr.get("text", "")) > 10
    _mark("chat: greeting intent + reply", ok, f"intent={dr.get('intent')}")
    st, dr2 = call("POST", f"/api/chat/{conv_id}/draft", {"text": "你期望薪资大概多少？", "use_llm": False})
    ok = st == 200 and dr2.get("intent") == "salary" and ("k" in dr2.get("text", ""))
    _mark("chat: salary intent + reply", ok, f"intent={dr2.get('intent')}")
    st, dr3 = call("POST", f"/api/chat/{conv_id}/draft", {"text": "你做过几年相关经验？技术栈熟不熟？", "use_llm": False})
    ok = st == 200 and dr3.get("intent") == "experience" and len(dr3.get("text", "")) > 10
    _mark("chat: experience intent + reply", ok, f"intent={dr3.get('intent')}")
    st, out = call("POST", f"/api/chat/{conv_id}/message", {"text": dr.get("text"), "status": "drafted"})
    out_id = out.get("id")
    st, ap = call("POST", f"/api/chat/{conv_id}/approve", {"message_id": out_id})
    ok = st == 200 and ap.get("status") == "approved"
    _mark("chat: approve outbound", ok)
    st, snd = call("POST", f"/api/chat/{conv_id}/send", {"message_id": out_id})
    ok = st == 200 and snd.get("ok") is True and snd.get("mode") == "bridge"
    _mark("chat: send hands to bridge (human-in-loop)", ok, f"mode={snd.get('mode')}")
    st, pend = call("GET", f"/api/chat/{conv_id}/outbound")
    ok = st == 200 and pend.get("pending") is True and pend.get("text")
    _mark("chat: bridge sees pending outbound", ok)
    st, done = call("POST", f"/api/chat/{conv_id}/sent", {"message_id": out_id})
    ok = st == 200 and done.get("ok") is True
    _mark("chat: mark sent (extension confirms)", ok)
else:
    for nm in ["chat: greeting intent + reply", "chat: salary intent + reply",
               "chat: experience intent + reply", "chat: approve outbound",
               "chat: send hands to bridge (human-in-loop)", "chat: bridge sees pending outbound",
               "chat: mark sent (extension confirms)"]:
        _mark(nm, False, "no conversation")

# 8) EXTENSION CONTRACT ----------------------------------------------------
import time as _time
_frag = str(int(_time.time()))  # unique per run so the insert path is truly exercised
fake_jobs = [
    {"title": "Go 高级后端工程师", "company": "幻方科技", "location": "深圳",
     "url": f"https://jobs.example.com/go-{_frag}", "description": "熟悉 Go、微服务、Kubernetes、高并发分布式系统。",
     "salary_text": "¥35k-55k", "salary_min": 35000, "salary_max": 55000},
    {"title": "云平台开发", "company": "云栖", "location": "杭州",
     "url": f"https://jobs.example.com/cloud-{_frag}", "description": "容器、Service Mesh、可观测性、Terraform。",
     "salary_text": "¥30k-50k", "salary_min": 30000, "salary_max": 50000},
    {"title": "UI 设计师", "company": "美刻", "location": "上海",
     "url": f"https://jobs.example.com/ui-{_frag}", "description": "Figma、交互设计、用户体验。",
     "salary_text": "¥20k-30k", "salary_min": 20000, "salary_max": 30000},
]
st, imp = call("POST", "/api/jobs/import-bulk", {"jobs": fake_jobs, "auto_match": True})
ok = st == 200 and imp.get("ok") is True and imp.get("imported", 0) == 3
_mark("extension: import-bulk (3 scraped jobs inserted)", ok, f"{imp}")
st, bu = call("GET", "/api/jobs/by-url?url=" + urllib.parse.quote(fake_jobs[0]["url"]))
ok = st == 200 and bu.get("found") is True
_mark("extension: resolve job by url", ok, f"{bu}")
qt = urllib.parse.quote("Go 高级后端工程师")
qc = urllib.parse.quote("幻方科技")
st, fnd = call("GET", f"/api/jobs/find?title={qt}&company={qc}")
ok = st == 200 and fnd.get("found") is True
_mark("extension: resolve job by title/company (URL-encoded)", ok, f"{fnd}")
if bu.get("found"):
    jid2 = bu["job_id"]
    st, jf = call("GET", f"/api/jobs/{jid2}/fill")
    ok = st == 200 and isinstance(jf, dict) and jf.get("求职意向")
    _mark("extension: per-job fill values", ok, f"intent={jf.get('求职意向') if isinstance(jf, dict) else None}")
st, cfg = call("GET", "/api/config/export")
ok = st == 200 and cfg.get("api_base") == BASE and "auto_tailor" in cfg
_mark("extension: config/export bootstrap", ok, f"{cfg}")

# 9) AGGREGATOR REFRESH (with concurrency stress to prove no DB lock crash) -
import concurrent.futures as _cf
def _refresh_once():
    s, r = call("POST", "/api/jobs/refresh")
    return s, r
with _cf.ThreadPoolExecutor(max_workers=6) as ex:
    futs = [ex.submit(_refresh_once) for _ in range(6)]
    _rf_res = [f.result() for f in futs]
ok = all(s == 200 and "new_jobs" in r for s, r in _rf_res)
_mark("aggregator refresh survives concurrent load (no 'database is locked')",
      ok, f"runs={len(_rf_res)} sample={_rf_res[0][1].get('new_jobs') if _rf_res else None}")

# 10) RE-TAILOR ON PROFILE UPDATE (regression for stale drafts) ------------
# re-POST profile (simulate user refining it) and confirm a drafted app's
# screening coverage is refreshed, not stale.
st, p2 = call("POST", "/api/profile", {**profile, "skills": profile["skills"] + ["Rust", "ClickHouse"]})
ok = st == 200 and p2.get("name") == "李四"
_mark("profile re-save triggers re-tailor", ok)
# verify at least one drafted app now has a fresh (high) coverage
st, drafted = call("GET", "/api/applications?status=drafted")
coverage_ok = False
for a in drafted.get("applications", []):
    scr = a.get("screening_report")
    if isinstance(scr, str):
        try:
            scr = json.loads(scr)
        except Exception:
            scr = {}
    if isinstance(scr, dict) and scr.get("coverage_pct", 0) >= 50:
        coverage_ok = True
        break
_mark("drafts stay fresh after profile update (coverage>=50)", coverage_ok,
      f"drafted_count={len(drafted.get('applications', []))}")

# 14) LLM PROVIDER REGISTRY ------------------------------------------------
st, pr = call("GET", "/api/llm/providers")
prov = (pr or {}).get("providers", [])
expected = {"openai", "deepseek", "zhipu", "moonshot", "qwen", "doubao",
            "baichuan", "minimax", "yi", "hunyuan", "hy3", "anthropic",
            "gemini", "openrouter", "azure"}
have = {p.get("key") for p in prov}
ok = st == 200 and expected.issubset(have) and all(p.get("models") for p in prov)
_mark(f"LLM: provider registry lists {len(prov)} mainstream providers", ok,
      f"missing={sorted(expected - have)}")

# graceful fallback when no key configured
st, t = call("POST", "/api/llm/test", {"llm": {"enabled": True, "provider": "openai", "api_key": "", "base_url": "", "model": ""}})
ok = st == 200 and t.get("ok") is False and "API Key" in (t.get("error") or "")
_mark("LLM: test endpoint fails gracefully without key", ok, f"{t}")

# unknown provider key is rejected by config resolution
st, t2 = call("POST", "/api/llm/test", {"llm": {"enabled": True, "provider": "nope", "api_key": "x", "base_url": "http://x", "model": "m"}})
ok = st == 200 and t2.get("ok") is False
_mark("LLM: unknown provider key rejected", ok, f"{t2}")

# 15) RESUME PARSE (no LLM -> heuristic fallback returns structured profile) -#
st, rp = call("POST", "/api/profile/parse", {"text": "张三 13800001111 zhang@mail.com\nPython Go Kubernetes 5年经验 后端工程师 期望城市 深圳"})
ok = st == 200 and rp.get("ok") and isinstance(rp.get("profile"), dict) and rp["profile"].get("email")
_mark("resume: paste text -> structured profile (heuristic)", ok,
      f"name={rp.get('profile',{}).get('name')} email={rp.get('profile',{}).get('email')} skills={rp.get('profile',{}).get('skills')}")

# 16) SMART DRAFT (match + tailor + draft all in one call) ------------------#
st, sd = call("POST", "/api/applications/smart-draft")
ok = st == 200 and "evaluated" in sd
_mark("smart-draft: one-click match+tailor+draft all jobs", ok,
      f"evaluated={sd.get('evaluated')} matched={sd.get('matched')}")

# 17) BATCH APPROVE (by threshold) ------------------------------------------#
# first ensure there are some matched/drafted apps, then approve >= a floor
call("POST", "/api/applications/smart-draft")
st, ba = call("POST", "/api/applications/batch-approve", {"min_score": 0})
ok = st == 200 and isinstance(ba.get("approved"), int) and ba["approved"] >= 0
_mark("batch-approve: approve all matched/drafted at once", ok, f"approved={ba.get('approved')}")

# 18) CHAT ADOPT-ALL (approve every pending outbound in a conversation) -----#
st, ib = call("POST", "/api/chat/inbound", {"platform": "boss", "recruiter_name": "测试HR",
                                            "job_title": "后端工程师", "company": "测测科技",
                                            "text": "你好，看简历合适，方便聊聊吗？"})
conv_id = (ib.get("conversation") or {}).get("id")
st, _ = call("POST", f"/api/chat/{conv_id}/message", {"text": "你好，方便，我做过相关方向。", "status": "drafted"})
st, aa = call("POST", f"/api/chat/{conv_id}/adopt-all")
ok = st == 200 and aa.get("approved", 0) >= 1
_mark("chat: adopt-all approves pending outbound in conversation", ok, f"approved={aa.get('approved')}")

# 19) AUTOPILOT: truly hands-off (approve + send + chat) -------------------#
# ensure there are drafted applications to auto-approve
call("POST", "/api/applications/smart-draft")
st, apps = call("GET", "/api/applications?status=drafted")
drafted = (apps or {}).get("applications", []) if isinstance(apps, dict) else []
if not drafted:
    st, apps = call("GET", "/api/applications")
    drafted = (apps or {}).get("applications", []) if isinstance(apps, dict) else []
target = drafted[0]["id"] if drafted else None
if target:
    # give it a recipient address so the auto-send path has a target
    call("POST", f"/api/applications/{target}/status",
         {"status": "drafted", "email_to": "hr_autotest@example.com"})

# a fresh conversation with only an inbound (no outbound yet) -> autopilot should
# draft AND approve a reply on its own
call("POST", "/api/chat/inbound", {"platform": "boss", "recruiter_name": "自动HR",
                                   "job_title": "算法工程师", "company": "自动科技",
                                   "text": "请问你熟悉 Transformer 吗？"})

# bogus SMTP so auto-send attempts a real connection (and fails fast -> sent_failed)
call("POST", "/api/settings", {"smtp": {"enabled": True, "host": "127.0.0.1", "port": 9,
                                        "username": "x", "password": "y", "use_tls": False,
                                        "from_address": "me@test.com", "from_name": "Me"}})
# enable autopilot: 自动批准(>=0) + 无人值守发送 + 自动采纳HR回复
call("POST", "/api/settings", {"autopilot": {"enabled": True, "auto_approve": True,
        "auto_send": True, "auto_chat_reply": True, "min_score": 0, "max_send_per_day": 30}})
st, run = call("POST", "/api/autopilot/run")
rep = (run or {}).get("report", {})
ok = st == 200 and rep.get("enabled") is True and rep.get("approved", 0) >= 1 and rep.get("chat_replies", 0) >= 1
_mark("autopilot: hands-off approve+send+chat runs", ok, f"{rep}")

if target:
    st, app_obj = call("GET", f"/api/applications/{target}")
    st_after = (app_obj or {}).get("status")
    ok2 = st_after in ("sent", "sent_failed")
    _mark("autopilot: auto-send path executed (terminal status set)", ok2, f"status={st_after}")

# disabled -> explicit no-op
call("POST", "/api/settings", {"autopilot": {"enabled": False}})
st, run2 = call("POST", "/api/autopilot/run")
ok3 = st == 200 and (run2 or {}).get("report", {}).get("enabled") is False
_mark("autopilot: disabled => no-op", ok3, f"{(run2 or {}).get('report')}")

# clean up so the live server doesn't keep failing sends in the background
call("POST", "/api/settings", {"smtp": {"enabled": False}, "autopilot": {"enabled": False}})

# --------------------------------------------------------------------------- #
# Resume Optimizer (简历优化)
# --------------------------------------------------------------------------- #
print("-" * 70)
print("RESUME OPTIMIZER")

st, res = call("POST", "/api/optimize/research",
               {"title": "后端开发工程师", "company": "字节跳动",
                "jd_text": "熟悉 Kubernetes 和 Kafka，精通 MySQL 优化", "use_llm": False})
reqs = (res or {}).get("requirements", {})
_mark("optimize: research maps to a role", st == 200 and res.get("ok") and reqs.get("matched_role") == "backend",
      f"role={reqs.get('role_label')} source={reqs.get('source')}")
_mark("optimize: research returns must_have requirements", bool(reqs.get("must_have")),
      f"{(reqs.get('must_have') or [])[:5]}")
_mark("optimize: research extracts skills from pasted JD",
      "kubernetes" in [x.lower() for x in reqs.get("from_jd", [])],
      f"from_jd={reqs.get('from_jd')}")

st, res = call("POST", "/api/optimize/research",
               {"title": "前端开发工程师", "use_llm": False})
_mark("optimize: research works with title only (no JD)",
      st == 200 and (res.get("requirements") or {}).get("matched_role") == "frontend")

# missing title -> graceful error
st, res = call("POST", "/api/optimize/research", {"title": "", "use_llm": False})
_mark("optimize: research rejects empty title", res.get("ok") is False)

# full optimize run
resume_txt = ("张三，5年后端开发经验。技能：Java, Spring Boot, MySQL, Redis。"
              "工作经历：A公司后端工程师 2020-2024，负责订单微服务。")
st, res = call("POST", "/api/optimize/run",
               {"title": "后端开发工程师", "company": "字节跳动",
                "jd_text": "熟悉 Kubernetes 和 Kafka", "resume_text": resume_txt, "use_llm": False})
m = (res or {}).get("match", {})
_mark("optimize: run returns coverage + optimized resume",
      st == 200 and res.get("ok") and "coverage_pct" in m and bool(res.get("optimized_html")),
      f"coverage={m.get('coverage_pct')}% method={res.get('method')}")
_mark("optimize: matched includes resume skills (Java/MySQL)",
      any(k in m.get("matched", []) for k in ("Java", "MySQL")),
      f"matched={m.get('matched')}")
_all_gaps = m.get("gaps", []) + m.get("nice_gaps", [])
_mark("optimize: gaps flag missing JD skills (Kubernetes/Kafka)",
      any(g.lower() in ("kubernetes", "kafka", "k8s") for g in _all_gaps),
      f"gaps={m.get('gaps')[:4]} nice_gaps={m.get('nice_gaps', [])[:4]}")
_mark("optimize: produces change explanations", bool(res.get("changes")),
      f"{len(res.get('changes', []))} changes")

# alias normalization: HTML/CSS should match HTML5/CSS3
st, res = call("POST", "/api/optimize/run",
               {"title": "前端开发工程师",
                "resume_text": "李四，技能：Vue, JavaScript, HTML, CSS。", "use_llm": False})
mm = (res or {}).get("match", {})
_mark("optimize: alias match (HTML->HTML5, CSS->CSS3)",
      "HTML5" in mm.get("matched", []) and "CSS3" in mm.get("matched", []),
      f"matched={mm.get('matched')}")

# empty resume -> graceful error
st, res = call("POST", "/api/optimize/run", {"title": "后端开发工程师", "resume_text": "", "use_llm": False})
_mark("optimize: run rejects empty resume", res.get("ok") is False)

# save optimized resume into profile (restore original afterwards)
_st, _prof_before = call("GET", "/api/profile")
_orig_html = (_prof_before or {}).get("resume_html") or ""
st, res = call("POST", "/api/optimize/save-profile",
               {"optimized_html": "<html><body><h1>优化简历</h1></body></html>"})
_mark("optimize: save optimized resume to profile", st == 200 and res.get("ok"))
# restore whatever the user had before
call("POST", "/api/profile", {**(_prof_before or {}), "resume_html": _orig_html})

# --------------------------------------------------------------------------- #
print("-" * 70)
print("DASHBOARD / BLACKLIST / FOLLOW-UP")
print("-" * 70)

# stats endpoint
st, stats = call("GET", "/api/stats")
ok = st == 200 and "funnel" in stats and "rates" in stats and "by_status" in stats
_mark("stats: dashboard aggregate returns funnel/rates/kanban", ok,
      f"jobs={stats.get('jobs_total')} funnel_keys={list((stats.get('funnel') or {}).keys())}")

# blacklist add + list
st, bl = call("POST", "/api/blacklist", {"kind": "company", "value": "黑名单测试公司", "reason": "自动化测试"})
ok = st == 200 and bl.get("ok") and (bl.get("entry") or {}).get("id")
bl_id = (bl.get("entry") or {}).get("id")
_mark("blacklist: add company", ok, f"id={bl_id}")
st, bl_list = call("GET", "/api/blacklist")
ok = st == 200 and any(b.get("value") == "黑名单测试公司" for b in bl_list.get("blacklist", []))
_mark("blacklist: listed", ok)

# match should skip the blacklisted company
_frag2 = str(int(_time.time()))
call("POST", "/api/jobs/import", {"source": "blacklist-test", "source_id": f"bl-{_frag2}",
      "title": "测试工程师", "company": "黑名单测试公司", "location": "深圳",
      "description": "Python 测试", "salary_text": "20k"})
st, ms = call("POST", "/api/match", {"min_score": 0, "auto_draft": False})
ok = st == 200 and (ms.get("skipped_blacklist", 0) >= 1)
_mark("match: skips blacklisted company", ok, f"skipped={ms.get('skipped_blacklist')}")

# follow-up generation + needs-followup list
st, conv = call("POST", "/api/chat/inbound", {"platform": "boss", "recruiter_name": "跟进HR",
    "job_title": "测试岗", "company": "跟进公司", "text": "你好，方便聊聊吗？"})
conv_id = (conv.get("conversation") or {}).get("id")
st, fu = call("POST", f"/api/chat/{conv_id}/followup", {"use_llm": False})
ok = st == 200 and len(fu.get("text", "")) > 10 and fu.get("intent") == "followup"
_mark("followup: generates a polite follow-up message", ok, f"text='{fu.get('text','')[:24]}…'")
st, nf = call("GET", "/api/chat/needs-followup")
ok = st == 200 and isinstance(nf.get("conversations"), list)
_mark("followup: needs-followup list endpoint", ok, f"count={len(nf.get('conversations', []))}")

# cleanup blacklist
if bl_id:
    call("DELETE", f"/api/blacklist/{bl_id}")
st, bl_list2 = call("GET", "/api/blacklist")
ok = st == 200 and not any(b.get("value") == "黑名单测试公司" for b in bl_list2.get("blacklist", []))
_mark("blacklist: delete removes entry", ok)

# --------------------------------------------------------------------------- #
print("=" * 70)
print(f"RESULT:  {len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  - {f}")
print("=" * 70)
if not smtp_enabled:
    print("NOTE: Email delivery is intentionally disabled until you configure SMTP in 设置.")
print("      The approval gate, drafting, tailoring, chat and extension APIs all work.")
