// 简历自动投递助手 - 前端逻辑
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.status);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), 2200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// tabs
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  $$(".view").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $("#view-" + t.dataset.tab).classList.add("active");
  const map = { optimize: loadOptimize, dashboard: loadDashboard, overview: loadOverview, profile: loadProfile, jobs: loadJobs, queue: loadQueue, sent: loadSent, chat: loadChat, settings: loadSettings };
  map[t.dataset.tab]?.();
}));

// ---------------- optimize (简历优化) ----------------
let _lastOptimized = null; // { optimized_text, optimized_html }

function loadOptimize() {
  // prefill target title from profile if empty
  const t = $("#o_title");
  if (t && !t.value) {
    api("GET", "/api/profile").then((p) => { if (p && p.title && !t.value) t.value = p.title; }).catch(() => {});
  }
  const r = $("#o_resume");
  if (r) $("#o_resume_hint").textContent = r.value.trim() ? `已输入约 ${r.value.trim().length} 字` : "";
}

$("#o_resume")?.addEventListener("input", () => {
  const v = $("#o_resume").value.trim();
  $("#o_resume_hint").textContent = v ? `已输入约 ${v.length} 字` : "";
});

$("#btnToggleGuide")?.addEventListener("click", () => {
  const b = $("#guideBody"), btn = $("#btnToggleGuide");
  const hidden = b.style.display === "none";
  b.style.display = hidden ? "" : "none";
  btn.textContent = hidden ? "收起" : "展开";
});

function renderRequirements(reqs) {
  const chip = (x, cls) => `<span class="chip ${cls}">${esc(x)}</span>`;
  const src = { knowledge_base: "岗位知识库", "knowledge_base+llm": "知识库 + AI", llm: "AI 分析", jd_only: "JD 提取" }[reqs.source] || reqs.source;
  $("#o_source").textContent = `分析来源：${src}`;
  let html = `<div class="reqbox"><div class="rb-title">🎯 岗位「${esc(reqs.role_label)}」核心要求</div>`;
  if (reqs.must_have?.length) html += `<div class="rb-line"><span class="rb-lab">硬性要求</span><div class="chips">${reqs.must_have.map((x) => chip(x, "req")).join("")}</div></div>`;
  if (reqs.nice_to_have?.length) html += `<div class="rb-line"><span class="rb-lab">加分项</span><div class="chips">${reqs.nice_to_have.map((x) => chip(x, "nice")).join("")}</div></div>`;
  if (reqs.responsibilities?.length) html += `<div class="rb-line"><span class="rb-lab">核心职责</span><div class="note">${reqs.responsibilities.map(esc).join("；")}</div></div>`;
  html += `</div>`;
  $("#o_reqs").innerHTML = html;
}

function renderMatch(m) {
  if (!m) { $("#o_match").innerHTML = ""; return; }
  const cov = m.coverage_pct ?? 0;
  const fit = m.score ?? cov;
  const cls = cov >= 70 ? "cov-ok" : cov >= 40 ? "cov-warn" : "cov-bad";
  let html = `<div class="matchbox">
    <div class="row"><b>简历与岗位匹配度</b><span class="spacer"></span><b style="font-size:20px">${cov}%</b></div>
    <div class="coverbar"><i class="${cls}" style="width:${cov}%"></i></div>
    <div class="note" style="margin-top:6px">综合匹配分（fit score）：<b>${fit}</b> / 100（越高越贴合岗位要求）</div>`;
  if (m.matched?.length) html += `<div class="chips" style="margin-top:8px">${m.matched.map((x) => `<span class="chip ok">✓ ${esc(x)}</span>`).join("")}</div>`;
  if (m.gaps?.length) html += `<div class="chips">${m.gaps.map((x) => `<span class="chip gap">✗ 缺 ${esc(x)}</span>`).join("")}</div>`;
  if (window._lastMissing?.length) {
    html += `<div class="note" style="margin-top:8px">🔑 缺失关键词（建议补进简历）：${window._lastMissing.map((x) => `<span class="chip gap">${esc(x)}</span>`).join("")}</div>`;
  }
  html += `</div>`;
  $("#o_match").innerHTML = html;
}

$("#btnAnalyze")?.addEventListener("click", async () => {
  const title = $("#o_title").value.trim();
  const msg = $("#o_msg");
  if (!title) { msg.textContent = "请先填写目标岗位"; msg.style.color = "#c0392b"; return; }
  msg.textContent = "分析岗位要求中…"; msg.style.color = "";
  try {
    const r = await api("POST", "/api/optimize/research", { title, company: $("#o_company").value.trim(), jd_text: $("#o_jd").value.trim() });
    if (!r.ok) { msg.textContent = "✗ " + (r.error || "分析失败"); msg.style.color = "#c0392b"; return; }
    $("#o_result").style.display = "";
    renderRequirements(r.requirements);
    window._lastMissing = [];
    renderMatch(null);
    $("#o_changes").innerHTML = ""; $("#o_suggestions").innerHTML = ""; $("#o_optimized").textContent = "";
    msg.textContent = "✓ 已列出该岗位要求，贴上简历后点「分析岗位并优化简历」即可改简历";
    msg.style.color = "#27ae60";
    $("#o_result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { msg.textContent = "✗ 请求失败：" + e; msg.style.color = "#c0392b"; }
});

$("#btnOptimize")?.addEventListener("click", async () => {
  const title = $("#o_title").value.trim();
  const resume_text = $("#o_resume").value.trim();
  const msg = $("#o_msg");
  if (!resume_text) { msg.textContent = "请先在左边粘贴你的简历"; msg.style.color = "#c0392b"; return; }
  if (!title) { msg.textContent = "请填写目标岗位"; msg.style.color = "#c0392b"; return; }
  const btn = $("#btnOptimize"); btn.disabled = true; const old = btn.textContent; btn.textContent = "优化中…（约需十几秒）";
  msg.textContent = "正在分析岗位要求并改写简历…"; msg.style.color = "";
  try {
    const r = await api("POST", "/api/optimize/run", { title, company: $("#o_company").value.trim(), jd_text: $("#o_jd").value.trim(), resume_text });
    if (!r.ok) { msg.textContent = "✗ " + (r.error || "优化失败"); msg.style.color = "#c0392b"; return; }
    _lastOptimized = { optimized_text: r.optimized_text, optimized_html: r.optimized_html };
    $("#o_result").style.display = "";
    renderRequirements(r.requirements);
    window._lastMissing = r.missing_keywords || [];
    renderMatch(r.match);
    $("#o_changes").innerHTML = (r.changes && r.changes.length) ? "<ul>" + r.changes.map((c) => `<li>${esc(c)}</li>`).join("") + "</ul>" : "（无）";
    $("#o_suggestions").innerHTML = (r.suggestions && r.suggestions.length) ? "<ul>" + r.suggestions.map((c) => `<li>${esc(c)}</li>`).join("") + "</ul>" : "（无）";
    $("#o_optimized").textContent = r.optimized_text || "";
    const via = r.method === "llm" ? "AI 改写" : "规则改写（开启 AI 效果更佳）";
    msg.textContent = `✓ 完成（${via}）。匹配度 ${r.match?.coverage_pct ?? 0}%`;
    msg.style.color = "#27ae60";
    $("#o_result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { msg.textContent = "✗ 请求失败：" + e; msg.style.color = "#c0392b"; }
  finally { btn.disabled = false; btn.textContent = old; }
});

$("#btnOpenResume")?.addEventListener("click", () => {
  if (!_lastOptimized) return;
  const w = window.open("", "_blank");
  w.document.write(_lastOptimized.optimized_html);
  w.document.close();
});
$("#btnCopyResume")?.addEventListener("click", async () => {
  if (!_lastOptimized) return;
  try { await navigator.clipboard.writeText(_lastOptimized.optimized_text); toast("已复制优化后简历文本"); }
  catch (e) { toast("复制失败，请手动选择文本复制"); }
});
$("#btnUseResume")?.addEventListener("click", async () => {
  if (!_lastOptimized) return;
  await api("POST", "/api/optimize/save-profile", { optimized_html: _lastOptimized.optimized_html });
  toast("已保存为你的简历，投递将使用优化版 ✓");
});

// ---------------- overview ----------------
async function loadOverview() {
  const [h, c] = await Promise.all([api("GET", "/api/health"), api("GET", "/api/profile/completeness")]);
  const counts = h.status_counts || {};
  const stats = [
    ["职位总数", h.jobs],
    ["匹配/草稿", (counts.matched || 0) + (counts.drafted || 0)],
    ["已批准", counts.approved || 0],
    ["已发送", counts.sent || 0],
  ];
  $("#statGrid").innerHTML = stats.map(([l, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  $("#completeness").innerHTML = `
    <div style="display:flex;justify-content:space-between;font-size:13px"><span>完整度</span><b>${c.score}%</b></div>
    <div class="bar"><i style="width:${c.score}%"></i></div>
    ${c.missing.length ? `<div class="note" style="margin-top:8px">待补充：${esc(c.missing.join("、"))}</div>` : `<div class="note" style="margin-top:8px;color:var(--ok)">资料已较完整 ✅</div>`}`;
  const llm = h.llm || {};
  const llmBadge = llm.enabled
    ? `<span class="pill approved">🤖 LLM 已启用（${esc(llm.provider || "")} / ${esc(llm.model || "")}）</span>`
    : `<span class="pill new">🤖 LLM 未启用（内容用模板，去设置开启）</span>`;
  $("#llmStatus").innerHTML = llmBadge;
  await loadAutopilot(h);
}

// ---------------- autopilot (全自动模式) ----------------
async function loadAutopilot(h) {
  if (!h) h = await api("GET", "/api/health");
  const s = await api("GET", "/api/autopilot/status");
  $("#ap_enabled").checked = !!s.enabled;
  $("#ap_auto_send").checked = !!s.auto_send;
  $("#ap_auto_chat").checked = !!s.auto_chat_reply;
  $("#ap_min").value = s.min_score ?? 75;
  $("#ap_cap").value = s.max_send_per_day ?? 30;
  renderApStatus(s);
}

function renderApStatus(s) {
  const on = s.enabled;
  const last = s.last_run;
  let txt = on
    ? `● 全自动模式运行中${s.auto_send ? "（含无人值守发邮件）" : "（发邮件保留人工确认）"}`
    : "○ 全自动模式未开启（开启后你无需再点任何按钮）";
  if (last && last.enabled) {
    txt += ` ｜ 上次运行：自动批准 ${last.approved ?? 0} 封、发送 ${last.sent ?? 0} 封、HR 回复采纳 ${last.chat_replies ?? 0} 条`;
  }
  $("#apStatus").textContent = txt;
}

async function saveAutopilot() {
  const enabled = $("#ap_enabled").checked;
  const payload = {
    autopilot: {
      enabled,
      auto_approve: true,
      auto_send: $("#ap_auto_send").checked,
      auto_chat_reply: $("#ap_auto_chat").checked,
      min_score: Number($("#ap_min").value) || 75,
      max_send_per_day: Number($("#ap_cap").value) || 30,
    },
  };
  await api("POST", "/api/settings", payload);
  const s = await api("GET", "/api/autopilot/status");
  renderApStatus(s);
  $("#apStatus").textContent += enabled ? " ｜ 已触发一次自动运行…" : "";
  if (enabled) {
    // give the kicked tick a moment, then refresh the status line
    setTimeout(async () => { const r = await api("GET", "/api/autopilot/status"); renderApStatus(r); loadOverview(); }, 2500);
  }
  loadOverview();
}

$("#ap_enabled").addEventListener("change", saveAutopilot);
$("#ap_auto_send").addEventListener("change", saveAutopilot);
$("#ap_auto_chat").addEventListener("change", saveAutopilot);
$("#ap_min").addEventListener("change", saveAutopilot);
$("#ap_cap").addEventListener("change", saveAutopilot);
$("#btnApRun").addEventListener("click", async () => {
  const r = await api("POST", "/api/autopilot/run");
  renderApStatus({ ...(await api("GET", "/api/autopilot/status")) });
  toast(`自动运行完成：批准 ${r.report?.approved ?? 0}，发送 ${r.report?.sent ?? 0}，HR 采纳 ${r.report?.chat_replies ?? 0}`);
  loadOverview();
});

// ---------------- profile ----------------
async function loadProfile() {
  const p = await api("GET", "/api/profile");
  $("#p_name").value = p.name || "";
  $("#p_title").value = p.title || "";
  $("#p_email").value = p.email || "";
  $("#p_phone").value = p.phone || "";
  $("#p_location").value = p.location || "";
  $("#p_years").value = p.years_experience || "";
  $("#p_smin").value = p.salary_min || "";
  $("#p_smax").value = p.salary_max || "";
  $("#p_skills").value = (p.skills || []).join(", ");
  $("#p_summary").value = p.summary || "";
  $("#p_resume").value = p.resume_html || "";
  $("#p_resume_data").value = Object.keys(p.resume_data || {}).length
    ? JSON.stringify(p.resume_data, null, 2) : "";
}
$("#btnSaveProfile").addEventListener("click", async () => {
  let resume_data = {};
  const raw = $("#p_resume_data").value.trim();
  if (raw) {
    try { resume_data = JSON.parse(raw); }
    catch (e) { toast("结构化履历 JSON 解析失败：" + e.message); return; }
  }
  const data = {
    name: $("#p_name").value, title: $("#p_title").value, email: $("#p_email").value,
    phone: $("#p_phone").value, location: $("#p_location").value,
    years_experience: Number($("#p_years").value) || 0,
    salary_min: Number($("#p_smin").value) || null, salary_max: Number($("#p_smax").value) || null,
    skills: $("#p_skills").value.split(",").map((s) => s.trim()).filter(Boolean),
    summary: $("#p_summary").value, resume_html: $("#p_resume").value,
    resume_data,
  };
  await api("POST", "/api/profile", data);
  $("#profileMsg").textContent = "已保存 ✓";
  setTimeout(() => ($("#profileMsg").textContent = ""), 2000);
  loadOverview();
});

// Fill the manual form from a parsed structured profile (user still reviews+saves)
function fillProfileForm(pr) {
  if (!pr) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
  set("p_name", pr.name); set("p_title", pr.title); set("p_email", pr.email);
  set("p_phone", pr.phone); set("p_location", pr.location);
  set("p_years", pr.years_experience); set("p_smin", pr.salary_min); set("p_smax", pr.salary_max);
  set("p_summary", pr.summary);
  if (Array.isArray(pr.skills)) $("#p_skills").value = pr.skills.join(", ");
  if (pr.resume_data && Object.keys(pr.resume_data).length) {
    $("#p_resume_data").value = JSON.stringify(pr.resume_data, null, 2);
  }
}
$("#btnParseResume").addEventListener("click", async () => {
  const text = $("#p_paste").value.trim();
  const msg = $("#parseMsg");
  if (!text) { msg.textContent = "请先粘贴简历文本"; msg.style.color = "#c0392b"; return; }
  msg.textContent = "解析中…"; msg.style.color = "";
  try {
    const r = await api("POST", "/api/profile/parse", { text });
    if (r.ok && r.profile) {
      fillProfileForm(r.profile);
      msg.textContent = `✓ 已解析填入（来源：${r.source === "llm" ? "AI" : "规则"}）。请核对后保存`;
      msg.style.color = "#27ae60";
    } else {
      msg.textContent = "✗ " + (r.error || "解析失败"); msg.style.color = "#c0392b";
    }
  } catch (e) { msg.textContent = "✗ 请求失败：" + e; msg.style.color = "#c0392b"; }
});
$("#btnExampleData").addEventListener("click", () => {
  $("#p_resume_data").value = JSON.stringify({
    educations: [{ school: "某某大学", degree: "本科", major: "计算机科学与技术", start: "2014", end: "2018" }],
    experiences: [
      { company: "A公司", title: "后端开发工程师", start: "2019", end: "2022",
        bullets: ["负责订单微服务，使用 Go + gRPC，QPS 提升 3 倍", "主导 Kubernetes 容器化迁移，发布效率提升 50%"] },
      { company: "B公司", title: "高级后端工程师", start: "2022", end: "2024",
        bullets: ["设计高并发分布式系统，支撑日均千万级请求", "搭建 CI/CD 与可观测性体系"] }
    ],
    projects: [{ name: "实时风控平台", role: "技术负责人", bullets: ["基于 Flink 实时计算", "接入 Kafka 消息队列"] }],
    certifications: ["AWS Certified Solutions Architect", "PMP"],
    languages: ["英语 CET-6", "普通话"],
    links: { github: "https://github.com/yourname", portfolio: "https://your.site" }
  }, null, 2);
  toast("已填入示例结构，按需修改后保存");
});
$("#btnPreview").addEventListener("click", () => window.open("/api/profile/resume", "_blank"));

// ---------------- jobs ----------------
async function loadJobs() {
  const { jobs } = await api("GET", "/api/jobs?limit=300");
  if (!jobs.length) { $("#jobsTable").innerHTML = `<div class="note">暂无职位，请点击「拉取职位」。</div>`; return; }
  $("#jobsTable").innerHTML = `<table><thead><tr><th>职位</th><th>公司</th><th>地点</th><th>薪资</th><th>来源</th><th>匹配</th><th></th></tr></thead><tbody>` +
    jobs.map((j) => `<tr>
      <td><b>${esc(j.title)}</b><br><a href="${esc(j.url)}" target="_blank" class="note">查看</a></td>
      <td>${esc(j.company)}</td><td>${esc(j.location)}</td><td>${esc(j.salary_text)}</td>
      <td><span class="note">${esc(j.source)}</span></td>
      <td><button class="ghost" data-draft="${j.id}">生成求职信</button></td>
    </tr>`).join("") + `</tbody></table>`;
  $$("[data-draft]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "生成中…";
    await api("POST", `/api/applications/${b.dataset.draft}/draft`, { use_llm: true });
    toast("已生成草稿，去「待投递」查看");
    loadOverview();
  }));
}

// ---------------- queue ----------------
async function loadQueue() {
  const f = $("#qFilter").value;
  const { applications } = await api("GET", "/api/applications" + (f ? `?status=${f}` : ""));
  const el = $("#queueList");
  if (!applications.length) { el.innerHTML = `<div class="note">暂无待投递项。先「匹配评分」并生成求职信。</div>`; return; }
  el.innerHTML = applications.map((a) => {
    let report = "";
    try { const sr = typeof a.screening_report === "string" ? JSON.parse(a.screening_report) : a.screening_report;
      if (sr && sr.coverage_pct != null) {
        const cls = sr.coverage_pct >= 70 ? "cov-ok" : sr.coverage_pct >= 40 ? "cov-warn" : "cov-bad";
        report = `<div class="note" style="margin:6px 0">
            <div>初筛匹配度：<b>${sr.coverage_pct}%</b>（JD 要求 ${sr.requirements?.length || 0} 项）</div>
            <div class="coverbar"><i class="${cls}" style="width:${sr.coverage_pct}%"></i></div>
            ${sr.matched?.length ? `<div class="chips">${sr.matched.map((m) => `<span class="chip ok">✓ ${esc(m)}</span>`).join("")}</div>` : ""}
            ${sr.gaps?.length ? `<div class="chips">${sr.gaps.map((g) => `<span class="chip gap">✗ 缺 ${esc(g)}</span>`).join("")}</div>` : ""}
          </div>`;
      }
    } catch (e) {}
    return `
    <div class="card" data-app="${a.id}">
      <div class="row">
        <input type="checkbox" class="q-check" style="width:auto;margin-right:6px" data-id="${a.id}">
        <b>${esc(a.title)}</b> <span class="note">@ ${esc(a.company)} · ${esc(a.location)}</span>
        <span class="spacer"></span>
        <span class="pill ${a.status}">${a.status}</span>
        <span class="note">分 ${a.match_score ?? 0}</span>
      </div>
      ${report}
      <div class="note" style="margin:4px 0">${esc(a.strengths || "")}</div>
      <div class="row" style="margin:6px 0">
        <button class="ghost q-resume" data-id="${a.id}">查看定制简历</button>
        <button class="ghost q-fill" data-id="${a.id}">填充建议</button>
      </div>
      <label>收件邮箱</label><input class="q-email" value="${esc(a.email_to || "")}" placeholder="recruiter@company.com">
      <label>求职信</label><textarea class="q-cl">${esc(a.cover_letter || "")}</textarea>
      <div class="row" style="margin-top:8px">
        <button class="ok q-approve">批准</button>
        <button class="q-send">批准并发送</button>
        <button class="ghost q-regenerate">重新生成</button>
        <button class="ghost q-blacklist">🚫 拉黑公司</button>
        <select class="q-status" style="width:auto">
          ${["new","matched","drafted","approved","sent","applied","interviewing","rejected","archived"].map((s) => `<option ${s===a.status?"selected":""}>${s}</option>`).join("")}
        </select>
      </div>
    </div>`;
  }).join("");

  $$("[data-app]").forEach((card) => {
    const id = Number(card.dataset.app);
    const getState = () => ({ email_to: $(".q-email", card).value, cover_letter: $(".q-cl", card).value });
    $(".q-approve", card).addEventListener("click", async () => {
      await api("POST", `/api/applications/${id}/approve`);
      toast("已批准 ✓"); loadQueue(); loadOverview();
    });
    $(".q-send", card).addEventListener("click", async () => {
      const st = getState();
      const r = await api("POST", `/api/applications/${id}/send`, st);
      toast(r.ok ? `已发送至 ${r.to}` : (r.error || "发送失败"));
      loadQueue(); loadOverview();
    });
    $(".q-regenerate", card).addEventListener("click", async () => {
      const job = (await api("GET", "/api/applications/" + id)).job_id;
      await api("POST", `/api/applications/${job}/draft`, { use_llm: true });
      toast("已重新生成"); loadQueue();
    });
    $(".q-status", card).addEventListener("change", async (e) => {
      await api("POST", `/api/applications/${id}/status`, { status: e.target.value });
      loadQueue(); loadOverview();
    });
    const rBtn = $(".q-resume", card), fBtn = $(".q-fill", card);
    if (rBtn) rBtn.addEventListener("click", () => window.open(`/api/applications/${id}/resume`, "_blank"));
    if (fBtn) fBtn.addEventListener("click", async () => {
      const fill = await api("GET", `/api/applications/${id}/fill`);
      openFillModal(fill);
    });
    const blBtn = $(".q-blacklist", card);
    if (blBtn) blBtn.addEventListener("click", async () => {
      const company = (await api("GET", "/api/applications/" + id)).company;
      if (!company) { toast("该申请无公司信息"); return; }
      if (!confirm(`确定把「${company}」加入黑名单吗？之后匹配会自动跳过它。`)) return;
      await api("POST", "/api/blacklist/auto", { kind: "company", value: company, reason: "手动拉黑（拒绝/不匹配）" });
      toast(`已拉黑 ${company} ✓`);
      loadQueue(); loadDashboard(); loadOverview();
    });
  });
}
$("#qFilter").addEventListener("change", loadQueue);

// ---- smart dispatch control bar ----
async function smartDraftAll() {
  toast("正在匹配+生成全部…");
  const r = await api("POST", "/api/applications/smart-draft");
  toast(`已匹配 ${r.matched ?? 0} / 草稿 ${r.drafted ?? 0}`);
  loadQueue(); loadOverview();
}
$("#btnSmartDraft").addEventListener("click", smartDraftAll);

$("#btnApproveAll").addEventListener("click", async () => {
  const min = Number($("#qMinScore").value) || 0;
  const r = await api("POST", "/api/applications/batch-approve", { min_score: min });
  $("#queueMsg").textContent = `已批准 ${r.approved} 个（≥${min}分）`;
  loadQueue(); loadOverview();
});
$("#btnApproveSelected").addEventListener("click", async () => {
  const ids = $$(".q-check:checked").map((c) => Number(c.dataset.id));
  if (!ids.length) { $("#queueMsg").textContent = "请先勾选"; return; }
  const r = await api("POST", "/api/applications/batch-approve", { ids });
  $("#queueMsg").textContent = `已批准 ${r.approved} 个`;
  loadQueue(); loadOverview();
});
$("#qSelectAll").addEventListener("change", (e) => {
  $$(".q-check").forEach((c) => (c.checked = e.target.checked));
});

// ---------------- fill-suggestion modal ----------------
function openFillModal(fill) {
  const order = ["求职意向", "期望城市", "自我评价", "核心技能", "工作经历", "项目经历", "教育背景"];
  const keys = order.filter((k) => k in fill).concat(Object.keys(fill).filter((k) => !order.includes(k)));
  $("#modalBody").innerHTML = keys.map((k) =>
    `<div class="fillrow"><label>${esc(k)}</label><div class="val">${esc(fill[k] || "（未填写）")}</div></div>`
  ).join("") + `<div class="note" style="margin-top:8px">把这些内容直接复制到招聘网站的对应申请字段即可。系统已按该岗位重排了最相关的经历与技能。</div>`;
  $("#modal").style.display = "flex";
}
$("#modalClose").addEventListener("click", () => ($("#modal").style.display = "none"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").style.display = "none"; });

// ---------------- sent ----------------
async function loadSent() {
  const { applications } = await api("GET", "/api/applications");
  const done = applications.filter((a) => ["sent", "applied", "interviewing", "rejected", "sent_failed"].includes(a.status));
  if (!done.length) { $("#sentList").innerHTML = `<div class="note">还没有投递记录。</div>`; return; }
  $("#sentList").innerHTML = `<table><thead><tr><th>职位</th><th>公司</th><th>收件人</th><th>状态</th><th>时间</th></tr></thead><tbody>` +
    done.map((a) => `<tr><td>${esc(a.title)}</td><td>${esc(a.company)}</td><td>${esc(a.email_to)}</td>
      <td><span class="pill ${a.status}">${a.status}</span></td><td class="note">${esc(a.sent_at || "-")}</td></tr>`).join("") + `</tbody></table>`;
}

// ---------------- chat (沟通助手) ----------------
let currentConv = null;
async function loadChat() {
  const [{ conversations }, fuRes] = await Promise.all([
    api("GET", "/api/chat/conversations"),
    api("GET", "/api/chat/needs-followup").catch(() => ({ conversations: [] })),
  ]);
  const fuIds = new Set((fuRes.conversations || []).map((c) => c.id));
  const el = $("#convList");
  const pending = conversations.filter((c) => (c.unread || 0) > 0).length;
  if (!conversations.length) {
    el.innerHTML = `<div class="note">还没有会话。点右上角「+ 模拟会话」体验自动拟回复，或安装 BOSS 直聘扩展让真实消息自动进来。</div>`;
    return;
  }
  const banner = pending
    ? `<div class="note" style="margin-bottom:8px;color:var(--ok)">📥 有 ${pending} 个会话有待回复；进入会话点「✅ 全部采纳」可批量批准，再去 BOSS 点发送。</div>`
    : "";
  const fuBanner = fuIds.size
    ? `<div class="note" style="margin-bottom:8px;color:#d97706">📌 有 ${fuIds.size} 个会话「已读不回」超过 ${FOLLOWUP_DAYS} 天，可进会话点「生成跟进话术」礼貌跟进。</div>`
    : "";
  el.innerHTML = banner + fuBanner + conversations.map((c) => `
    <div class="conv-item ${currentConv === c.id ? "active" : ""}" data-conv="${c.id}">
      <div class="ci-top"><b>${esc(c.recruiter_name || "HR")}</b>${c.unread ? `<span class="unread">${c.unread}</span>` : ""}${fuIds.has(c.id) ? `<span class="unread fu">📌</span>` : ""}</div>
      <div class="ci-job">${esc(c.job_title || "岗位未关联")} @ ${esc(c.company || "")}</div>
    </div>`).join("");
  $$("[data-conv]").forEach((item) => item.addEventListener("click", () => openConv(Number(item.dataset.conv))));
}

async function openConv(id) {
  currentConv = id;
  const { conversation: c, messages } = await api("GET", `/api/chat/conversations/${id}`);
  const tag = { boss: "BOSS 直聘", linkedin: "LinkedIn", generic: "其他" }[c.platform] || c.platform;
  const thread = messages.map((m) => {
    const isOut = m.direction === "outbound";
    const statusCls = isOut ? m.status : "";
    const time = (m.created_at || "").replace("T", " ").slice(0, 16);
    const actions = isOut
      ? (m.status === "drafted" ? `<button class="ghost mini" data-approve="${m.id}">批准</button>` : "")
        + (m.status === "approved" ? `<button class="ghost mini" data-sent="${m.id}">标记已发送</button>` : "")
      : "";
    return `<div class="msg ${m.direction === "inbound" ? "in" : "out"} ${statusCls}">
      ${esc(m.text)}
      <div class="m-meta">${isOut ? "我" : "HR"} · ${time}${isOut && m.status !== "sent" ? " · " + m.status : ""}</div>
      ${actions}
    </div>`;
  }).join("");
  const needsFu = needsFollowup(messages);
  $("#chatMain").innerHTML = `
    <div class="chat-head">
      <b>${esc(c.recruiter_name || "HR")}</b> <span class="note">· ${esc(c.job_title || "岗位未关联")} @ ${esc(c.company || "")}</span>
      <span class="spacer"></span><span class="pill warn" id="followBadge" style="display:${needsFu ? "" : "none"}">📌 待跟进 ${needsFu ? needsFu + "天" : ""}</span><span class="pill new">${tag}</span>
      <button class="ghost" id="btnAdoptAll" type="button">✅ 全部采纳</button>
    </div>
    <div class="chat-thread" id="thread">${thread || '<div class="note">还没有消息</div>'}</div>
    <div class="chat-compose">
      <div class="row">
        <button id="btnDraft">✨ 拟回复</button>
        <button class="ghost" id="btnFollowup" type="button">📌 生成跟进话术</button>
        <span class="spacer"></span><span id="intentTag" class="note"></span>
      </div>
      <textarea id="replyText" placeholder="点「拟回复」自动生成，或手动输入…"></textarea>
      <div class="row">
        <button id="btnSaveDraft">保存草稿</button>
        <button class="ok" id="btnApproveSend">批准并发送</button>
        <button class="ghost" id="btnSimMsg">模拟 HR 消息</button>
      </div>
      <div id="bridgeHint"></div>
    </div>`;
  $("#thread").scrollTop = $("#thread").scrollHeight;

  $("#btnDraft").addEventListener("click", async () => {
    $("#btnDraft").disabled = true; $("#btnDraft").textContent = "生成中…";
    const d = await api("POST", `/api/chat/${id}/draft`, { use_llm: true });
    $("#replyText").value = d.text;
    $("#intentTag").innerHTML = `意图：<span class="intent-tag">${esc(d.intent)}</span> 置信度 ${(d.confidence * 100).toFixed(0)}%`;
    $("#btnDraft").disabled = false; $("#btnDraft").textContent = "✨ 拟回复";
  });
  const adoptBtn = $("#btnAdoptAll");
  if (adoptBtn) adoptBtn.addEventListener("click", async () => {
    const r = await api("POST", `/api/chat/${id}/adopt-all`);
    toast(r.approved ? `已采纳 ${r.approved} 条，去 BOSS 点发送` : "没有待采纳的回复");
    openConv(id);
  });
  $("#btnSaveDraft").addEventListener("click", async () => {
    const text = $("#replyText").value.trim();
    if (!text) { toast("回复内容为空"); return; }
    await api("POST", `/api/chat/${id}/message`, { text, status: "drafted" });
    toast("已保存草稿"); openConv(id);
  });
  $("#btnApproveSend").addEventListener("click", async () => {
    const text = $("#replyText").value.trim();
    if (!text) { toast("请先写/生成回复"); return; }
    const r = await api("POST", `/api/chat/${id}/message`, { text, status: "approved" });
    const s = await api("POST", `/api/chat/${id}/send`, {});
    $("#bridgeHint").innerHTML = `<div class="bridge-hint">${esc(s.note || "已就绪")}</div>`;
    toast(s.ok ? "已就绪，去 BOSS 页面发送 ✓" : (s.error || "失败"));
    openConv(id);
  });
  $("#btnSimMsg").addEventListener("click", async () => {
    const samples = [
      "你好，看到你的简历挺合适的，方便聊聊吗？",
      "你期望薪资大概多少？",
      "你之前主要做什么方向的技术？几年经验了？",
      "方便发下简历吗？我想更详细看看。",
      "你这边大概什么时候能到岗？",
      "我们对你比较感兴趣，方便约个电话沟通下吗？",
    ];
    const text = samples[Math.floor(Math.random() * samples.length)];
    await api("POST", `/api/chat/${id}/simulate`, { text });
    toast("已模拟收到 HR 消息，点「拟回复」试试");
    openConv(id);
  });
  const fuBtn = $("#btnFollowup");
  if (fuBtn) fuBtn.addEventListener("click", async () => {
    fuBtn.disabled = true; const old = fuBtn.textContent; fuBtn.textContent = "生成中…";
    try {
      const d = await api("POST", `/api/chat/${id}/followup`, { use_llm: true });
      $("#replyText").value = d.text;
      $("#intentTag").innerHTML = `意图：<span class="intent-tag">跟进</span> 置信度 ${(d.confidence ? (d.confidence * 100).toFixed(0) : 95)}%`;
      toast("已生成跟进话术，检查后发送");
    } catch (e) { toast("生成失败：" + e); }
    finally { fuBtn.disabled = false; fuBtn.textContent = old; }
  });
  $$("[data-approve]").forEach((b) => b.addEventListener("click", async () => {
    await api("POST", `/api/chat/${id}/approve`, { message_id: Number(b.dataset.approve) });
    openConv(id);
  }));
  $$("[data-sent]").forEach((b) => b.addEventListener("click", async () => {
    await api("POST", `/api/chat/${id}/sent`, { message_id: Number(b.dataset.sent) });
    toast("已标记发送"); openConv(id);
  }));
}

$("#btnSimConv").addEventListener("click", async () => {
  // create a demo conversation from the first matched job if available
  let job = null;
  try { const { applications } = await api("GET", "/api/applications"); job = applications.find((a) => a.title); } catch (e) {}
  const body = {
    platform: "boss",
    recruiter_name: "李经理",
    job_title: job ? job.title : "后端开发工程师",
    company: job ? job.company : "某互联网公司",
    job_id: job ? job.job_id : null,
    text: "你好，看到你的简历挺合适的，方便聊聊吗？",
  };
  const r = await api("POST", "/api/chat/inbound", body);
  toast("已创建模拟会话");
  openConv(r.conversation.id);
});

// ---------------- dashboard (数据看板) ----------------
const STATUS_ORDER = ["new", "matched", "drafted", "approved", "sent", "applied", "interviewing", "rejected", "archived"];
const STATUS_LABEL = { new: "新建", matched: "已匹配", drafted: "已草稿", approved: "已批准", sent: "已发送", applied: "已投递", interviewing: "面试中", rejected: "已拒绝", archived: "已归档" };
const FOLLOWUP_DAYS = 3;

function daysSince(iso) {
  try { const t = new Date(iso); return Math.floor((Date.now() - t.getTime()) / 86400000); }
  catch (e) { return 999; }
}
function needsFollowup(messages) {
  if (!messages || !messages.length) return 0;
  const last = messages[messages.length - 1];
  if (last.direction !== "inbound") return 0;
  const hasOutAfter = messages.some((m) => m.direction === "outbound" && m.id > last.id);
  if (hasOutAfter) return 0;
  const age = daysSince(last.created_at);
  return age >= FOLLOWUP_DAYS ? age : 0;
}
function nextStatus(st) {
  const i = STATUS_ORDER.indexOf(st);
  if (i < 0 || i >= STATUS_ORDER.length - 1) return null;
  return STATUS_ORDER[i + 1];
}

async function loadDashboard() {
  const s = await api("GET", "/api/stats");
  const r = s.rates || {};
  const rates = [
    ["职位总数", s.jobs_total],
    ["发送率", (r.send_rate ?? 0) + "%"],
    ["面试率", (r.interview_rate ?? 0) + "%"],
    ["HR回复率", (r.reply_rate ?? 0) + "%"],
  ];
  $("#rateCards").innerHTML = rates.map(([l, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

  const f = s.funnel || {};
  const stages = [
    ["职位", f.jobs || 0, "#64748b"],
    ["匹配", f.matched || 0, "var(--ok)"],
    ["已批准", f.approved || 0, "#7c3aed"],
    ["已发送", f.sent || 0, "#2563eb"],
    ["面试中", f.interviewing || 0, "#d97706"],
    ["已拒绝", f.rejected || 0, "#c0392b"],
  ];
  const maxv = Math.max(1, ...stages.map((x) => x[1]));
  $("#funnel").innerHTML = stages.map(([l, v, col]) =>
    `<div class="frow"><div class="flab">${l}</div><div class="fbar"><i style="width:${(v / maxv * 100).toFixed(1)}%;background:${col}"></i></div><div class="fnum">${v}</div></div>`
  ).join("");

  renderKanban(s.by_status || {});
  await renderBlacklist();
  $("#dashUpdated").textContent = "实时数据";
}

function renderKanban(byStatus) {
  const cols = STATUS_ORDER.filter((st) => (byStatus[st] || []).length);
  if (!cols.length) {
    $("#kanban").innerHTML = `<div class="note">还没有投递记录。去「职位库」拉取职位并「匹配评分」后，这里会按状态显示看板。</div>`;
    return;
  }
  $("#kanban").innerHTML = cols.map((st) =>
    `<div class="kcol"><div class="khead">${STATUS_LABEL[st]} <span class="kn">${(byStatus[st] || []).length}</span></div><div class="kcards">` +
    (byStatus[st] || []).map((a) =>
      `<div class="kcard" data-id="${a.id}"><b>${esc(a.title)}</b><div class="note">@ ${esc(a.company)}</div>` +
      `<div class="krow"><span class="note">分 ${a.match_score ?? 0}</span>` +
      `<button class="ghost mini knext" data-id="${a.id}" data-status="${st}">→</button></div></div>`
    ).join("") + `</div></div>`
  ).join("");
  $$(".knext").forEach((b) => b.addEventListener("click", async () => {
    const id = Number(b.dataset.id);
    const nxt = nextStatus(b.dataset.status);
    if (!nxt) { toast("已是最终状态"); return; }
    await api("POST", `/api/applications/${id}/status`, { status: nxt });
    toast(`已推进到「${STATUS_LABEL[nxt]}」`);
    loadDashboard(); loadOverview();
  }));
}

async function renderBlacklist() {
  const { blacklist } = await api("GET", "/api/blacklist");
  if (!blacklist.length) {
    $("#blacklist").innerHTML = `<div class="note">暂无黑名单。当某家公司「已读不回 / 不匹配 / 你拒绝」时，可一键拉黑，之后匹配会自动跳过它。</div>`;
    return;
  }
  $("#blacklist").innerHTML = `<table><thead><tr><th>类型</th><th>内容</th><th>原因</th><th>来源</th><th></th></tr></thead><tbody>` +
    blacklist.map((b) => `<tr><td>${esc(b.kind)}</td><td><b>${esc(b.value)}</b></td><td class="note">${esc(b.reason || "-")}</td><td class="note">${esc(b.source)}</td><td><button class="ghost mini bl-del" data-id="${b.id}">移除</button></td></tr>`).join("") +
    `</tbody></table>`;
  $$(".bl-del").forEach((b) => b.addEventListener("click", async () => {
    await api("DELETE", `/api/blacklist/${b.dataset.id}`);
    toast("已移除黑名单"); renderBlacklist();
  }));
}

// ---------------- settings ----------------
let _llmProviders = [];

function _fillProviderOptions(selected) {
  const sel = $("#llm_provider");
  sel.innerHTML = "";
  for (const p of _llmProviders) {
    const o = document.createElement("option");
    o.value = p.key; o.textContent = p.label;
    if (p.key === selected) o.selected = true;
    sel.appendChild(o);
  }
}

function _fillModelOptions(providerKey, selectedModel, allowUnknown) {
  const p = _llmProviders.find(x => x.key === providerKey) || {};
  const sel = $("#llm_model");
  sel.innerHTML = "";
  const models = p.models || [];
  for (const m of models) {
    const o = document.createElement("option");
    o.value = m; o.textContent = m;
    if (m === selectedModel) o.selected = true;
    sel.appendChild(o);
  }
  // if saved model not in list, add it so it isn't lost (initial load only)
  if (allowUnknown && selectedModel && !models.includes(selectedModel)) {
    const o = document.createElement("option");
    o.value = selectedModel; o.textContent = selectedModel; o.selected = true;
    sel.appendChild(o);
  }
  if (!selectedModel || (!models.includes(selectedModel) && !(allowUnknown && selectedModel))) {
    sel.selectedIndex = 0;
  }
}

function _onProviderChange(allowUnknown) {
  const key = $("#llm_provider").value;
  const p = _llmProviders.find(x => x.key === key) || {};
  const prev = $("#llm_model").value;
  _fillModelOptions(key, prev || (p.models || [])[0], allowUnknown);
  if (!p.custom_endpoint) {
    $("#llm_base").value = p.base_url || "";
    $("#llm_base").placeholder = "";
    $("#llm_base_hint").textContent = "";
  } else {
    $("#llm_base_hint").textContent = "（自定义接入地址，需填写）";
  }
  const docEl = $("#llm_doc");
  docEl.innerHTML = p.doc ? `申请地址：<a href="${p.doc}" target="_blank" rel="noopener">${p.doc}</a>` : "";
  const keyEl = $("#llm_key");
  if (p.api_key_placeholder) keyEl.placeholder = p.api_key_placeholder;
}

async function loadSettings() {
  const s = await api("GET", "/api/settings");
  const sch = s.schedule || {}, smtp = s.smtp || {}, llm = s.llm || {}, safe = s.safety || {}, chatCfg = s.chat || {};
  $("#s_interval").value = sch.refresh_interval_minutes ?? 360;
  $("#s_minscore").value = sch.min_match_score ?? 60;
  $("#s_autodraft").checked = !!sch.auto_draft;
  $("#smtp_on").checked = !!smtp.enabled;
  $("#smtp_host").value = smtp.host || ""; $("#smtp_port").value = smtp.port ?? 465;
  $("#smtp_user").value = smtp.username || ""; $("#smtp_pass").value = smtp.password || "";
  $("#smtp_name").value = smtp.from_name || ""; $("#smtp_from").value = smtp.from_address || "";
  $("#smtp_tls").checked = !!smtp.use_tls;
  $("#safe_approve").checked = safe.require_human_approval !== false;
  $("#safe_rate").value = safe.max_send_per_hour ?? 10;
  $("#chat_assist").checked = !!chatCfg.assisted_auto_reply;

  // LLM providers
  try {
    const pr = await api("GET", "/api/llm/providers");
    _llmProviders = pr.providers || [];
  } catch (_) { _llmProviders = []; }
  const provKey = llm.provider || "openai";
  _fillProviderOptions(provKey);
  $("#llm_on").checked = !!llm.enabled;
  $("#llm_base").value = llm.base_url || "";
  $("#llm_key").value = llm.api_key || "";
  _onProviderChange(true);
  if (llm.model) $("#llm_model").value = llm.model;
}
$("#llm_provider").addEventListener("change", () => _onProviderChange(false));

$("#btnSaveSettings").addEventListener("click", async () => {
  const payload = {
    schedule: { refresh_interval_minutes: Number($("#s_interval").value), min_match_score: Number($("#s_minscore").value), auto_draft: $("#s_autodraft").checked },
    smtp: { enabled: $("#smtp_on").checked, host: $("#smtp_host").value, port: Number($("#smtp_port").value), username: $("#smtp_user").value, password: $("#smtp_pass").value, from_name: $("#smtp_name").value, from_address: $("#smtp_from").value, use_tls: $("#smtp_tls").checked },
    llm: { enabled: $("#llm_on").checked, provider: $("#llm_provider").value, api_key: $("#llm_key").value, base_url: $("#llm_base").value, model: $("#llm_model").value },
    safety: { require_human_approval: $("#safe_approve").checked, max_send_per_hour: Number($("#safe_rate").value), respect_robots_txt: true },
    chat: { assisted_auto_reply: $("#chat_assist").checked },
  };
  await api("POST", "/api/settings", payload);
  $("#settingsMsg").textContent = "已保存 ✓";
  setTimeout(() => ($("#settingsMsg").textContent = ""), 2000);
});

$("#btnTestLLM").addEventListener("click", async () => {
  const msg = $("#llmTestMsg");
  const cfg = { enabled: $("#llm_on").checked, provider: $("#llm_provider").value, api_key: $("#llm_key").value, base_url: $("#llm_base").value, model: $("#llm_model").value };
  if (!cfg.api_key) { msg.textContent = "请先填写 API Key"; msg.style.color = "#c0392b"; return; }
  msg.textContent = "测试中…"; msg.style.color = "";
  try {
    const r = await api("POST", "/api/llm/test", { llm: cfg });
    if (r.ok) { msg.textContent = `✓ 连通成功（${r.provider} / ${r.model}）`; msg.style.color = "#27ae60"; }
    else { msg.textContent = "✗ " + (r.error || "连接失败"); msg.style.color = "#c0392b"; }
  } catch (e) { msg.textContent = "✗ 请求失败：" + e; msg.style.color = "#c0392b"; }
});

// blacklist: add (static control, register once)
$("#bl_add")?.addEventListener("click", async () => {
  const value = $("#bl_value").value.trim();
  if (!value) { toast("请填写要拉黑的内容"); return; }
  await api("POST", "/api/blacklist", { kind: $("#bl_kind").value, value, reason: $("#bl_reason").value.trim() });
  $("#bl_value").value = ""; $("#bl_reason").value = "";
  toast("已加入黑名单，匹配时会自动跳过 ✓");
  renderBlacklist(); loadOverview();
});

// ---------------- quick actions ----------------
async function refreshJobs() {
  toast("正在拉取职位…");
  const r = await api("POST", "/api/jobs/refresh");
  toast(`拉取完成：新增 ${r.new_jobs} 个职位`);
  loadOverview(); loadJobs();
}
async function runMatch() {
  toast("正在匹配评分…");
  const r = await api("POST", "/api/match", { auto_draft: true });
  toast(`匹配完成：评估 ${r.evaluated} 个，匹配 ${r.matched} 个`);
  loadOverview(); loadQueue();
}
$("#btnRefresh").addEventListener("click", refreshJobs);
$("#btnMatch").addEventListener("click", runMatch);
$("#btnRefresh2").addEventListener("click", refreshJobs);
$("#btnMatch2").addEventListener("click", runMatch);

// init (优化页为默认首页)
loadOptimize();
