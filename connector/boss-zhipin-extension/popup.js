// popup 逻辑：连接检测 + 四个操作按钮
const $ = (id) => document.getElementById(id);

function setMsg(text, kind) {
  const el = $("msg");
  el.textContent = text;
  el.className = "msg" + (kind ? " " + kind : "");
}

async function send(cmd) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return chrome.runtime.sendMessage(Object.assign({ type: cmd }, tab ? { tabId: tab.id } : {}));
}

async function refresh() {
  const d = await chrome.storage.local.get(["apiBase", "apiToken"]);
  $("apiBase").value = d.apiBase || "http://127.0.0.1:8011";
  $("apiToken").value = d.apiToken || "";
  chrome.runtime.sendMessage({ type: "config" }, (r) => {
    const s = $("status");
    if (r && r.status === "online") { s.textContent = "连接状态：已连接 ✓"; s.className = "status online"; }
    else { s.textContent = "连接状态：未连接（请先启动简历助手）"; s.className = "status offline"; }
  });
}

$("saveCfg").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "setConfig", apiBase: $("apiBase").value.trim(), apiToken: $("apiToken").value.trim() });
  setMsg("配置已保存", "ok");
  refresh();
});

$("scrape").addEventListener("click", async () => {
  setMsg("正在抓取…", "");
  const r = await send("scrape");
  if (r && r.ok) setMsg(`✓ ${r.note}`, "ok");
  else setMsg("✗ " + ((r && r.error) || "抓取失败"), "err");
});

$("fill").addEventListener("click", async () => {
  setMsg("正在匹配并填入…", "");
  const r = await send("fill");
  if (r && r.ok) setMsg(`✓ 已填入「${r.job}」：${r.filled.join("、")}`, "ok");
  else setMsg("✗ " + ((r && r.error) || "填入失败"), "err");
});

$("adopt").addEventListener("click", async () => {
  setMsg("正在采纳回复…", "");
  const r = await send("adoptChat");
  if (r && r.ok) setMsg("✓ 已把拟好的回复填进输入框，请检查后点 BOSS 的发送", "ok");
  else setMsg("✗ " + ((r && r.error) || "无待填入回复"), "err");
});

refresh();
