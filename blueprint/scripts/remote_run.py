#!/usr/bin/env python3
"""remote_run.py: dispatch commands to other nodes in a multi-node Slurm
allocation. Runs INSIDE the container; communicates with the host-side
dispatcher by writing job files (JSON) to the shared dispatch directory.

Pure stdlib (runs with the container's plain python3).

Usage:
  remote-run <node> [--gpus N] -- <command...>     Run command (foreground, waits)
  remote-run <node> [--gpus N] --bg -- <command...> Run command (background, returns job ID)
  remote-run --status [job-id]                      Show job status (all or specific)
  remote-run --logs <job-id>                        Show stdout/stderr of a job
  remote-run --tail <job-id>                        Tail stdout of a running job
  remote-run --kill <job-id>                        Kill a running job
  remote-run --nodes                                List allocated nodes
  remote-run --help                                 Show this help
"""
import json
import os
import sys
import time
from pathlib import Path

DISPATCH_DIR = os.environ.get("AGENTIC_DISPATCH_DIR")
JOBS_DIR = Path(DISPATCH_DIR) / "jobs" if DISPATCH_DIR else None


def die(msg: str) -> int:
    print(f"Error: {msg}", file=sys.stderr)
    return 1


def show_help() -> int:
    print(
        "remote-run: dispatch commands to other nodes in a multi-node Slurm allocation.\n"
        "\n"
        "Usage:\n"
        "  remote-run <node> [--gpus N] -- <command...>      Run command (foreground, waits)\n"
        "  remote-run <node> [--gpus N] --bg -- <command...>  Run command (background, returns job ID)\n"
        "  remote-run --status [job-id]                       Show job status (all or specific)\n"
        "  remote-run --logs <job-id>                         Show stdout/stderr of a job\n"
        "  remote-run --tail <job-id>                         Tail stdout of a running job\n"
        "  remote-run --kill <job-id>                         Kill a running job\n"
        "  remote-run --nodes                                 List allocated nodes\n"
        "  remote-run --help                                  Show this help"
    )
    return 0


def require_dispatch() -> int | None:
    if not DISPATCH_DIR or not Path(DISPATCH_DIR).is_dir():
        return die("remote-run requires AGENTIC_DISPATCH_DIR (started with --multi-node)")
    return None


def list_nodes() -> int:
    err = require_dispatch()
    if err is not None:
        return err
    nodes_file = Path(DISPATCH_DIR) / "nodes.txt"
    if not nodes_file.is_file():
        return die("Node list not found. Is the dispatcher running?")
    head_file = Path(DISPATCH_DIR) / "head_node.txt"
    head_node = head_file.read_text().strip() if head_file.is_file() else ""
    print("Allocated nodes:")
    for node in nodes_file.read_text().split():
        if node == head_node:
            print(f"  {node}  (head - agent runs here, use GPUs directly)")
        else:
            print(f"  {node}  (remote - dispatch via remote-run)")
    return 0


def read_job(job_id: str) -> dict:
    job_file = JOBS_DIR / f"{job_id}.job"
    if not job_file.is_file():
        return {}
    return json.loads(job_file.read_text())


def show_status(job_id: str | None = None) -> int:
    err = require_dispatch()
    if err is not None:
        return err
    if job_id:
        status_file = JOBS_DIR / f"{job_id}.status"
        if not status_file.is_file():
            return die(f"Job {job_id} not found")
        job = read_job(job_id)
        node = job.get("node", "unknown")
        print(f"Job {job_id}: {status_file.read_text().strip()} (node: {node})")
        return 0
    found = False
    for job_file in sorted(JOBS_DIR.glob("*.job")):
        found = True
        job_id = job_file.stem
        status_file = JOBS_DIR / f"{job_id}.status"
        status = status_file.read_text().strip() if status_file.is_file() else "pending"
        job = json.loads(job_file.read_text())
        cmd = job.get("cmd", "")[:60]
        print(f"{job_id:<6} {job.get('node', ''):<16} {status:<12} {cmd}")
    if not found:
        print("No jobs submitted yet.")
    return 0


def show_logs(job_id: str) -> int:
    err = require_dispatch()
    if err is not None:
        return err
    stdout_file = JOBS_DIR / f"{job_id}.stdout"
    stderr_file = JOBS_DIR / f"{job_id}.stderr"
    if stdout_file.is_file():
        print("=== stdout ===")
        print(stdout_file.read_text(), end="")
    if stderr_file.is_file():
        print("=== stderr ===")
        print(stderr_file.read_text(), end="")
    if not stdout_file.is_file() and not stderr_file.is_file():
        return die(f"No output files for job {job_id}")
    return 0


def tail_logs(job_id: str) -> int:
    err = require_dispatch()
    if err is not None:
        return err
    stdout_file = JOBS_DIR / f"{job_id}.stdout"
    if not stdout_file.is_file():
        return die(f"No stdout file for job {job_id} (job may not have started yet)")
    # Tail: read from the end, then follow new bytes.
    with open(stdout_file, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size > 4096:
            f.seek(size - 4096)
        sys.stdout.buffer.write(f.read())
        sys.stdout.flush()
        while True:
            data = f.read()
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.flush()
            else:
                time.sleep(1)


def kill_job(job_id: str) -> int:
    err = require_dispatch()
    if err is not None:
        return err
    pid_file = JOBS_DIR / f"{job_id}.pid"
    if not pid_file.is_file():
        return die(f"No PID file for job {job_id}")
    pid = pid_file.read_text().strip()
    # The PID is on the host side (dispatcher's srun process); signal the
    # dispatcher by writing a kill request.
    (JOBS_DIR / f"{job_id}.kill").write_text(pid)
    print(f"Kill request sent for job {job_id} (pid {pid})")
    return 0


def next_job_id() -> str:
    n = 1
    while (JOBS_DIR / f"{n:03d}.job").is_file():
        n += 1
    return f"{n:03d}"


def submit_job(argv: list[str]) -> int:
    err = require_dispatch()
    if err is not None:
        return err
    node = ""
    gpus = ""
    background = False
    workdir = "/workspace"
    cmd = ""
    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--gpus":
            if i + 1 >= len(args):
                return die("--gpus requires a value")
            gpus = args[i + 1]
            i += 1
        elif a in ("--bg", "--background"):
            background = True
        elif a == "--workdir":
            if i + 1 >= len(args):
                return die("--workdir requires a value")
            workdir = args[i + 1]
            i += 1
        elif a == "--":
            cmd = " ".join(args[i + 1 :])
            break
        elif a.startswith("-"):
            return die(f"Unknown option: {a}")
        else:
            if not node:
                node = a
            else:
                return die(f"Unexpected argument: {a} (did you forget '--' before the command?)")
        i += 1

    if not node:
        return die("No node specified. Usage: remote-run <node> [--gpus N] -- <command...>")
    if not cmd:
        return die("No command specified. Use '--' to separate options from the command.")

    nodes_file = Path(DISPATCH_DIR) / "nodes.txt"
    if nodes_file.is_file():
        nodes = nodes_file.read_text().split()
        if node not in nodes:
            print(f"Warning: {node} is not in the allocated node list.")
            print("Available nodes:")
            print(nodes_file.read_text(), end="")
            return die(f"Node {node} not allocated")

    job_id = next_job_id()
    if not gpus:
        gpus = os.environ.get("AGENTIC_GPUS_PER_NODE", "4")

    (JOBS_DIR / f"{job_id}.job").write_text(
        json.dumps({"node": node, "gpus": gpus, "workdir": workdir, "cmd": cmd}, indent=2)
    )

    if background:
        print(f"Submitted job {job_id} on {node} ({gpus} GPUs): {cmd}")
        print(f"  Check status:  remote-run --status {job_id}")
        print(f"  View logs:     remote-run --logs {job_id}")
        print(f"  Tail output:   remote-run --tail {job_id}")
        return 0

    print(f"Submitted job {job_id} on {node} ({gpus} GPUs)")
    print("Waiting for completion...")
    while True:
        status_file = JOBS_DIR / f"{job_id}.status"
        if status_file.is_file():
            status = status_file.read_text().strip()
            if status == "running":
                pass
            elif status.startswith("done:0"):
                print("Job completed successfully.")
                stdout_file = JOBS_DIR / f"{job_id}.stdout"
                if stdout_file.is_file():
                    print(stdout_file.read_text(), end="")
                return 0
            elif status.startswith("done:"):
                code = status[5:]
                print(f"Job failed (exit code {code}).")
                stderr_file = JOBS_DIR / f"{job_id}.stderr"
                stdout_file = JOBS_DIR / f"{job_id}.stdout"
                if stderr_file.is_file():
                    print("=== stderr ===")
                    print(stderr_file.read_text(), end="")
                if stdout_file.is_file():
                    print("=== stdout ===")
                    print(stdout_file.read_text(), end="")
                return int(code) if code.isdigit() else 1
        time.sleep(2)


def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "--help"
    if cmd in ("--help", "-h", ""):
        return show_help()
    if cmd == "--nodes":
        return list_nodes()
    if cmd == "--status":
        return show_status(argv[1] if len(argv) > 1 else None)
    if cmd == "--logs":
        if len(argv) < 2:
            return die("usage: remote-run --logs <job-id>")
        return show_logs(argv[1])
    if cmd == "--tail":
        if len(argv) < 2:
            return die("usage: remote-run --tail <job-id>")
        return tail_logs(argv[1])
    if cmd == "--kill":
        if len(argv) < 2:
            return die("usage: remote-run --kill <job-id>")
        return kill_job(argv[1])
    return submit_job(argv)


if __name__ == "__main__":
    sys.exit(main())
