// 简历助手 content script —— 自动填表 / 抓取职位 / 填入聊天回复
//
// 设计红线（务必遵守）：
//   1. 本脚本只『读取 DOM』和『填写输入框』，绝不自动点击「发送 / 提交」。
//      最后那一击永远是你本人（平台也强制真人操作，且自动点会触发反爬封号）。
//   2. 填表不依赖写死的选择器：按表单字段的 label / placeholder / name 文本自动匹配，
//      因此对企业官网等任意投递页也能用，无需为每个站点单独配选择器。
//
// 招聘站页面结构会变，下面 SELECTORS 是 2024 年左右的参考，若抓取为空请用 F12 核对。

if (!window.__raaReady) {
  window.__raaReady = true;

  // ---------------------------------------------------------------------------
  // 平台识别
  // ---------------------------------------------------------------------------
  function detectPlatform() {
    const h = location.hostname;
    if (h.includes("zhipin.com") || h.includes("kanzhun.com")) return "boss";
    if (h.includes("liepin.com")) return "liepin";
    if (h.includes("lagou.com")) return "lagou";
    if (h.includes("linkedin.com")) return "linkedin";
    if (h.includes("zhaopin.com")) return "zhaopin";
    if (h.includes("51job.com")) return "job51";
    return "generic";
  }

  // 各招聘站列表页「职位卡片」选择器（抓职位用）。识别不到时回退到通用启发式。
  const CARD_SELECTORS = {
    boss: ".job-card-wrapper",
    liepin: ".job-card",
    lagou: ".position",
    linkedin: ".jobs-search-results__list-item, .base-card",
    zhaopin: ".joblist-box__item",
    job51: ".j_joblist",
  };

  // 从单个卡片里取字段
  const FIELD_PARSERS = {
    boss: {
      title: (n) => txt(n, ".job-name a") || txt(n, ".job-name"),
      company: (n) => txt(n, ".company-name"),
      location: (n) => txt(n, ".city-link") || txt(n, ".job-area"),
      salary: (n) => txt(n, ".salary"),
      url: (n) => href(n, ".job-name a") || href(n, "a"),
    },
    liepin: {
      title: (n) => txt(n, ".job-title-link") || txt(n, "a"),
      company: (n) => txt(n, ".company-name"),
      location: (n) => txt(n, ".job-location"),
      salary: (n) => txt(n, ".job-salary"),
      url: (n) => href(n, "a"),
    },
    lagou: {
      title: (n) => txt(n, ".position-link") || txt(n, "a"),
      company: (n) => txt(n, ".company-name"),
      location: (n) => txt(n, ".add"),
      salary: (n) => txt(n, ".money"),
      url: (n) => href(n, "a"),
    },
    linkedin: {
      title: (n) => txt(n, ".base-search-card__title") || txt(n, "h3"),
      company: (n) => txt(n, ".base-search-card__subtitle") || txt(n, "h4"),
      location: (n) => txt(n, ".job-search-card__location"),
      salary: (n) => txt(n, ".job-search-card__salary-info"),
      url: (n) => href(n, "a"),
    },
    zhaopin: {
      title: (n) => txt(n, ".jobinfo__name") || txt(n, "a"),
      company: (n) => txt(n, ".companyinfo__name"),
      location: (n) => txt(n, ".jobinfo__other-info-tag"),
      salary: (n) => txt(n, ".jobinfo__salary"),
      url: (n) => href(n, "a"),
    },
    job51: {
      title: (n) => txt(n, ".jname") || txt(n, "a"),
      company: (n) => txt(n, ".cname"),
      location: (n) => txt(n, ".d at"),
      salary: (n) => txt(n, ".salary"),
      url: (n) => href(n, "a"),
    },
  };

  // ---------------------------------------------------------------------------
  // 小工具
  // ---------------------------------------------------------------------------
  function txt(root, sel) {
    const el = root.querySelector(sel);
    return el ? (el.innerText || el.textContent || "").trim() : "";
  }
  function href(root, sel) {
    const el = root.querySelector(sel);
    return el ? (el.href || "") : "";
  }
  function absoluteUrl(u) {
    if (!u) return "";
    try { return new URL(u, location.href).href; } catch { return u; }
  }

  // ---------------------------------------------------------------------------
  // 抓取列表页职位
  // ---------------------------------------------------------------------------
  function scrapeJobs() {
    const platform = detectPlatform();
    const jobs = [];
    let cards = [];
    const cardSel = CARD_SELECTORS[platform];
    if (cardSel) cards = Array.from(document.querySelectorAll(cardSel));

    if (cards.length) {
      const parser = FIELD_PARSERS[platform] || {};
      for (const c of cards) {
        const title = parser.title ? parser.title(c) : "";
        if (!title) continue;
        jobs.push({
          title,
          company: parser.company ? parser.company(c) : "",
          location: parser.location ? parser.location(c) : "",
          salary_text: parser.salary ? parser.salary(c) : "",
          url: absoluteUrl(parser.url ? parser.url(c) : ""),
          platform,
        });
      }
    }

    // 通用回退：若平台卡片选择器没命中，扫描所有看起来像职位的链接
    if (!jobs.length) {
      for (const a of Array.from(document.querySelectorAll("a[href]"))) {
        const t = (a.innerText || a.textContent || "").trim();
        if (t.length > 3 && t.length < 60 && /(工程师|开发|经理|专员|助理|设计师|架构|专家|分析师|运营|产品|实习)/.test(t)) {
          // 尝试在同卡片里找公司名
          let card = a;
          for (let i = 0; i < 4 && card; i++) card = card.parentElement;
          const comp = card ? (card.querySelector("[class*='company']")?.innerText || "").trim() : "";
          jobs.push({ title: t, company: comp, url: absoluteUrl(a.href), platform: "generic" });
        }
      }
    }
    return jobs.slice(0, 60);
  }

  // ---------------------------------------------------------------------------
  // 当前页职位信息（apply 页匹配用）
  // ---------------------------------------------------------------------------
  function pageJobInfo() {
    const platform = detectPlatform();
    let title = "", company = "";
    if (platform === "boss" || platform === "linkedin") {
      title = (document.querySelector(".job-title, .job-name, h1")?.innerText || "").trim();
      company = (document.querySelector(".company-name, [class*='company']")?.innerText || "").trim();
    } else {
      title = (document.querySelector("h1, .job-title, .position-title")?.innerText || document.title || "").trim();
      company = (document.querySelector(".company-name, [class*='company']")?.innerText || "").trim();
    }
    return { title, company, platform, url: location.href };
  }

  // ---------------------------------------------------------------------------
  // 自动填表：按 label/placeholder 文本匹配字段（核心，不依赖写死选择器）
  // ---------------------------------------------------------------------------
  // 字段关键词 -> 在表单里找匹配的 input/textarea
  const FIELD_KEYWORDS = {
    name: ["姓名", "名字", "name", "您的姓名"],
    email: ["邮箱", "电子邮件", "email", "e-mail", "mail"],
    phone: ["电话", "手机", "联系", "phone", "mobile", "tel"],
    intention: ["求职意向", "应聘岗位", "期望职位", "申请职位", "目标岗位", "position"],
    city: ["城市", "地点", "期望城市", "工作地", "city", "location", "地区"],
    self_eval: ["自我评价", "自我介绍", "个人简介", "summary", "about", "profile"],
    skills: ["技能", "特长", "专业技能", "skills", "expertise", "能力"],
    experience: ["工作经历", "工作经验", "实习经历", "experience", "work"],
    project: ["项目经历", "项目经验", "project"],
    education: ["教育背景", "教育经历", "学历", "学校", "education", "school"],
  };

  function fieldLabel(el) {
    // 汇总一个 input 的所有可识别文本：label 文本、placeholder、name、id、aria
    const bits = [];
    const id = el.id || "";
    if (id) bits.push(id);
    if (el.name) bits.push(el.name);
    if (el.placeholder) bits.push(el.placeholder);
    if (el.getAttribute("aria-label")) bits.push(el.getAttribute("aria-label"));
    if (el.getAttribute("aria-placeholder")) bits.push(el.getAttribute("aria-placeholder"));
    // 相邻 <label>
    const lbl = el.closest("label");
    if (lbl) bits.push(lbl.innerText || lbl.textContent || "");
    // 前一个 label[for] 或紧邻文本节点/兄弟 label
    if (id) {
      const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l) bits.push(l.innerText || l.textContent || "");
    }
    // 父容器里最近的一个 label/标题
    let p = el.parentElement;
    for (let i = 0; i < 3 && p; i++) {
      const lab = p.querySelector("label");
      if (lab) { bits.push(lab.innerText || lab.textContent || ""); break; }
      p = p.parentElement;
    }
    return bits.join(" ").toLowerCase();
  }

  function findField(keywords) {
    const els = Array.from(document.querySelectorAll("input, textarea, select, [contenteditable='true']"));
    let best = null, bestScore = 0;
    for (const el of els) {
      if (el.type === "hidden" || el.type === "submit" || el.type === "button") continue;
      const label = fieldLabel(el);
      if (!label) continue;
      for (const kw of keywords) {
        if (label.includes(kw.toLowerCase())) {
          // 越短的关键词命中越精确，给更高分
          const score = 10 - Math.min(kw.length, 9);
          if (score > bestScore) { bestScore = score; best = el; }
        }
      }
    }
    return best;
  }

  function setValue(el, value) {
    if (!el || !value) return;
    const v = String(value);
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      el.innerText = v;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function fillForm(fields) {
    const filled = [];
    for (const [key, keywords] of Object.entries(FIELD_KEYWORDS)) {
      const value = fields[key];
      if (!value) continue;
      const el = findField(keywords);
      if (el) { setValue(el, value); filled.push(key); }
    }
    // 工作经历/项目/教育可能在一个大 textarea 里，单独处理（若上面的精确匹配没填到）
    const longFields = { experience: fields.experience, project: fields.project, education: fields.education };
    for (const [key, value] of Object.entries(longFields)) {
      if (!value || filled.includes(key)) continue;
      const el = findField(FIELD_KEYWORDS[key]);
      if (el) { setValue(el, value); filled.push(key); }
    }
    return filled;
  }

  // ---------------------------------------------------------------------------
  // 填入 BOSS 聊天输入框
  // ---------------------------------------------------------------------------
  function fillChat(text) {
    const input =
      document.querySelector(".chat-input, .input-area textarea, textarea[class*='chat']") ||
      document.querySelector("textarea, [contenteditable='true']");
    if (!input) return false;
    setValue(input, text);
    return true;
  }

  // ---------------------------------------------------------------------------
  // 指令分发
  // ---------------------------------------------------------------------------
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      if (msg.type === "scrapeJobs") {
        sendResponse({ ok: true, jobs: scrapeJobs() });
      } else if (msg.type === "pageInfo") {
        sendResponse({ ok: true, info: pageJobInfo() });
      } else if (msg.type === "fillForm") {
        const filled = fillForm(msg.fields || {});
        sendResponse({ ok: true, filled });
      } else if (msg.type === "fillChat") {
        const ok = fillChat(msg.text || "");
        sendResponse({ ok });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
    return true; // 保持异步通道
  });

  // ---------------------------------------------------------------------------
  // BOSS 聊天：仅【读取】HR 消息并送进本地助手（绝不自动回复/自动发送）
  // ---------------------------------------------------------------------------
  let knownMsgs = new Set();
  function startChatScan() {
    const h = location.hostname;
    if (!h.includes("zhipin.com") && !h.includes("kanzhun.com")) return;
    if (window.__raaChatStarted) return;
    window.__raaChatStarted = true;
    const SEL = {
      messages: ".msg, .chat-message, [class*='message']",
      sender: ".name, [class*='name']",
      jobTitle: ".job-title, [class*='jobTitle'], .title",
      company: ".company, [class*='company']",
    };
    function scan() {
      document.querySelectorAll(SEL.messages).forEach((n) => {
        if (knownMsgs.has(n)) return;
        knownMsgs.add(n);
        const text = (n.innerText || "").trim();
        if (!text) return;
        const isMe = n.classList.contains("self") || n.getAttribute("data-side") === "right";
        if (isMe) return;
        chrome.runtime.sendMessage({
          type: "inbound",
          recruiter_name: (n.querySelector(SEL.sender)?.innerText || "").trim() || "HR",
          job_title: (document.querySelector(SEL.jobTitle)?.innerText || "").trim(),
          company: (document.querySelector(SEL.company)?.innerText || "").trim(),
          text,
        });
      });
    }
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
    scan();
  }
  startChatScan();
}
