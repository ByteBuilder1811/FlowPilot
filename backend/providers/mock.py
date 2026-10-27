"""Deterministic mock LLM. Every response is labelled mock=True and prefixed so it can never pass as a real model.

Directives in the user prompt drive behaviour:
  [[tool:NAME {"json":"args"}]]  call that tool (one per turn, in order)
  [[loop]]                       keep re-issuing the same tool call (to exercise loop detection)
  [[answer:TEXT]]                final answer text
"""
import hashlib
import json
import re

from backend.providers.base import LLMResult, Provider

_DIRECTIVE = re.compile(r"\[\[(tool|answer|loop)(?::(.*?))?\]\]", re.S)


class MockProvider(Provider):
    name = "mock"
    models = ["mock-agent-1"]

    def status(self):
        return {"name": "mock", "label": "Mock LLM (deterministic)", "configured": True, "mock": True, "models": self.models,
                "message": "Always available. Responses are deterministic test doubles, not a real model."}

    def complete(self, *, model, system, messages, tools, timeout=60) -> LLMResult:
        user_text = ""
        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], str):
                user_text = m["content"]  # last plain user message = the current prompt (earlier ones are memory)
        directives = _DIRECTIVE.findall(user_text)
        tool_dirs = [d for d in directives if d[0] == "tool"]
        loop = any(d[0] == "loop" for d in directives)
        results = []
        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                results += [b for b in m["content"] if b.get("type") == "tool_result"]
        n_done = len(results)
        allowed = {t["name"] for t in tools}

        def mk(name_args: str, idx: int):
            name, _, rest = name_args.strip().partition(" ")
            try:
                args = json.loads(rest) if rest.strip() else {}
            except ValueError:
                args = {}
            return {"id": f"toolu_mock_{idx}", "name": name, "input": args}

        if tool_dirs and loop and tool_dirs[0][1] and tool_dirs[0][1].split(" ")[0] in allowed:
            return LLMResult("[mock-llm] calling tool again", [mk(tool_dirs[0][1], n_done)], None, "mock", model, True, "tool_use")
        if n_done < len(tool_dirs):
            name = tool_dirs[n_done][1].strip().split(" ")[0]
            if name in allowed:
                return LLMResult(f"[mock-llm] I will call {name}.", [mk(tool_dirs[n_done][1], n_done)], None, "mock", model, True, "tool_use")
            return LLMResult(f"[mock-llm] Tool '{name}' is not available to this agent.", [], None, "mock", model, True)
        answers = [d[1] for d in directives if d[0] == "answer"]
        if answers:
            return LLMResult(f"[mock-llm] {answers[0].strip()}", [], None, "mock", model, True)
        if results:
            urls, snippets = [], []
            for r in results:
                try:
                    c = json.loads(r["content"]) if isinstance(r["content"], str) else r["content"]
                except ValueError:
                    c = {}
                for item in (c.get("results") or []) if isinstance(c, dict) else []:
                    urls.append(item.get("url"))
                    snippets.append(f"{item.get('snippet', '')} [{len(urls)}]")
            body = " ".join(snippets) if snippets else f"Tool returned: {str(results[-1]['content'])[:200]}"
            src = ("\nSources:\n" + "\n".join(f"[{i + 1}] {u}" for i, u in enumerate(urls))) if urls else ""
            return LLMResult(f"[mock-llm] Summary: {body}{src}", [], None, "mock", model, True)
        h = hashlib.sha256((system + user_text).encode()).hexdigest()[:8]
        first = re.sub(r"\s+", " ", _DIRECTIVE.sub("", user_text)).strip()[:160]
        return LLMResult(f"[mock-llm:{h}] Echo of request: {first}", [], None, "mock", model, True)
