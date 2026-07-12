#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build / refresh the job-requirement knowledge base (jd_knowledge.json).

Merges web-scraped role drafts (data/kb_draft_*.json, produced by research agents)
into the curated knowledge base, preserving the existing curated roles and
*enriching* them with the newly researched requirements. This is the
"training data" for the resume analysis / optimization engine: it is structured
domain knowledge (not a model fine-tune), so every analysis stays grounded in
real recruitment market data.

Run:  python3 scripts/build_knowledge.py
"""
from __future__ import annotations
import json
import glob
import os
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_PATH = os.path.join(BASE, "data", "jd_knowledge.json")
DRAFT_GLOB = os.path.join(BASE, "data", "kb_draft_*.json")


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _merge_list(existing, new):
    out = list(existing or [])
    seen = {_norm(x) for x in out}
    for x in (new or []):
        if x is None:
            continue
        n = _norm(x)
        if n and n not in seen:
            out.append(x)
            seen.add(n)
    return out


def merge_role(existing, new):
    """Return a merged role object (existing curated data wins on label/aliases
    base, but lists are extended with the newly researched items)."""
    e = existing or {}
    out = dict(e)
    out["role"] = new.get("role", e.get("role"))
    # keep the richer label if new research produced one
    if new.get("label"):
        out["label"] = new["label"]
    out["aliases"] = _merge_list(e.get("aliases"), new.get("aliases"))
    out["must_have"] = _merge_list(e.get("must_have"), new.get("must_have"))
    out["nice_to_have"] = _merge_list(e.get("nice_to_have"), new.get("nice_to_have"))
    out["keywords"] = _merge_list(e.get("keywords"), new.get("keywords"))
    out["responsibilities"] = _merge_list(e.get("responsibilities"), new.get("responsibilities"))
    out["soft_skills"] = _merge_list(e.get("soft_skills"), new.get("soft_skills"))
    # salary band from research (if present)
    if new.get("salary_band"):
        out["salary_band"] = new["salary_band"]
    return out


def main() -> None:
    kb = json.load(open(KB_PATH, encoding="utf-8"))
    existing = {r["role"]: r for r in kb.get("roles", [])}
    initial_count = len(existing)

    drafts = []
    for path in sorted(glob.glob(DRAFT_GLOB)):
        try:
            drafts.extend(json.load(open(path, encoding="utf-8")))
        except Exception as e:
            print(f"  ! skip {path}: {e}")

    added, enriched = 0, 0
    for role in drafts:
        key = role.get("role")
        if not key:
            continue
        if key in existing:
            existing[key] = merge_role(existing[key], role)
            enriched += 1
        else:
            existing[key] = merge_role(None, role)
            added += 1

    merged = list(existing.values())
    kb["roles"] = merged
    kb["_meta"] = {
        "description": (
            "岗位要求知识库：从主流招聘平台（猎聘/BOSS直聘/拉勾/牛客/智联/各大厂招聘页/"
            "高校就业网等）真实 JD 大规模抓取并归纳，用作简历分析与优化的检索增强数据源。"
            "非模型微调，而是结构化的领域知识，让分析'有据可依'。"
        ),
        "built_from": "web-scraped real recruitment JD (BOSS直聘/猎聘/拉勾/牛客/智联/各大厂招聘页/高校就业网/安全社区/量化社区/设计社区等)",
        "built_at": date.today().isoformat(),
        "version": int(kb.get("_meta", {}).get("version", 1)) + 1,
        "roles_count": len(merged),
        "curated_roles": initial_count,
        "researched_roles_added": added,
        "curated_roles_enriched": enriched,
    }
    json.dump(kb, open(KB_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # keyword universe (for LEXICON augmentation downstream)
    kw = set()
    for r in merged:
        for f in ("keywords", "must_have", "nice_to_have"):
            for k in r.get(f, []):
                kw.add(_norm(k))
    print(f"Merged knowledge base -> {len(merged)} roles "
          f"({added} new, {enriched} enriched from {initial_count} curated).")
    print(f"Unique keyword universe: {len(kw)} terms.")


if __name__ == "__main__":
    main()
