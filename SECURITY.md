# Security Policy

## The security model

agentic-workspace runs a coding agent inside a container and gives it exactly one writable directory: your project, mounted at `/workspace`.
Everything else on your machine is out of reach except the documented mounts listed below.
The container is disposable and rebuilt from pinned inputs.

What the agent can reach:

- The project directory, read-write.
- `~/.ssh` and `~/.gitconfig`, mounted read-only when they exist, so git push/pull works inside the sandbox.
- The instance's state directory (sessions, tool config), read-write.
- API keys you export in your shell, forwarded explicitly (`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `WANDB_API_KEY`, `HF_TOKEN`, a subscription provider key).
- On Slurm login nodes, the host's Slurm client binaries and config, and extra directories you opt into via `AGENTIC_EXTRA_BIND_DIRS`.

## Limits you should understand before launching

- **Your SSH keys are readable by the agent.**
  The read-only mount prevents modification, not reading.
  A compromised or misbehaving agent can read every private key in `~/.ssh` and exfiltrate them over the network.
  If that risk is unacceptable, move the keys out of `~/.ssh` or use a dedicated host account for launches.
- **The agent can use your git identity.**
  `~/.gitconfig` is read-only mounted too, so the agent can commit and push as you wherever those credentials reach.
- **Containers are a boundary, not a guarantee.**
  This tool relies on the container runtime's isolation.
  It does not add seccomp profiles, user namespaces, or network policies beyond what Docker/Apptainer apply by default.
  Do not point it at projects on machines where a container escape would be catastrophic.
- **`--yolo` disables the last confirmation layer.**
  It maps to the engine's native skip-permissions flag and lets the agent run any tool without asking.
  Inside the sandbox that is still bounded to `/workspace` plus the mounts above, but the agent can push, spend API budget, and reach the network freely.
- **Build-time downloads trust the upstream registries.**
  Images install tools from the Ubuntu archive, the npm registry, PyPI, GitHub releases, and nodejs.org over TLS.
  After `./agentic-workspace update`, the direct downloads (node, yq, gh, typst, uv, bun) are pinned by version and verified against a sha256 from the same upstream source, and npm/uv installs are integrity-checked by their package managers.
  A fresh clone without `versions.json` resolves `latest` and builds without checksum verification.
  Two users building from the same commit can therefore get different images unless both ran `update` against the same pin state.
- **Agent instructions are prompts, not sandbox policy.**
  An instance's INSTRUCTIONS.md shapes behavior, but any code the agent writes runs with the full permissions described above.

## Reporting a vulnerability

Please open a private security advisory at https://github.com/akarsai/agentic-workspace/security/advisories/new.
Include reproduction steps and the affected commit.
Expect a response within a few days.
Public disclosure is fine once a fix is released.
