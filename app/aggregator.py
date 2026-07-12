"""Job aggregator.

Supports three source types, all config-driven (see config/defaults.json):
  - "demo": built-in sample jobs so the system runs out-of-the-box for testing
  - "rss" : fetch & parse an RSS/Atom feed of job postings
  - "http" : fetch a JSON endpoint that returns a list of jobs (custom adapter)

All fetching is rate-limited and time-boxed. No scraping of sites that forbid it.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from . import database as db

USER_AGENT = "ResumeAutoApply/1.0 (+personal job-search assistant)"
HTTP_TIMEOUT = 15
MIN_REQUEST_GAP = 1.0  # seconds between network requests (polite)


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _demo_jobs(limit: int = 20) -> list[dict]:
    samples = [
        ("后端开发工程师", "云启科技", "深圳", "Python/Go，微服务，Kubernetes", 25000, 40000),
        ("全栈工程师", "星河网络", "上海", "React，Node.js，TypeScript", 22000, 35000),
        ("数据工程师", "数擎智能", "北京", "Spark，Flink，SQL，Python", 28000, 45000),
        ("前端开发工程师", "微光互动", "杭州", "React/Vue，TypeScript，可视化", 20000, 32000),
        ("DevOps 工程师", "磐石云", "远程", "K8s，Terraform，CI/CD", 26000, 42000),
        ("机器学习工程师", "深智研究院", "北京", "PyTorch，LLM，NLP", 35000, 60000),
        ("初级软件工程师", "新芽科技", "成都", "Java，Spring Boot", 12000, 18000),
        ("高级后端工程师", "潮汐科技", "广州", "Go，分布式系统，gRPC", 30000, 50000),
        ("测试开发工程师", "质行软件", "南京", "自动化测试，Python，Pytest", 18000, 28000),
        ("技术产品经理", "蓝图科技", "上海", "B端产品，数据驱动", 25000, 40000),
        ("高级前端工程师", "织云科技", "深圳", "性能优化，架构，微前端", 30000, 48000),
        ("云原生工程师", "御风云", "远程", "容器，Service Mesh，可观测性", 28000, 45000),
    ]
    out = []
    for i, (title, company, loc, skills, sm, sx) in enumerate(samples[:limit]):
        out.append({
            "source": "demo",
            "source_id": f"demo-{i}",
            "title": title,
            "company": company,
            "location": loc,
            "url": f"https://example.jobs/{i}",
            "description": (
                f"{company} 正在招聘 {title}（{loc}）。\n"
                f"岗位要求：熟悉 {skills}。\n"
                f"我们提供有竞争力的薪资、弹性办公与成长空间。"
            ),
            "salary_text": f"¥{sm//1000}k-{sx//1000}k",
            "salary_min": sm,
            "salary_max": sx,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        })
    return out


def _parse_rss(text: str, source_id: str, limit: int = 30) -> list[dict]:
    root = ET.fromstring(text)
    out = []
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        out.append({
            "source": source_id,
            "source_id": link or title,
            "title": title,
            "company": "",
            "location": "",
            "url": link,
            "description": desc,
            "salary_text": "",
            "salary_min": None,
            "salary_max": None,
            "posted_at": pub or datetime.now(timezone.utc).isoformat(),
        })
    # Atom
    if not out:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("a:title", namespaces=ns) or "").strip()
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            content = entry.findtext("a:content", namespaces=ns) or entry.findtext("a:summary", namespaces=ns) or ""
            out.append({
                "source": source_id,
                "source_id": link or title,
                "title": title,
                "company": "",
                "location": "",
                "url": link,
                "description": content.strip(),
                "salary_text": "",
                "salary_min": None,
                "salary_max": None,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            })
    return out[:limit]


def fetch_source(cfg: dict) -> tuple[list[dict], str]:
    """Return (jobs, note). Never raises — returns ([], error) on failure."""
    stype = cfg.get("type")
    try:
        if stype == "demo":
            return _demo_jobs(int(cfg.get("limit", 20))), "ok"
        if stype == "rss":
            time.sleep(MIN_REQUEST_GAP)
            text = _get(cfg["url"])
            return _parse_rss(text, cfg.get("id", "rss"), int(cfg.get("limit", 30))), "ok"
        if stype == "http":
            time.sleep(MIN_REQUEST_GAP)
            text = _get(cfg["url"])
            data = json.loads(text)
            return data if isinstance(data, list) else data.get("jobs", []), "ok"
        return [], f"unknown source type: {stype}"
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, KeyError, ValueError) as e:
        return [], f"error: {e}"


def run_all(settings: dict | None = None) -> dict:
    """Run every enabled source and store new jobs. Returns a summary."""
    settings = settings or db.get_settings()
    sources = settings.get("sources", [])
    total_new = 0
    notes = []
    for cfg in sources:
        if not cfg.get("enabled"):
            continue
        jobs, note = fetch_source(cfg)
        added = 0
        for j in jobs:
            if db.insert_job(j) is not None:
                added += 1
        total_new += added
        notes.append({"source": cfg.get("id"), "found": len(jobs), "added": added, "note": note})
    return {
        "new_jobs": total_new,
        "total_jobs": db.job_count(),
        "sources": notes,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    db.init_db()
    print(json.dumps(run_all(), ensure_ascii=False, indent=2))
