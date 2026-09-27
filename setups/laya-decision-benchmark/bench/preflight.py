#!/usr/bin/env python3
"""Phase 0 preflight: machine, memory, Python, Claude CLI, Ollama, data repos, HF → preflight.json."""
import json, os, pathlib, platform, shutil, subprocess, sys, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import SETUP, runlog  # noqa: E402

HOME = pathlib.Path.home()


def sh(*c):
    p = subprocess.run(c, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def find_vault():
    roots = [os.environ.get("ACS_VAULT"), HOME / "Developer", HOME / "code", HOME / "src",
             HOME / "Documents", HOME / "Obsidian", HOME / "repos", HOME]
    for r in filter(None, roots):
        r = pathlib.Path(r)
        for cand in [r, *r.glob("*"), *r.glob("*/*")]:
            if (cand / "_MOC.md").exists() and (cand / "Projects/acs-task-manager").is_dir():
                return str(cand)
    return None


def main():
    free = shutil.disk_usage(HOME).free / 1e9
    mp = sh("memory_pressure")
    vault = find_vault()
    tm = next((str(p) for p in [HOME / "repos/acs-task-manager", HOME / "code/acs-task-manager"]
               if (p / "cli/tracker").exists()), None)
    ollama = sh("ollama", "list")
    try:
        hf = urllib.request.urlopen("https://huggingface.co", timeout=10).status
    except Exception as e:  # noqa: BLE001
        hf = str(e)
    out = {
        "arch": platform.machine(), "chip": sh("sysctl", "-n", "machdep.cpu.brand_string"),
        "ram_gb": int(sh("sysctl", "-n", "hw.memsize")) / 2**30,
        "perf_cores": int(sh("sysctl", "-n", "hw.perflevel0.physicalcpu")),
        "memory_free_pct": (mp or "").splitlines()[-1] if mp else None,
        "disk_free_gb": round(free, 1), "python": sys.version.split()[0],
        "claude": sh("claude", "--version"), "uv": shutil.which("uv"),
        "ollama_models": [l.split()[0] for l in (ollama or "").splitlines()[1:]],
        "hf_reachable": hf,
        "paths": {"vault": vault, "task_manager": tm, "skills_source": str(HOME / ".claude/skills")},
    }
    assert out["arch"] == "arm64" and free >= 8, "machine gate"
    assert vault, "vault not found (hard stop)"
    # Committed file: write home-relative paths (the absolute home dir carries the login name).
    (SETUP / "preflight.json").write_text(json.dumps(out, indent=2).replace(str(HOME), "~"))
    runlog("phase0 preflight: " + json.dumps({k: out[k] for k in ("chip", "ram_gb", "perf_cores",
                                                                   "memory_free_pct", "disk_free_gb")}))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
