#!/usr/bin/env python3
"""dispatcher.py: host-side daemon that watches for job requests from the
containerized agent and dispatches them to remote nodes via srun + apptainer.

Started by the launcher (--multi-node) before launching the container; killed
automatically when the container exits.

The key property that makes Slurm work from inside the sandbox: every remote
job re-enters the same Apptainer image with the host workspace bind-mounted at
/workspace, so /workspace paths resolve identically on the compute node
instead of referencing a login-node-only bind mount.

Usage: dispatcher.py <dispatch-config-file>   (JSON, written by the launcher)

Pure stdlib (host daemon).
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def log(dispatch_dir: Path, msg: str) -> None:
    with open(dispatch_dir / "dispatcher.log", "a") as f:
        f.write(f"[dispatcher {time.strftime('%H:%M:%S')}] {msg}\n")


def build_apptainer_args(conf: dict) -> list[str]:
    args = [
        "--nv",
        "--no-mount", "home",
        "--home", "/agentic-home",
        "--bind", f"{conf['workspace_host']}:/workspace",
        "--bind", f"{conf['uv_cache_dir']}:/uv-cache",
        "--bind", f"{conf['uv_python_install_dir']}:/uv-python",
        "--bind", f"{conf['uv_tool_dir']}:/uv-tools",
        "--bind", f"{conf['state_root']}:{conf['state_root']}",
        "--pwd", "/workspace",
        "--env", "UV_CACHE_DIR=/uv-cache",
        "--env", "UV_PYTHON_INSTALL_DIR=/uv-python",
        "--env", "UV_TOOL_DIR=/uv-tools",
        "--env", "UV_LINK_MODE=symlink",
        "--env", f"HF_HOME={conf['hf_home']}",
        "--env", f"TRITON_CACHE_DIR={conf['triton_cache_dir']}",
        "--env", f"WANDB_DIR={conf['wandb_dir']}",
        "--env", f"TERM={os.environ.get('TERM', 'xterm-256color')}",
    ]
    https_proxy = conf.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if https_proxy:
        http_proxy = conf.get("http_proxy") or os.environ.get("HTTP_PROXY") or https_proxy
        args += ["--env", f"https_proxy={https_proxy}", "--env", f"http_proxy={http_proxy}"]
    if os.path.isdir("/scratch/local"):
        args += ["--bind", "/scratch/local:/scratch/local"]
    return args


def run_job(conf: dict, jobs_dir: Path, job_id: str, ap_args: list[str]) -> None:
    job_file = jobs_dir / f"{job_id}.job"
    try:
        job = json.loads(job_file.read_text())
    except (OSError, json.JSONDecodeError):
        log(Path(conf["dispatch_dir"]), f"Job {job_id}: INVALID (unreadable job file)")
        (jobs_dir / f"{job_id}.status").write_text("failed:invalid")
        return
    node = job.get("node", "")
    gpus = job.get("gpus", "4")
    cmd = job.get("cmd", "")
    workdir = job.get("workdir", "/workspace")
    dispatch_dir = Path(conf["dispatch_dir"])

    if not node or not cmd:
        log(dispatch_dir, f"Job {job_id}: INVALID (missing node or cmd)")
        (jobs_dir / f"{job_id}.status").write_text("failed:invalid")
        return

    log(dispatch_dir, f"Job {job_id}: node={node} gpus={gpus} cmd='{cmd}'")
    (jobs_dir / f"{job_id}.status").write_text("running")

    with open(jobs_dir / f"{job_id}.stdout", "wb") as out, open(jobs_dir / f"{job_id}.stderr", "wb") as err:
        proc = subprocess.Popen(
            [
                "srun", "--overlap", "--nodes=1", "--ntasks=1", f"--nodelist={node}",
                f"--gres=gpu:{gpus}", "--cpu-bind=none",
                "apptainer", "exec", *ap_args, "--pwd", workdir,
                conf["container_image"],
                "bash", "-c", f"cd {workdir} && {cmd}",
            ],
            stdout=out,
            stderr=err,
        )
    (jobs_dir / f"{job_id}.pid").write_text(str(proc.pid))
    log(dispatch_dir, f"Job {job_id}: launched (pid={proc.pid})")

    def wait_and_finish() -> None:
        rc = proc.wait()
        (jobs_dir / f"{job_id}.status").write_text(f"done:{rc}")
        log(dispatch_dir, f"Job {job_id}: finished (exit={rc})")

    threading.Thread(target=wait_and_finish, daemon=True).start()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: dispatcher.py <dispatch-config-file>", file=sys.stderr)
        return 1
    conf_file = Path(sys.argv[1])
    conf = json.loads(conf_file.read_text())

    dispatch_dir = Path(conf["dispatch_dir"])
    jobs_dir = dispatch_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Write node list for the agent.
    nodelist = conf["slurm_job_nodelist"]
    nodes = subprocess.run(
        ["scontrol", "show", "hostnames", nodelist], capture_output=True, text=True, check=True
    ).stdout.split()
    (dispatch_dir / "nodes.txt").write_text("\n".join(nodes) + "\n")
    (dispatch_dir / "head_node.txt").write_text(conf["head_node"] + "\n")

    log(dispatch_dir, f"Started. Config: {conf_file}")
    log(dispatch_dir, f"Nodes: {' '.join(nodes)}")
    log(dispatch_dir, f"Head node: {conf['head_node']}")
    log(dispatch_dir, f"Container: {conf['container_image']}")

    ap_args = build_apptainer_args(conf)
    log(dispatch_dir, "Apptainer args built OK")

    seen: set[str] = set()
    log(dispatch_dir, "Entering main loop")
    while True:
        for job_file in sorted(jobs_dir.glob("*.job")):
            job_id = job_file.stem
            if job_id in seen:
                continue
            seen.add(job_id)
            run_job(conf, jobs_dir, job_id, ap_args)

        for kill_file in jobs_dir.glob("*.kill"):
            job_id = kill_file.stem
            try:
                pid = int(kill_file.read_text().strip())
            except ValueError:
                kill_file.unlink(missing_ok=True)
                continue
            log(dispatch_dir, f"Kill request for job {job_id} (pid={pid})")
            try:
                os.kill(pid, 15)
                log(dispatch_dir, f"Killed pid {pid}")
            except ProcessLookupError:
                log(dispatch_dir, f"pid {pid} already dead")
            (jobs_dir / f"{job_id}.status").write_text("killed")
            kill_file.unlink(missing_ok=True)

        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
