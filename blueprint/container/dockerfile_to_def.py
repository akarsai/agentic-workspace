#!/usr/bin/env python3
"""dockerfile_to_def.py — convert a Dockerfile into an Apptainer definition.

Lets the agentic-workspace images (base + instance layers) be built with
Apptainer on hosts that have Apptainer but no Docker (e.g. HPC clusters).
Apptainer cannot build a Dockerfile directly with the local two-layer
layout (instance layers reference the local base image), so each layer's
Dockerfile is translated into a self-contained .def recipe.

Two modes:

  --mode base
      Produce a recipe that bootstraps from a remote OCI image
      (Bootstrap: docker + the Dockerfile's FROM image).

  --mode instance --base-sif PATH
      Produce a recipe layered on an already-built base SIF
      (Bootstrap: localimage + From: PATH).

Supported Dockerfile instructions (the subset used by the framework):
FROM, ARG (ignored -- values are substituted into RUN lines via --build-arg),
SHELL (ignored), ENV, RUN (multi-line with trailing backslash), COPY,
WORKDIR, ENTRYPOINT, CMD. Unknown instructions are ignored with a warning.

--build-arg KEY=VALUE expands ARG references in RUN commands textually
(${KEY}, $KEY, and the ${KEY:-fallback} / ${KEY:+word} forms) with the given
value, mirroring how Docker exposes build args to the shell. The tool pins
from blueprint/container/versions.json reach Apptainer builds this way.

Usage:
  dockerfile_to_def.py --mode base DOCKERFILE
  dockerfile_to_def.py --mode instance --base-sif PATH [--default-entrypoint CMD] DOCKERFILE
  dockerfile_to_def.py ... [--build-arg KEY=VALUE ...] [--out OUT]
"""

import argparse
import re
import shlex
import sys


def join_continuations(raw_lines):
    """Join RUN/COPY lines continued with a trailing backslash."""
    lines = []
    buf = None
    for line in raw_lines:
        stripped = line.strip()
        if buf is None:
            if stripped.endswith("\\"):
                buf = stripped[:-1]
            else:
                lines.append(stripped)
        else:
            if stripped.endswith("\\"):
                buf += " " + stripped[:-1]
            else:
                lines.append(buf + " " + stripped)
                buf = None
    if buf is not None:
        lines.append(buf)
    return lines


def parse_env_pairs(rest):
    """Parse an ENV line into (KEY, VALUE) pairs (quoted values supported)."""
    pairs = []
    for token in shlex.split(rest):
        if "=" in token:
            key, _, value = token.partition("=")
            pairs.append((key, value))
    return pairs


def parse_array(rest):
    """Parse ENTRYPOINT/CMD into an arg list (JSON-array or plain form)."""
    rest = rest.strip()
    if rest.startswith("["):
        inner = rest[rest.find("[") + 1 : rest.rfind("]")]
        return shlex.split(inner)
    return shlex.split(rest)


def tar_no_same_owner(command):
    """Add --no-same-owner to tar extractions.

    GNU tar restores file ownership by default when run as root. In Apptainer's
    root-mapped namespace (no fakeroot/subuid) only UID 0 is mapped, so chowning
    extracted files to the tarball's original owner (e.g. uid 1000 for the node
    tarball) fails with EINVAL and the extraction is discarded. --no-same-owner
    keeps the extracted files owned by the build user. Mirrors the reference
    repo's .def recipes.
    """
    return re.sub(r"\btar\s+(-[a-zA-Z]*x)", r"tar --no-same-owner \1", command)


def substitute_args(command: str, args: dict[str, str]) -> str:
    """Expand ARG references in a RUN command with concrete values.

    Docker makes build args environment variables for RUN; the def recipe
    has no ARG mechanism, so the values are substituted textually instead.
    Longest keys first so one arg can never clobber another that shares a
    prefix ($PI_VERSION before $PI, say). re.sub gets lambdas so backslashes
    in a value are not read as escapes.
    """
    for key in sorted(args, key=len, reverse=True):
        value = args[key]
        pattern = re.escape(key)
        # ${KEY:+word} -> word (the "if set" branch is taken: KEY is set)
        command = re.sub(r"\$\{" + pattern + r":\+([^}]*)\}", lambda m: m.group(1), command)
        # ${KEY:-fallback} -> value (a pin never wants the fallback)
        command = re.sub(r"\$\{" + pattern + r":-[^}]*\}", lambda m: value, command)
        # ${KEY} -> value
        command = command.replace("${" + key + "}", value)
        # $KEY -> value (word boundary so $KEY_SUFFIX is untouched)
        command = re.sub(r"\$" + pattern + r"\b", lambda m: value, command)
    return command


def convert(dockerfile, mode, base_sif, default_entrypoint, build_args=None):
    with open(dockerfile) as fh:
        raw_lines = fh.read().splitlines()

    from_image = None
    env_pairs = []
    post_lines = []  # ordered %post body: export + command lines in source order
    copy_pairs = []
    workdir = None
    entrypoint = None
    cmd = None

    for line in join_continuations(raw_lines):
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        directive = parts[0].upper()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if directive == "FROM":
            from_image = rest.split()[0]
        elif directive in ("ARG", "SHELL"):
            continue
        elif directive == "ENV":
            pairs = parse_env_pairs(rest)
            env_pairs.extend(pairs)
            # Docker applies ENV only to subsequent RUN steps; mirror that by
            # exporting at this position in the script, not hoisted to the top.
            for key, value in pairs:
                post_lines.append(f"    export {key}={value}")
        elif directive == "RUN":
            # Apptainer ignores `set -e` in %post, so fail fast per step with an
            # explicit `|| exit 1` (a `cmd && ...` chain short-circuits to it).
            # ARG values are folded in before the recipe is written out.
            command = substitute_args(tar_no_same_owner(rest), build_args or {})
            post_lines.append(f"    {command} || exit 1")
        elif directive == "COPY":
            tokens = rest.split()
            if len(tokens) < 2:
                continue
            dst = tokens[-1]
            for src in tokens[:-1]:
                copy_pairs.append((src, dst))
        elif directive == "WORKDIR":
            workdir = rest
        elif directive == "ENTRYPOINT":
            entrypoint = parse_array(rest)
        elif directive == "CMD":
            cmd = parse_array(rest)
        else:
            print(f"Warning: ignoring Dockerfile instruction: {directive}", file=sys.stderr)

    out = []
    if mode == "base":
        if not from_image:
            print(f"Error: no FROM image found in {dockerfile}", file=sys.stderr)
            sys.exit(1)
        out.append("Bootstrap: docker")
        out.append(f"From: {from_image}")
    else:
        if not base_sif:
            print("Error: --mode instance requires --base-sif", file=sys.stderr)
            sys.exit(1)
        out.append("Bootstrap: localimage")
        out.append(f"From: {base_sif}")
    out.append("")

    if post_lines:
        out.append("%post")
        # Fail fast like Docker's per-RUN abort, and run apt-get as root:
        # in Apptainer's root-mapped namespace (no fakeroot/subuid) apt cannot
        # drop to its "_apt" sandbox user, so package installs fail. The
        # config file is removed at the end of %post, so the final image is
        # identical to a Docker build.
        out.append("    set -e")
        out.append("    if [ -n \"$BASH_VERSION\" ]; then set -o pipefail; fi")
        out.append("    mkdir -p /etc/apt/apt.conf.d")
        out.append('    printf \'APT::Sandbox::User "root";\\n\' > /etc/apt/apt.conf.d/99-agentic-root-apt')
        # WORKAROUND (remove when Apptainer fakeroot/subuid is available):
        # In Apptainer's root-mapped namespace only UID/GID 0 are mapped, so a
        # package postinst doing `chown root:<group>` or `chgrp <group>` to any
        # other group fails (EINVAL) and dpkg aborts (e.g. fontconfig-config's
        # `chown root:staff /usr/local/share/fonts`). Rewriting every existing
        # system group in /etc/group to GID 0 makes those chowns resolve to the
        # one mapped GID and succeed. Applies to both base and instance recipes.
        # Harmless on Docker (never uses this recipe).
        out.append("    # WORKAROUND-BEGIN: map system groups to GID 0 (root-mapped namespace)")
        out.append("    sed -i -E 's/^([^:]+):([^:]+):[0-9]+:/\\1:\\2:0:/' /etc/group")
        out.append("    # WORKAROUND-END")
        if mode == "base":
            # WORKAROUND (remove when Apptainer fakeroot/subuid is available):
            # openssh-client's postinst does `chgrp _ssh /usr/bin/ssh-agent`.
            # In Apptainer's root-mapped namespace only UID/GID 0 are mapped,
            # so chgrp to the _ssh group's GID fails (EINVAL) and dpkg aborts.
            # A dpkg-statoverride with group `root` (GID 0, mapped) makes dpkg
            # apply root:root ownership at unpack and makes the postinst skip
            # its addgroup/chgrp/chmod. ssh-agent ends up setgid-root instead
            # of setgid-_ssh, which is functionally identical for the sandbox.
            # The _ssh group is still pre-created so the postinst's `addgroup`
            # is skipped. Harmless on Docker (never uses this recipe).
            out.append("    # WORKAROUND-BEGIN: openssh-client _ssh group (root-mapped namespace)")
            out.append("    getent group _ssh >/dev/null 2>&1 \\")
            out.append("        || addgroup --system --quiet --force-badname _ssh 2>/dev/null \\")
            out.append("        || echo '_ssh:x:117:' >> /etc/group")
            out.append("    dpkg-statoverride --list /usr/bin/ssh-agent >/dev/null 2>&1 \\")
            out.append("        || dpkg-statoverride --add root root 2755 /usr/bin/ssh-agent 2>/dev/null || true")
            out.append("    # WORKAROUND-END")
        out.extend(post_lines)
        if workdir:
            # Docker's WORKDIR creates the directory; Apptainer's %post has no
            # WORKDIR, so create it before cd (cd would otherwise fail-fast).
            out.append(f"    mkdir -p {workdir} || exit 1")
            out.append(f"    cd {workdir} || exit 1")
        out.append("    rm -f /etc/apt/apt.conf.d/99-agentic-root-apt")
        out.append("")

    if copy_pairs:
        out.append("%files")
        for src, dst in copy_pairs:
            out.append(f"    {src} {dst}")
        out.append("")

    if env_pairs:
        out.append("%environment")
        for key, value in env_pairs:
            out.append(f"    export {key}={value}")
        out.append("")

    runscript = entrypoint or cmd
    if not runscript and default_entrypoint:
        runscript = shlex.split(default_entrypoint)
    if runscript:
        out.append("%runscript")
        if workdir:
            out.append(f"    cd {workdir}")
        quoted = " ".join(shlex.quote(arg) for arg in runscript)
        out.append(f'    exec {quoted} "$@"')
        out.append("")

    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["base", "instance"])
    parser.add_argument("--base-sif", help="path of the base SIF (instance mode)")
    parser.add_argument(
        "--default-entrypoint",
        help="runscript used when the Dockerfile has no ENTRYPOINT/CMD",
    )
    parser.add_argument(
        "--build-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="substitute ARG references in RUN commands (repeatable)",
    )
    parser.add_argument("--out", help="write to file instead of stdout")
    parser.add_argument("dockerfile", help="path to the Dockerfile")
    args = parser.parse_args(argv)

    build_args: dict[str, str] = {}
    for pair in args.build_arg:
        if "=" not in pair:
            parser.error(f"--build-arg expects KEY=VALUE, got: {pair}")
        key, _, value = pair.partition("=")
        build_args[key] = value

    content = convert(
        args.dockerfile,
        args.mode,
        args.base_sif,
        args.default_entrypoint,
        build_args,
    )
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(content)
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
