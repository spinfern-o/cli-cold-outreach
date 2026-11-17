import plistlib

from companyscout import monitor


def test_plist_runs_monitor_on_the_configured_interval(cfg):
    p = plistlib.loads(monitor.plist(cfg, "/usr/local/bin/outreach"))
    assert p["ProgramArguments"] == ["/usr/local/bin/outreach", "monitor", "run"]
    assert p["StartInterval"] == cfg["monitor_tick_s"] and p["RunAtLoad"] is True
    assert p["EnvironmentVariables"]["OUTREACH_HOME"] == str(monitor.config.home())


def test_start_registers_agent_with_launchd_and_stop_removes_it(cfg, tmp_path):
    calls = []
    run = lambda cmd, **kw: calls.append(cmd)
    path = monitor.start(cfg, "/usr/local/bin/outreach", agents_dir=tmp_path, run=run)
    assert path.exists() and calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][-1] == str(path)
    monitor.stop(agents_dir=tmp_path, run=run)
    assert not path.exists() and calls[-1][:2] == ["launchctl", "bootout"]
