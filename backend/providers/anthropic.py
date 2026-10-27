"""Anthropic Messages API adapter. Needs an API key in the env var named by the provider configuration.
Not exercised in automated tests (no credentials in CI): see docs/PROGRESS.md."""
import os

import httpx

from backend.providers.base import LLMResult, Provider, ProviderError

DEFAULT_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, env_var: str = "ANTHROPIC_API_KEY"):
        self.env_var = env_var

    def status(self):
        ok = bool(os.environ.get(self.env_var))
        return {"name": "anthropic", "label": "Anthropic Claude", "configured": ok, "mock": False, "models": DEFAULT_MODELS, "env_var": self.env_var,
                "message": "Ready." if ok else f"Set the {self.env_var} environment variable before starting the backend, then restart. FlowPilot never stores API keys."}

    def complete(self, *, model, system, messages, tools, timeout=60) -> LLMResult:
        key = os.environ.get(self.env_var)
        if not key:
            raise ProviderError("not_configured", f"Anthropic is not configured: set {self.env_var} and restart the backend.", transient=False)
        body = {"model": model, "max_tokens": 2048, "messages": messages}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"].replace(".", "__"), "description": t.get("description", ""), "input_schema": t.get("schema") or {"type": "object", "properties": {}}} for t in tools]
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", json=body, timeout=timeout,
                           headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        except httpx.TimeoutException:
            raise ProviderError("timeout", "Anthropic request timed out", transient=True) from None
        except httpx.HTTPError as e:
            raise ProviderError("unavailable", f"Anthropic connection error: {type(e).__name__}", transient=True) from None
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError("rate_limit" if r.status_code == 429 else "unavailable", f"Anthropic HTTP {r.status_code}", transient=True)
        if r.status_code in (401, 403):
            raise ProviderError("auth", "Anthropic rejected the credentials (check the API key).", transient=False)
        if r.status_code >= 400:
            raise ProviderError("bad_request", f"Anthropic HTTP {r.status_code}: {r.text[:300]}", transient=False)
        data = r.json()
        text, calls = "", []
        for b in data.get("content", []):
            if b.get("type") == "text":
                text += b.get("text", "")
            elif b.get("type") == "tool_use":
                calls.append({"id": b["id"], "name": b["name"].replace("__", "."), "input": b.get("input", {})})
        u = data.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")} if u else None
        return LLMResult(text, calls, usage, "anthropic", model, False, data.get("stop_reason", "end_turn"))
