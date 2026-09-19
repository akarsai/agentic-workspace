# Agentic harness configuration (child instance)
# Managed by: agentic-workspace install / setup
#
# Location: ${XDG_CONFIG_HOME:-$HOME/.config}/<name>/config.py
# Edit individual settings:  <name> --setup KEY=VALUE

# Container runtime: docker (or apptainer, for Linux HPC builds without Docker)
AGENTIC_CONTAINER_RUNTIME = "docker"

# Authentication: tool
AGENTIC_AUTH_MODE = "tool"

# Name of the env var holding the preferred API key (default: DEEPSEEK_API_KEY)
AGENTIC_API_KEY_ENV = ""

# CLI tool: pi (or opencode, claude, codex)
AGENTIC_CLI_TOOL = "pi"

# Default model passed to the CLI tool
AGENTIC_DEFAULT_MODEL = ""

# Model subscription: one flat-rate coding-plan provider used as the DEFAULT
# model everywhere -- main session AND all subagents. Explicit settings
# (AGENTIC_DEFAULT_MODEL, AGENTIC_SUBAGENT_MODEL, --model) still win.
# Format: "<provider>:<model>"  e.g. "zai:glm-5.3"  (z.ai GLM Coding Plan;
# export ZAI_API_KEY). Empty = disabled.
AGENTIC_MODEL_SUBSCRIPTION = ""

# Subagent dispatch defaults (used by the baked-in subagent extension).
# Empty means the extension's built-in default applies.
#   AGENTIC_SUBAGENT_MODEL     model for spawned subagents (default: deepseek-v4-flash)
#   AGENTIC_SUBAGENT_TOOLS     comma-separated tool allowlist (default: read,bash,grep,find,ls,edit,write)
#   AGENTIC_SUBAGENT_THINKING  thinking level for subagents (default: off)
AGENTIC_SUBAGENT_MODEL = ""
AGENTIC_SUBAGENT_TOOLS = ""
AGENTIC_SUBAGENT_THINKING = ""

# HTTP(S) proxy (leave empty if not needed)
AGENTIC_HTTPS_PROXY = ""
AGENTIC_HTTP_PROXY = ""

# Base directory for local state, caches, and container temp data
AGENTIC_STATE_ROOT = "<HOME>/.cache/<name>"

# Additional directories to bind into the sandbox (colon-separated)
# Inside the container they appear under /workspace/.mount/<basename>
AGENTIC_EXTRA_BIND_DIRS = ""
