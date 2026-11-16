"""Install-time helpers, run by the installer through command-line flags.

    ThermalGuard.exe --register-task   start at every logon, elevated, no UAC prompt
    ThermalGuard.exe --unregister      remove that task, restore Balanced, delete the plan

Task Scheduler rather than the Startup folder or a Run key: changing power
settings needs elevation, and only a task can start elevated at logon without
a UAC prompt. The task is bound to DOMAIN\\user, not the bare username, which
on domain-joined PCs resolves to the wrong account or none.
"""
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

import psutil

TASK_NAME = "ThermalGuard"
CREATE_NO_WINDOW = 0x08000000

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Switches quiet/balanced/performance power modes by CPU load and temperature</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT20S</Delay>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def current_user():
    domain, name = os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")
    return f"{domain}\\{name}" if domain else name


def task_xml(exe, user):
    return TASK_XML.format(user=escape(user), exe=escape(exe), workdir=escape(os.path.dirname(exe)))


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW)


def register(log):
    exe = os.path.abspath(sys.executable)
    user = current_user()
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(task_xml(exe, user))
        result = _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
    finally:
        os.remove(path)
    if result.returncode != 0:
        log.error("Could not create the logon task for %s: %s", user, (result.stdout + result.stderr).strip())
        return 1
    log.info("Logon task created for %s -> %s", user, exe)
    return 0


def stop_other_instances():
    me = os.getpid()
    _schtasks("/End", "/TN", TASK_NAME)
    procs = [p for p in psutil.process_iter(["name"])
             if (p.info["name"] or "").lower() == "thermalguard.exe" and p.pid != me]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)


def unregister(log):
    from power import BALANCED, SCHEME, PowerError, _powercfg, active_scheme

    stop_other_instances()
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    try:
        if active_scheme() == SCHEME:
            _powercfg("/setactive", BALANCED)
        if SCHEME in _powercfg("/list").lower():
            _powercfg("/delete", SCHEME)
    except PowerError as exc:
        log.error("Could not remove the ThermalGuard power plan: %s", exc)
        return 1
    log.info("Logon task and ThermalGuard power plan removed; Balanced is active")
    return 0
