"""Unified multi-provider LLM client.

One small module that every other part of the app talks to when it wants a
chat completion. It hides the differences between vendors behind a single
``chat(messages, ...)`` call and a curated list of mainstream providers.

Supported back-ends
-------------------
* ``openai``   – OpenAI-compatible ``/chat/completions`` (the de-facto standard
                 used by DeepSeek, Zhipu GLM, Moonshot/Kimi, Qwen, Doubao,
                 Baichuan, MiniMax, Yi, Hunyuan, HY3, OpenRouter, Azure, ...).
* ``anthropic``– native Anthropic Messages API (Claude).
* ``gemini``   – native Google Generative Language API (Gemini).

Adding a new OpenAI-compatible vendor is a one-line entry in ``PROVIDERS``.
"""
from __future__ import annotations

import httpx
from . import database as db

# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #
# kind: "openai" | "anthropic" | "gemini"
# base_url: default endpoint; empty => user must fill it (custom_endpoint=True)
# models:   list of model ids the UI offers; first one is the default
PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI (GPT)",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini", "o3-mini"],
        "doc": "https://platform.openai.com/api-keys",
        "api_key_placeholder": "sk-...",
    },
    "deepseek": {
        "label": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "doc": "https://platform.deepseek.com/",
        "api_key_placeholder": "sk-...",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-airx", "glm-4-flash", "glm-4-long", "glm-4"],
        "doc": "https://open.bigmodel.cn/usercenter/apikeys",
        "api_key_placeholder": "Your Zhipu API key",
    },
    "moonshot": {
        "label": "Moonshot (Kimi)",
        "kind": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "doc": "https://platform.moonshot.cn/",
        "api_key_placeholder": "sk-...",
    },
    "qwen": {
        "label": "阿里通义千问 (Qwen)",
        "kind": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long", "qwen2.5-72b-instruct", "qwen3-235b-a22b"],
        "doc": "https://dashscope.console.aliyun.com/apiKey",
        "api_key_placeholder": "sk-...",
    },
    "doubao": {
        "label": "字节豆包 (Doubao / 方舟)",
        "kind": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-1.6-250615", "doubao-pro-32k", "doubao-lite-32k"],
        "doc": "https://console.volcengine.com/ark",
        "api_key_placeholder": "Your Volcengine ARK key",
    },
    "baichuan": {
        "label": "百川 (Baichuan)",
        "kind": "openai",
        "base_url": "https://api.baichuan-ai.com/v1",
        "models": ["Baichuan4-Turbo", "Baichuan4-Air", "Baichuan3-Turbo"],
        "doc": "https://platform.baichuan-ai.com/",
        "api_key_placeholder": "sk-...",
    },
    "minimax": {
        "label": "MiniMax",
        "kind": "openai",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-Text-01", "abab6.5-chat", "abab6.5s-chat"],
        "doc": "https://www.minimax.io/platform",
        "api_key_placeholder": "Your MiniMax key",
    },
    "yi": {
        "label": "零一万物 (Yi)",
        "kind": "openai",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "models": ["yi-lightning", "yi-large", "yi-medium", "yi-spark"],
        "doc": "https://platform.lingyiwanwu.com/",
        "api_key_placeholder": "Your Yi key",
    },
    "hunyuan": {
        "label": "腾讯混元 (Hunyuan)",
        "kind": "openai",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "models": ["hunyuan-turbo", "hunyuan-pro", "hunyuan-standard", "hunyuan-lite"],
        "doc": "https://cloud.tencent.com/product/hunyuan",
        "api_key_placeholder": "Your Tencent Cloud key",
    },
    "hy3": {
        "label": "HY3 (混元 HY3)",
        "kind": "openai",
        "base_url": "",
        "custom_endpoint": True,
        "models": ["hy3", "hy3-pro", "hy3-lite"],
        "doc": "",
        "api_key_placeholder": "Your HY3 endpoint key",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest", "claude-3-7-sonnet-latest"],
        "doc": "https://console.anthropic.com/settings/keys",
        "api_key_placeholder": "sk-ant-...",
    },
    "gemini": {
        "label": "Google Gemini",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "doc": "https://aistudio.google.com/app/apikey",
        "api_key_placeholder": "Your Gemini API key",
    },
    "openrouter": {
        "label": "OpenRouter (聚合)",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "openai/gpt-4o", "anthropic/claude-3.5-sonnet",
            "google/gemini-pro-1.5", "deepseek/deepseek-chat",
            "meta-llama/llama-3.1-70b-instruct",
        ],
        "doc": "https://openrouter.ai/keys",
        "api_key_placeholder": "sk-or-...",
    },
    "azure": {
        "label": "Azure OpenAI",
        "kind": "openai",
        "base_url": "",
        "custom_endpoint": True,
        "models": ["gpt-4o", "gpt-4", "gpt-35-turbo"],
        "doc": "https://portal.azure.com/",
        "api_key_placeholder": "Your Azure key",
    },
}

# Order shown in the UI dropdown.
PROVIDER_ORDER = [
    "openai", "deepseek", "zhipu", "moonshot", "qwen", "doubao",
    "baichuan", "minimax", "yi", "hunyuan", "hy3", "anthropic",
    "gemini", "openrouter", "azure",
]


def list_providers() -> list[dict]:
    """Public list of providers for the UI (no secrets)."""
    out = []
    for key in PROVIDER_ORDER:
        p = PROVIDERS.get(key)
        if not p:
            continue
        out.append({
            "key": key,
            "label": p["label"],
            "kind": p["kind"],
            "base_url": p.get("base_url", ""),
            "custom_endpoint": bool(p.get("custom_endpoint", False)),
            "models": list(p.get("models", [])),
            "doc": p.get("doc", ""),
            "api_key_placeholder": p.get("api_key_placeholder", ""),
        })
    return out


def _resolve_config(settings: dict | None = None) -> dict | None:
    """Build a concrete call config from settings, or None if disabled."""
    s = settings if settings is not None else db.get_settings()
    llm = (s.get("llm") or {}) if s else {}
    if not (llm.get("enabled") and llm.get("api_key")):
        return None
    provider_key = llm.get("provider", "openai")
    prov = PROVIDERS.get(provider_key)
    if not prov:
        return None
    base_url = (llm.get("base_url") or "").strip() or (prov.get("base_url") or "").strip()
    models = prov.get("models") or []
    model = llm.get("model") or (models[0] if models else "")
    if not base_url:
        return None  # custom-endpoint provider without a URL
    return {
        "provider": provider_key,
        "kind": prov["kind"],
        "base_url": base_url,
        "api_key": llm["api_key"],
        "model": model,
    }


def chat(messages: list[dict], settings: dict | None = None,
         temperature: float = 0.7, max_tokens: int = 1024) -> str | None:
    """Unified chat completion.

    ``messages`` is a list of ``{"role": "system"|"user"|"assistant",
    "content": "..."}`` (OpenAI style). Returns the assistant text, or ``None``
    on any failure so callers can fall back to a template.
    """
    cfg = _resolve_config(settings)
    if not cfg:
        return None
    try:
        if cfg["kind"] == "openai":
            return _call_openai(cfg, messages, temperature, max_tokens)
        if cfg["kind"] == "anthropic":
            return _call_anthropic(cfg, messages, temperature, max_tokens)
        if cfg["kind"] == "gemini":
            return _call_gemini(cfg, messages, temperature, max_tokens)
    except Exception:
        return None
    return None


def chat_simple(system: str, user: str, settings: dict | None = None,
                temperature: float = 0.7, max_tokens: int = 1024) -> str | None:
    """Convenience wrapper: just give a system + user prompt."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    return chat(msgs, settings, temperature, max_tokens)


def test_connection(settings: dict | None = None) -> dict:
    """Best-effort connectivity check. Returns {ok, provider, model, error}."""
    cfg = _resolve_config(settings)
    if not cfg:
        return {"ok": False, "error": "未启用或缺少 API Key / Base URL"}
    try:
        text = chat(
            [{"role": "user", "content": "用一句话介绍你自己（中文，15字内）。"}],
            settings, temperature=0.2, max_tokens=64,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"],
                "error": f"{type(e).__name__}: {e}"}
    if text and text.strip():
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
                "reply": text.strip()[:120]}
    return {"ok": False, "provider": cfg["provider"], "model": cfg["model"],
            "error": "模型返回为空"}


# --------------------------------------------------------------------------- #
# Back-end implementations
# --------------------------------------------------------------------------- #
def _call_openai(cfg: dict, messages: list[dict], temperature: float, max_tokens: int) -> str | None:
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json"},
            json={
                "model": cfg["model"],
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


def _call_anthropic(cfg: dict, messages: list[dict], temperature: float, max_tokens: int) -> str | None:
    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [m for m in messages if m["role"] in ("user", "assistant")]
    payload: dict = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo,
    }
    if system_text:
        payload["system"] = system_text
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{cfg['base_url'].rstrip('/')}/messages",
            headers={
                "x-api-key": cfg["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data.get("content") or []
        return "".join(p.get("text", "") for p in parts).strip()


def _call_gemini(cfg: dict, messages: list[dict], temperature: float, max_tokens: int) -> str | None:
    sys_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    payload: dict = {
        "contents": contents or [{"role": "user", "parts": [{"text": "hi"}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if sys_text:
        payload["systemInstruction"] = {"parts": [{"text": sys_text}]}
    with httpx.Client(timeout=60) as client:
        url = (f"{cfg['base_url'].rstrip('/')}/models/{cfg['model']}"
               f":generateContent?key={cfg['api_key']}")
        resp = client.post(url, json=payload,
                           headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()
