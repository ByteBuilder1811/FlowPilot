from backend.db.models import ProviderConfiguration, SecretReference
from backend.db.session import session
from backend.providers.anthropic import AnthropicProvider
from backend.providers.base import Provider
from backend.providers.mock import MockProvider


def get_provider(name: str) -> Provider:
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        env = "ANTHROPIC_API_KEY"
        with session() as s:
            cfg = s.get(ProviderConfiguration, "anthropic")
            if cfg and cfg.secret_ref:
                ref = s.query(SecretReference).filter_by(name=cfg.secret_ref).first()
                if ref:
                    env = ref.env_var
        return AnthropicProvider(env)
    from backend.providers.base import ProviderError
    raise ProviderError("unknown_provider", f"Unknown provider '{name}'", transient=False)


def all_statuses() -> list[dict]:
    out = []
    for n in ("mock", "anthropic"):
        st = get_provider(n).status()
        with session() as s:
            cfg = s.get(ProviderConfiguration, n)
            st["enabled"] = cfg.enabled if cfg else True
            st["default_model"] = (cfg.default_model if cfg else None) or st["models"][0]
        out.append(st)
    return out
