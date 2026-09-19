#!/bin/bash
# slurm-env.sh -- self-healing Slurm client environment INSIDE the sandbox.
#
# Source it when a Slurm command (squeue, sbatch, sacct) unexpectedly fails:
#
#   source scripts/slurm-env.sh
#
# Normally nothing to do: the launcher generates a client slurm.conf (host
# conf + SlurmUser rewritten to the sandbox user) and exports SLURM_CONF.
# When that is missing -- an image or launcher predating it, or a wiped .tmp
# client conf -- this script rebuilds one in .tmp from any readable host conf:
# SlurmUser is rewritten to the current user (the image has no "slurm" user
# entry, so the host value fails to parse; the plain "name" form is required,
# "name(uid)" is rejected), and PluginDir is redirected to a mounted shim dir
# when the host plugin path is not visible in the sandbox.
#
# Launcher-managed file: overwritten from the instance on every launch.

# Already working? Done.
if sinfo >/dev/null 2>&1; then
    return 0 2>/dev/null || exit 0
fi

__slurm_env() {
    # 1. Find a readable host conf.
    local conf="${SLURM_CONF:-}"
    if [ -z "$conf" ] || [ ! -f "$conf" ]; then
        local c
        for c in /nopt/slurm/etc/slurm.conf /etc/slurm/slurm.conf /etc/slurm-llnl/slurm.conf; do
            [ -f "$c" ] && { conf=$c; break; }
        done
    fi
    if { [ -z "$conf" ] || [ ! -f "$conf" ]; } && scontrol show config >/dev/null 2>&1; then
        conf=$(scontrol show config 2>/dev/null | sed -n 's/^[[:space:]]*Configuration file = //p' | head -1)
    fi
    if [ -z "$conf" ] || [ ! -f "$conf" ]; then
        echo "slurm-env: no readable slurm.conf found" >&2
        return 1
    fi

    # 2. Regenerate the client copy. Scratch goes to $PWD/.tmp: inside the
    #    sandbox PWD is /workspace (whose .tmp the launcher pre-creates);
    #    AGENTIC_WORKSPACE_HOST is a node-side path, not visible in here.
    local root=$PWD
    mkdir -p "$root/.tmp" 2>/dev/null || root=$(mktemp -d)
    local out="$root/.tmp/slurm-client.conf"
    sed "s/^SlurmUser=.*/SlurmUser=$(id -un)/" "$conf" > "$out"

    # 3. PluginDir must point somewhere visible in the sandbox. The host conf
    #    may carry NO PluginDir line at all (compiled-in default, e.g.
    #    Kestrel) -- the clients then look in a host path that is not mounted
    #    and every command dies with "Bad value for PluginDir". Rewrite an
    #    existing broken entry, or append one, pointing at the shim-mounted
    #    dir that actually holds the Slurm plugins (the one with libslurm;
    #    plain system lib dirs also contain *.so files).
    local pd target d so
    pd=$(sed -n 's/^PluginDir=//p' "$out" | head -1 | awk '{print $1}')
    if [ -z "$pd" ] || [ ! -d "$pd" ]; then
        target=""
        for d in /opt/slurm-host/lib/*/ ; do
            d=${d%/}
            for so in "$d"/libslurm*.so* ; do
                [ -e "$so" ] || continue
                target=$d
                break
            done
            [ -n "$target" ] && break
        done
        if [ -z "$target" ]; then
            for d in /opt/slurm-host/lib/*/ ; do
                d=${d%/}
                if ls "$d"/*_*.so >/dev/null 2>&1; then target=$d; break; fi
            done
        fi
        if [ -n "$target" ]; then
            if [ -n "$pd" ]; then
                sed -i "0,/^PluginDir=/s|^PluginDir=.*|PluginDir=$target|" "$out"
            else
                echo "PluginDir=$target" >> "$out"
            fi
        fi
    fi

    export SLURM_CONF="$out"
    if ! sinfo >/dev/null 2>&1; then
        echo "slurm-env: client still fails with SLURM_CONF=$out" >&2
        return 1
    fi
    echo "slurm-env: SLURM_CONF=$out"
}

__slurm_env || return 1 2>/dev/null || exit 1
