"""launchd registration for the background news agent."""
import os
import plistlib
import subprocess
from pathlib import Path

from . import config

LABEL = "com.companyscout.monitor"
AGENTS = Path.home() / "Library" / "LaunchAgents"


def plist(cfg, exe: str) -> bytes:
    claude_dir = str(Path(cfg["claude_path"]).expanduser().parent) if "/" in cfg["claude_path"] else str(Path.home() / ".local/bin")
    logs = config.home() / "logs"
    logs.mkdir(exist_ok=True)
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [exe, "monitor", "run"],
        "StartInterval": cfg["monitor_tick_s"],  # launchd coalesces intervals missed during sleep into one run on wake
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "monitor.out.log"),
        "StandardErrorPath": str(logs / "monitor.err.log"),
        "EnvironmentVariables": {"OUTREACH_HOME": str(config.home()),
                                 "PATH": f"{claude_dir}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    })


def _target() -> str:
    return f"gui/{os.getuid()}"


def start(cfg, exe: str, agents_dir: Path = AGENTS, run=subprocess.run) -> Path:
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{LABEL}.plist"
    path.write_bytes(plist(cfg, exe))
    run(["launchctl", "bootout", f"{_target()}/{LABEL}"], capture_output=True)  # replace any earlier registration
    run(["launchctl", "bootstrap", _target(), str(path)], capture_output=True, check=True)
    return path


def stop(agents_dir: Path = AGENTS, run=subprocess.run) -> None:
    path = agents_dir / f"{LABEL}.plist"
    path.unlink(missing_ok=True)
    run(["launchctl", "bootout", f"{_target()}/{LABEL}"], capture_output=True)


def loaded(run=subprocess.run) -> bool:
    return run(["launchctl", "print", f"{_target()}/{LABEL}"], capture_output=True).returncode == 0
