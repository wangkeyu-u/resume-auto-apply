// 简历助手 background（service worker）
// 职责：
//   - 与本地助手 API 通信（带可选 token）
//   - 处理 popup 的指令：抓取职位 / 自动填表 / 采纳聊天回复
//   - 接收 content 的 HR 消息，建会话映射
// 红线：只转发消息和「填空」，绝不自动点击发送/提交。

const DEFAULT_API = "http://127.0.0.1:8011";
let convMap = {}; // key: recruiter|job|company -> convId

chrome.storage.local.get(["apiBase", "apiToken", "convMap"], (d) => {
  if (d.convMap) convMap = d.convMap;
});
function save() { chrome.storage.local.set({ convMap, apiBase: lastBase, apiToken: lastToken }); }
let lastBase = DEFAULT_API, lastToken = "";

async function api(path, opts = {}) {
  const base = (await chrome.storage.local.get("apiBase")).apiBase || DEFAULT_API;
  lastBase = base;
  const token = (await chrome.storage.local.get("apiToken")).apiToken || "";
  lastToken = token;
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token) headers["X-API-Token"] = token;
  const r = await fetch(base + path, Object.assign({ headers }, opts));
  if (!r.ok) throw new Error("API " + r.status);
  return r.json();
}

// 确保 content 脚本已注入（官网投递页未常驻，用 scripting 注入；幂等）
async function sendToTab(tabId, msg) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  } catch (e) { /* 常驻页面已注入，忽略 */ }
  return chrome.tabs.sendMessage(tabId, msg);
}

// ---- HR 消息入站（来自 content 的 BOSS 聊天扫描）----
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "inbound") {
    api("/api/chat/inbound", { method: "POST", body: JSON.stringify(msg) })
      .then((res) => {
        const key = `${msg.recruiter_name}|${msg.job_title}|${msg.company}`;
        convMap[key] = res.conversation.id;
        save();
      })
      .catch(() => {});
    return;
  }
  if (msg.type === "markSent" && msg.convId) {
    api(`/api/chat/${msg.convId}/sent`, { method: "POST", body: JSON.stringify({ message_id: msg.messageId }) })
      .catch(() => {});
    return;
  }
  // popup 指令
  handlePopup(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true; // 异步响应
});

async function handlePopup(msg) {
  if (msg.type === "config") {
    const d = await chrome.storage.local.get(["apiBase", "apiToken"]);
    let status = "offline";
    try { const r = await fetch((d.apiBase || DEFAULT_API) + "/api/health"); if (r.ok) status = "online"; } catch {}
    return { ok: true, apiBase: d.apiBase || DEFAULT_API, apiToken: d.apiToken || "", status };
  }

  if (msg.type === "setConfig") {
    await chrome.storage.local.set({ apiBase: msg.apiBase, apiToken: msg.apiToken });
    lastBase = msg.apiBase; lastToken = msg.apiToken;
    return { ok: true };
  }

  if (msg.type === "scrape") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const res = await sendToTab(tab.id, { type: "scrapeJobs" });
    const jobs = (res && res.jobs) || [];
    if (!jobs.length) return { ok: true, imported: 0, skipped: 0, matched: 0, note: "未抓到职位，请确认在招聘网站『职位列表页』" };
    const r = await api("/api/jobs/import-bulk", { method: "POST", body: JSON.stringify({ jobs }) });
    return { ok: true, ...r, note: `已导入 ${r.imported} 个，自动匹配 ${r.matched} 个` };
  }

  if (msg.type === "fill") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const info = await sendToTab(tab.id, { type: "pageInfo" });
    const pi = (info && info.info) || {};
    // 用页面职位名/公司匹配本系统里的 job
    let job = null;
    try { job = await api(`/api/jobs/find?title=${encodeURIComponent(pi.title || "")}&company=${encodeURIComponent(pi.company || "")}`); } catch {}
    if (!job || !job.found) {
      return { ok: false, error: "本系统还没有这个职位，请先在『职位列表页』点『抓取职位』。" };
    }
    const [fill, profile] = await Promise.all([
      api(`/api/jobs/${job.job_id}/fill`),
      api("/api/profile"),
    ]);
    const fields = {
      name: profile.name,
      email: profile.email,
      phone: profile.phone,
      intention: fill["求职意向"],
      city: fill["期望城市"],
      self_eval: fill["自我评价"],
      skills: fill["核心技能"],
      experience: fill["工作经历"],
      project: fill["项目经历"],
      education: fill["教育背景"],
    };
    const r = await sendToTab(tab.id, { type: "fillForm", fields });
    return { ok: true, filled: (r && r.filled) || [], job: job.title };
  }

  if (msg.type === "adoptChat") {
    // 找有 pending outbound 的会话，填入当前 BOSS 聊天页
    const convs = await api("/api/chat/conversations");
    let target = null;
    for (const c of (convs.conversations || [])) {
      try {
        const ob = await api(`/api/chat/${c.id}/outbound`);
        if (ob.pending) { target = { convId: c.id, text: ob.text }; break; }
      } catch {}
    }
    if (!target) return { ok: false, error: "没有待填入的回复，请先在『沟通』页点『拟回复』并『批准』。" };
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const r = await sendToTab(tab.id, { type: "fillChat", text: target.text });
    return { ok: true, filled: !!(r && r.ok), convId: target.convId };
  }

  return { ok: false, error: "unknown command" };
}
