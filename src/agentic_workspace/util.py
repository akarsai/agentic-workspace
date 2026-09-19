"""Shared helpers: subprocess running, terminal output, small text utils."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# gh-style colours; disabled when stdout is not a TTY or NO_COLOR is set, so
# piped/CI output stays clean.
def _c(code: str) -> str:
    return code if (sys.stdout.isatty() and not os.environ.get("NO_COLOR")) else ""


BOLD = _c("\033[1m")
DIM = _c("\033[2m")
CYAN = _c("\033[36m")
GREEN = _c("\033[32m")
YELLOW = _c("\033[33m")
MAGENTA = _c("\033[35m")
RESET = _c("\033[0m")


def say(msg: str = "") -> None:
    print(msg)


def bold(msg: str) -> None:
    print(f"{BOLD}{msg}{RESET}")


def banner(title: str, *, min_width: int = 64, side_pad: int = 5, bold: bool = False) -> None:
    """Print a boxed title banner that auto-fits its content.

    Long titles are word-wrapped onto multiple lines and the box width adapts
    to the longest line, so the frame never overflows (or under-fills).
    `bold` renders the whole box in bold (used by the installer).
    """
    words = title.split() or [""]
    inner = max(min_width - 2 * side_pad, 8)
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > inner:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    if not lines:
        lines = [""]

    width = max(min_width, max(len(line) for line in lines) + 2 * side_pad)
    inner = width - 2 * side_pad
    box = "╔" + "═" * width + "╗\n"
    box += "\n".join(
        f"║{' ' * side_pad}{line.ljust(inner)}{' ' * side_pad}║" for line in lines
    )
    box += "\n" + "╚" + "═" * width + "╝"
    if bold:
        print(f"{BOLD}{box}{RESET}")
    else:
        print(box)


def dim(msg: str) -> None:
    print(f"{DIM}{msg}{RESET}")


def info(msg: str) -> None:
    print(f"{CYAN}→ {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"{GREEN}✓ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"{YELLOW}⚠ {msg}{RESET}", file=sys.stderr)


def error(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> int:
    error(msg)
    return code


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command list; raise on nonzero exit unless check=False."""
    return subprocess.run(cmd, check=check, **kwargs)


def run_quiet(cmd: list[str]) -> bool:
    """Run a command, discarding output; True on exit 0."""
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _read_key() -> str:
    """Read one key without requiring Enter (gh-style single-key prompt).

    Returns the pressed character; empty string on EOF, '\r'/newline for Enter.
    Piped stdin (tests, scripts) falls back to a line read, so piped input
    still works. Ctrl+C propagates as KeyboardInterrupt.
    """
    if not sys.stdin.isatty():
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            return ""
        if line == "":
            return ""  # EOF
        stripped = line.strip()
        return stripped[0] if stripped else "\n"
    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return ""
    try:
        tty.setcbreak(fd)  # no echo, no line buffering; Ctrl+C stays a signal
        ch = os.read(fd, 1)
    except OSError:
        return ""
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)
        except termios.error:
            pass
    return ch.decode("utf-8", "replace")


def confirm(prompt: str, hint: str = "y/N") -> bool:
    """gh-style yes/no prompt: answers as soon as a key is pressed, no Enter
    needed. The CAPITAL letter in the hint is the default when Enter (or no
    input) is pressed. ESC aborts the program; Ctrl+C aborts with a traceback
    as usual. Piped input falls back to line-based reading."""
    default = "y" if "Y" in hint else "n"
    print(f"{BOLD}? {prompt}{RESET} [{hint}] ", end="", flush=True)
    key = _read_key()
    if key in ("\r", "\n", ""):
        ans = default
        print()  # Enter: show a blank line, use the default
    elif key == "\x1b":
        print("\nAborted.")
        sys.exit(0)
    else:
        ans = key
        print(key)  # echo the pressed key
    return ans.lower().startswith("y")


def run_with_apptainer_fallback(cmd: list[str]) -> int:
    """Run a command; if it fails because apptainer is not on PATH, try
    `module load apptainer` once and retry. `module` is a bash function, so the
    retry runs inside `bash -lc` where loading affects the environment."""
    rc = subprocess.call(cmd)
    if rc == 0 or which("apptainer") is not None or shutil.which("module") is None:
        return rc
    warn(f"'{cmd[0]}' failed (exit {rc}); trying 'module load apptainer' and retrying once.")
    import shlex

    inner = "module load apptainer && exec " + " ".join(shlex.quote(c) for c in cmd)
    return subprocess.call(["bash", "-lc", inner])
