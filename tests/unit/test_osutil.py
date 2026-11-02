import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from bridge import osutil, security


# @pt.id T0041-platform-boundary
# @pt.item Platform operation boundary
# @pt.condition All bridge Python modules except osutil
# @pt.expected POSIX-only references remain in osutil
# @pt.target bridge
# @pt.method unit
# @pt.legacy None
def test_platform_operations_are_centralized():
    bridge_dir = Path(osutil.__file__).parent
    for path in bridge_dir.glob("*.py"):
        if path.name == "osutil.py":
            continue
        source = path.read_text(encoding="utf-8")
        for reference in ("fcntl", "killpg", "start_new_session"):
            assert reference not in source, f"{path.name} contains {reference}"


# @pt.id T0041-lock
# @pt.item Platform locks
# @pt.condition Two POSIX handles or a busy Windows region
# @pt.expected Busy locks raise BlockingIOError
# @pt.target bridge.osutil.lock_nonblocking
# @pt.method unit
# @pt.legacy None
def test_locks(tmp_path, monkeypatch):
    if not osutil.WINDOWS:
        with (tmp_path / "lock").open("a") as first, (tmp_path / "lock").open("a") as second:
            osutil.lock_nonblocking(first)
            with pytest.raises(BlockingIOError):
                osutil.lock_nonblocking(second)
    locking = Mock(side_effect=PermissionError(13, "locked"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=locking))
    monkeypatch.setattr(osutil, "WINDOWS", True)
    handle = Mock()
    handle.fileno.return_value = 42
    with pytest.raises(BlockingIOError):
        osutil.lock_nonblocking(handle)
    handle.seek.assert_called_once_with(0)
    locking.assert_called_once_with(42, 2, 1)


# @pt.id T0041-kill
# @pt.item Windows process trees
# @pt.condition Graceful forced and missing process
# @pt.expected taskkill receives tree and optional force flags
# @pt.target bridge.osutil.kill_group
# @pt.method unit
# @pt.legacy None
def test_windows_kill(monkeypatch):
    monkeypatch.setattr(osutil, "WINDOWS", True)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(osutil.subprocess, "run", run)
    for force in (False, True):
        osutil.kill_group(42, force)
        run.assert_called_with(["taskkill", "/PID", "42", "/T", *(["/F"] if force else [])],
                               capture_output=True, check=False)
    run.return_value.returncode = 128
    with pytest.raises(ProcessLookupError):
        osutil.kill_group(42)


# @pt.id T0042-windows-lock
# @pt.item Blocking Windows lock
# @pt.condition Windows region locking succeeds or fails
# @pt.expected Seek before LK_LOCK of one byte and propagate OSError
# @pt.target bridge.osutil.lock
# @pt.method unit
# @pt.legacy None
def test_windows_blocking_lock(monkeypatch):
    calls = Mock()
    handle = calls.handle
    handle.fileno.return_value = 42
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_LOCK=1, locking=calls.locking))
    monkeypatch.setattr(osutil, "WINDOWS", True)
    osutil.lock(handle)
    assert calls.mock_calls == [call.handle.seek(0), call.handle.fileno(), call.locking(42, 1, 1)]
    calls.locking.side_effect = OSError("busy")
    with pytest.raises(OSError, match="busy"):
        osutil.lock(handle)


# @pt.id T0041-alive
# @pt.item Windows process liveness
# @pt.condition Running exited inaccessible and query failure
# @pt.expected Query only and always close opened handles
# @pt.target bridge.osutil.alive
# @pt.method unit
# @pt.legacy None
def test_windows_alive(monkeypatch):
    monkeypatch.setattr(osutil, "WINDOWS", True)
    kill = Mock(side_effect=AssertionError("must not signal"))
    monkeypatch.setattr(osutil.os, "kill", kill)
    kernel = SimpleNamespace(OpenProcess=Mock(return_value=123),
                             GetExitCodeProcess=Mock(), CloseHandle=Mock())
    monkeypatch.setattr(osutil.ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    for code in (259, 0):
        def query(handle, pointer):
            pointer._obj.value = code
            return 1
        kernel.GetExitCodeProcess.side_effect = query
        assert osutil.alive(42) is (code == 259)
        kernel.OpenProcess.assert_called_with(0x1000, False, 42)
        kernel.CloseHandle.assert_called_with(123)
    kernel.GetExitCodeProcess.side_effect = RuntimeError("query")
    with pytest.raises(RuntimeError):
        osutil.alive(42)
    assert kernel.CloseHandle.call_count == 3
    kernel.OpenProcess.return_value = 0
    assert not osutil.alive(42)
    assert not osutil.alive(None)
    kill.assert_not_called()


# @pt.id T0041-processes
# @pt.item Process discovery
# @pt.condition POSIX output and Windows JSON list or object
# @pt.expected Parse ancestry and return empty on failure
# @pt.target bridge.osutil.list_processes bridge.security.process_started
# @pt.method unit
# @pt.legacy None
def test_process_discovery(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout="10 1 agent-bridge\nbad\nx y invalid\n"))
    monkeypatch.setattr(osutil.subprocess, "run", run)
    monkeypatch.setattr(osutil, "WINDOWS", False)
    assert osutil.list_processes() == {10: (1, "agent-bridge")}
    run.assert_called_with(["ps", "-axo", "pid=,ppid=,command="],
                           capture_output=True, text=True, check=True)
    monkeypatch.setattr(osutil, "WINDOWS", True)
    row = {"ProcessId": 10, "ParentProcessId": 1, "CommandLine": "agent-bridge"}
    for rows in (row, [row]):
        run.return_value.stdout = json.dumps(rows)
        assert osutil.list_processes() == {10: (1, "agent-bridge")}
        assert run.call_args.args[0][:3] == ["powershell", "-NoProfile", "-Command"]
    run.return_value.stdout = "invalid"
    assert osutil.list_processes() == {}
    run.side_effect = OSError("missing")
    assert osutil.list_processes() == {}
    run.reset_mock()
    assert security.process_started(10) is None
    run.assert_not_called()
