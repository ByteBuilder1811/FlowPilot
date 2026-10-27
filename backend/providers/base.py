from dataclasses import dataclass, field


class ProviderError(Exception):
    def __init__(self, kind: str, message: str, transient: bool = False):
        super().__init__(message)
        self.kind, self.message, self.transient = kind, message, transient

    def to_dict(self):
        return {"kind": self.kind, "message": self.message, "transient": self.transient}


@dataclass
class LLMResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # [{id,name,input}]
    usage: dict | None = None  # {input_tokens, output_tokens} ONLY when reported by the provider
    provider: str = ""
    model: str = ""
    mock: bool = False
    stop_reason: str = "end_turn"

    def to_dict(self):
        return {"text": self.text, "tool_calls": self.tool_calls, "usage": self.usage, "provider": self.provider,
                "model": self.model, "mock": self.mock, "stop_reason": self.stop_reason}

    @staticmethod
    def from_dict(d):
        return LLMResult(**d)


class Provider:
    name = "base"

    def status(self) -> dict:
        raise NotImplementedError

    def complete(self, *, model: str, system: str, messages: list[dict], tools: list[dict], timeout: float = 60) -> LLMResult:
        raise NotImplementedError
