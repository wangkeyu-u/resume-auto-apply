# 简历自动投递助手 · 竞品调研与升级规划

> 调研时间：2026-07-12 ｜ 目的：参考网上同类开源项目，规划本项目的下一轮迭代

## 一、对标项目清单（真实可访问）

| 项目 | 技术栈 | 核心能力 | 地址 | 亮点 |
|---|---|---|---|---|
| feder-cr/Auto_Jobs_Applier_AIHawk | Python + Selenium | LinkedIn Easy Apply 自动化 + AI 表单 + 动态简历 | github.com/feder-cr/Auto_Jobs_Applier_AIHawk | 经典项目（已 archive，曾因 LinkedIn 投诉下架） |
| GodsScion/Auto_job_applier_linkedIn | Python + undetected-chromedriver | LinkedIn/Indeed 抓取、JD 定制简历、双引擎、Flask 看板 | github.com/GodsScion/Auto_job_applier_linkedIn | 看板 + 多引擎 |
| srbhr/Resume-Matcher | TS+Python | Master Resume、向量相似度打分、关键词高亮、本地 Ollama | github.com/srbhr/Resume-Matcher | 匹配度打分 + 缺失关键词（参考） |
| AmruthPillai/Reactive-Resume | 全栈 | 简历单一可信源 + PDF 导出 | github.com/AmruthPillai/Reactive-Resume | 底座 |
| jolie-z/Auto-JobHunter | FastAPI + LangGraph + DrissionPage | BOSS/猎聘/51job 抓取→评估→RPA 静默投递、自然语言调度、一票否决黑名单 | github.com/jolie-z/Auto-JobHunter | 工业级、NL 调度、黑名单 |
| geekgeekrun/geekgeekrun | Puppeteer + Electron | BOSS 专用、自动开聊、已读不回提醒、达上限暂停 60 分钟 | github.com/geekgeekrun/geekgeekrun | 已读不回提醒 + 限速（重点参考） |
| loks666/get_jobs | Java + Selenium | BOSS/51job/猎聘/拉勾/智联 全平台、投递后自动更新黑名单 | github.com/loks666/get_jobs | 多平台 + 黑名单沉淀 |
| towenjian/AutoGetJob | Python + DrissionPage | BOSS+51job、AI 匹配度、自动发好友请求+个性化消息 | github.com/towenjian/AutoGetJob | AI 匹配 + 复聊 |

商业参考：Rezi（0–100 ATS 分/23 因素）、Teal（插件抓岗 + 看板追踪）、Jobscan（JD 匹配率打分）。

## 二、功能矩阵（✓有 / △部分 / —无）

| 能力 | 本项目现状 | 对标普遍水平 |
|---|---|---|
| JD 抓取/解析 | ✓ 扩展抓取 + demo | ✓ |
| 简历解析(PDF→结构化) | ✓ 一键解析 | ✓ |
| 简历-JD 匹配度打分 | ✓ 可解释 0-100 | ✓ |
| 一键简历定制改写 | ✓ 优化器 | ✓ |
| 多平台自动投递 | △ 仅 BOSS 桥接 | ✓ 多平台 |
| 投递状态追踪/看板 | △ 仅有列表，无看板/漏斗 | ✓ Teal/JobHunter |
| 投递节奏控制/防封 | △ 每日上限+每小时限速 | ✓ 随机延时+达上限暂停 |
| 邮件通道 | ✓ SMTP 全自动+人工确认 | — |
| 候选人画像/偏好 | ✓ | ✓ |
| 数据看板(投递数/回复率) | ✗ 缺 | ✓ JobHunter/Teal |
| AI 复聊话术 | △ 有草稿 | ✓ geekgeekrun |
| 已读不回跟进提醒 | ✗ 缺 | ✓ geekgeekrun |
| 企业/岗位黑名单沉淀 | ✗ 缺 | ✓ get_jobs/JobHunter |

## 三、本轮迭代范围（高价值 + 可行）

1. **投递数据看板 + 状态看板（Kanban）** — 新增 `/api/stats`；新建「数据看板」页：投递漏斗、发送/面试/回复率、按状态看板可点击推进。
2. **黑名单自动沉淀** — 新增 `blacklist` 表；`已读不回/不匹配/拒绝`自动拉黑公司；匹配时自动跳过黑名单公司；设置页可管理。
3. **已读不回跟进提醒 + 复聊话术** — 检测"已读不回"（HR 发消息后 N 天无己方回复）；一键生成跟进话术；沟通页加提醒徽标。
4. **匹配强化（轻量）** — 优化器结果补 `fit_score`(0-100) 与 `missing_keywords`，页面高亮缺口关键词。

## 四、风险与合规（必须保留）

- 不替用户在 BOSS 代点"发送"；邮件通道可全自动。
- 自动投递/聊天保持"半自动+人工确认"，避免封号与违反 ToS。
- 限速、每日上限、黑名单属于"保护账号 + 提升回复质量"的合规设计，保留并强化。
