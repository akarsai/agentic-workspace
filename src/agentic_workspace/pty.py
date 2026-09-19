"""PTY wrapper for interactive Apptainer launches.

Allocates a pseudo-TTY for the child command (needed by interactive CLIs like
opencode), forwards signals, and provides a force-quit escape valve. Ported
from the old blueprint/scripts/pty-wrapper.py.
"""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty

FORCE_QUIT_COUNT = 5
FORCE_QUIT_WINDOW = 2.0
CHILD_CHECK_INTERVAL = 1.0


def _set_window_size(master_fd: int) -> None:
    try:
        s = struct.pack("HHHH", 0, 0, 0, 0)
        size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, s)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def _nonblock_write(fd: int, data: bytes) -> None:
    try:
        os.write(fd, data)
    except OSError as e:
        if e.errno == errno.EAGAIN:
            pass  # PTY buffer full (child not reading); drop rather than block
        else:
            raise


def run_with_pty(cmd: list[str]) -> int:
    pid, master_fd = pty.fork()

    if pid == 0:  # child
        os.execvp(cmd[0], cmd)

    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    ctrl_c_times: list[float] = []

    def forward_signal(signum, frame):  # noqa: ANN001
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass

    def handle_sigwinch(signum, frame):  # noqa: ANN001
        try:
            _set_window_size(master_fd)
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, handle_sigwinch)
    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGHUP, forward_signal)
    _set_window_size(master_fd)

    # Raw mode on stdin if it is a TTY; retry on EINTR (a SIGWINCH from the new
    # controlling terminal can interrupt tcsetattr right after pty.fork()).
    old_settings = None
    if sys.stdin.isatty():
        old_settings = termios.tcgetattr(sys.stdin)
        while True:
            try:
                tty.setraw(sys.stdin.fileno())
                break
            except termios.error as e:
                err = getattr(e, "errno", e.args[0] if e.args else None)
                if err != errno.EINTR:
                    raise

    exit_code = 1
    try:
        while True:
            try:
                r, _w, _e = select.select(
                    [sys.stdin, master_fd], [], [], CHILD_CHECK_INTERVAL
                )
            except (InterruptedError, select.error):
                continue

            if not r:
                try:
                    result = os.waitpid(pid, os.WNOHANG)
                    if result[0] != 0:
                        break
                except ChildProcessError:
                    break
                continue

            if sys.stdin in r:
                data = os.read(sys.stdin.fileno(), 4096)
                if data:
                    if b"\x03" in data:
                        now = time.monotonic()
                        ctrl_c_times.append(now)
                        ctrl_c_times = [t for t in ctrl_c_times if now - t < FORCE_QUIT_WINDOW]
                        if len(ctrl_c_times) >= FORCE_QUIT_COUNT:
                            try:
                                os.kill(pid, signal.SIGTERM)
                                for _ in range(10):
                                    time.sleep(0.1)
                                    try:
                                        if os.waitpid(pid, os.WNOHANG)[0] != 0:
                                            break
                                    except ChildProcessError:
                                        break
                                else:
                                    os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            break
                    else:
                        ctrl_c_times.clear()
                    _nonblock_write(master_fd, data)
                else:
                    break  # stdin EOF

            if master_fd in r:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        os.write(sys.stdout.fileno(), data)
                        sys.stdout.flush()
                    else:
                        break  # EOF
                except OSError as e:
                    if e.errno == errno.EAGAIN:
                        continue
                    break
    finally:
        if old_settings:
            while True:
                try:
                    termios.tcsetattr(sys.stdin, termios.TCSAFLUSH, old_settings)
                    break
                except termios.error as e:
                    err = getattr(e, "errno", e.args[0] if e.args else None)
                    if err != errno.EINTR:
                        raise
        try:
            _pid, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        except ChildProcessError:
            exit_code = 1
    return exit_code
