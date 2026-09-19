"""Model subscriptions: one provider+model used as the default everywhere.

A subscription is a flat-rate coding-plan provider (e.g. the z.ai GLM coding
plan). Setting

    AGENTIC_MODEL_SUBSCRIPTION = "<provider>:<model>"    # e.g. zai:glm-5.3

in the instance config (or via `<name> --setup`) makes that model the
default for everything:

- the main session (it fills AGENTIC_DEFAULT_MODEL unless explicitly set),
- every subagent (it fills AGENTIC_SUBAGENT_MODEL unless explicitly set),
- the provider is wired into the CLI tool: pi gets a provider entry in
  ~/.pi/agent/models.json (host-side config store), Claude Code gets the
  provider's Anthropic-compatible endpoint via env.

Explicit settings still win: AGENTIC_DEFAULT_MODEL / AGENTIC_SUBAGENT_MODEL
in the config, the `--model` launch flag, and per-call subagent `model:`
overrides all take precedence.

The API key is never stored here; it is forwarded from the launch shell via
the passthrough keys (e.g. ZAI_API_KEY).
"""
from __future__ import annotations

import json
from pathlib import Path

# Known subscription providers. Keys are the alias used in
# AGENTIC_MODEL_SUBSCRIPTION ("<alias>:<model>") and the pi provider id.
#   label            human-readable name (wizard, banner)
#   api_key_env      env var the key is read from (also added to passthrough)
#   alt_api_key_envs alternative env var names accepted for the same key
#   pi               provider fragment for pi's models.json (baseUrl, api,
#                    apiKey env reference, compat); models are appended
#   claude_env       env pairs for Claude Code; "{key}" resolves to the key
SUBSCRIPTION_PROVIDERS: dict[str, dict] = {
    "zai": {
        "label": "z.ai GLM Coding Plan",
        "api_key_env": "ZAI_API_KEY",
        "alt_api_key_envs": ("Z_AI_API_KEY",),
        "pi": {
            "baseUrl": "https://api.z.ai/api/coding/paas/v4",
            "api": "openai-completions",
            "apiKey": "$ZAI_API_KEY",
            "compat": {"thinkingFormat": "zai"},
        },
        "claude_env": {
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "{key}",
        },
    },
}


def parse(spec: str) -> tuple[str, str] | None:
    """Parse "<provider>:<model>" (or "<provider>/<model>") -> (alias, model).

    Returns None for empty or malformed specs. An unknown provider alias is
    still returned -- callers report it separately (see provider()).
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    sep = ":" if ":" in spec else ("/" if "/" in spec else None)
    if sep is None:
        return None
    alias, _, model = spec.partition(sep)
    alias, model = alias.strip(), model.strip()
    if not alias or not model or sep in model:
        return None
    return alias, model


def provider(alias: str) -> dict | None:
    """The provider registry entry for an alias, else None."""
    return SUBSCRIPTION_PROVIDERS.get(alias)


def api_key_envs(alias: str) -> list[str]:
    """Env vars that may hold this provider's API key (preferred first)."""
    p = provider(alias)
    if not p:
        return []
    return [p["api_key_env"], *p.get("alt_api_key_envs", ())]


def api_key(alias: str, env: dict[str, str]) -> str:
    """The provider's API key from env (preferred name first), else ''."""
    for name in api_key_envs(alias):
        if env.get(name):
            return env[name]
    return ""


def pi_model(spec: str) -> str:
    """pi model reference: '<alias>/<model>' (pi's provider/id syntax)."""
    alias, model = parse(spec) or ("", "")
    return f"{alias}/{model}" if alias and model else ""


def apply_to_config(cfg: dict[str, str]) -> dict[str, str]:
    """Fill AGENTIC_DEFAULT_MODEL and AGENTIC_SUBAGENT_MODEL from the
    subscription when (and only when) they are not explicitly set."""
    parsed = parse(cfg.get("AGENTIC_MODEL_SUBSCRIPTION", ""))
    if parsed is None or provider(parsed[0]) is None:
        return cfg
    if not cfg.get("AGENTIC_DEFAULT_MODEL"):
        cfg["AGENTIC_DEFAULT_MODEL"] = pi_model(cfg["AGENTIC_MODEL_SUBSCRIPTION"])
    if not cfg.get("AGENTIC_SUBAGENT_MODEL"):
        cfg["AGENTIC_SUBAGENT_MODEL"] = pi_model(cfg["AGENTIC_MODEL_SUBSCRIPTION"])
    return cfg


def engine_env_pairs(spec: str, tool: str, env: dict[str, str]) -> list[tuple[str, str]]:
    """Container env pairs that route the selected CLI tool through the
    subscription provider. pi reads models.json instead (see
    write_pi_models_json); codex/opencode are not wired (yet)."""
    parsed = parse(spec)
    if parsed is None:
        return []
    alias, _model = parsed
    p = provider(alias)
    if not p:
        return []
    if tool != "claude":
        return []
    key = api_key(alias, env)
    pairs: list[tuple[str, str]] = []
    for k, v in p.get("claude_env", {}).items():
        pairs.append((k, v.replace("{key}", key)))
    return pairs


def write_pi_models_json(config_store: Path, spec: str) -> Path | None:
    """Upsert the subscription provider into pi's models.json inside the
    host-side config store (mounted as /home in the sandbox). Existing
    providers and all other keys are preserved; the subscription owns only
    its own provider entry."""
    parsed = parse(spec)
    if parsed is None:
        return None
    alias, model = parsed
    p = provider(alias)
    if p is None:
        return None

    agent_dir = config_store / ".pi" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    models_json = agent_dir / "models.json"

    data: dict = {}
    if models_json.is_file():
        try:
            loaded = json.loads(models_json.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}  # unreadable: rewrite rather than fail the launch

    entry = json.loads(json.dumps(p["pi"]))  # deep copy of the registry entry
    entry["models"] = [
        {
            "id": model,
            "name": f"{p['label']} {model}",
            "reasoning": True,
        }
    ]
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = data["providers"] = {}
    # Keep models the user added to this provider in earlier sessions.
    existing_models = providers.get(alias, {}).get("models", [])
    if isinstance(existing_models, list):
        known = {m.get("id") for m in existing_models if isinstance(m, dict)}
        for m in entry["models"]:
            if m["id"] not in known:
                existing_models.append(m)
        entry["models"] = existing_models

    providers[alias] = entry
    models_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return models_json
