"""Platform-specific locking and process operations."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess

WINDOWS = os.name == "nt"
GROUP_KWARGS = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
                if WINDOWS else {"start_new_session": True})


def lock(handle):
    if WINDOWS:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX)


def lock_nonblocking(handle):
    if WINDOWS:
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(*exc.args) from exc
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def kill_group(pid: int, force: bool = False):
    if not WINDOWS:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return
    result = subprocess.run(["taskkill", "/PID", str(pid), "/T", *(["/F"] if force else [])],
                            capture_output=True, check=False)
    if result.returncode == 128:
        raise ProcessLookupError(pid)


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def list_processes() -> dict[int, tuple[int, str]]:
    try:
        if WINDOWS:
            result = subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress",
            ], capture_output=True, text=True, check=True)
            rows = json.loads(result.stdout)
            if isinstance(rows, dict):
                rows = [rows]
            return {int(row["ProcessId"]): (int(row["ParentProcessId"]), row["CommandLine"] or "")
                    for row in rows}
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                capture_output=True, text=True, check=True)
        processes = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3:
                try:
                    processes[int(parts[0])] = (int(parts[1]), parts[2])
                except ValueError:
                    continue
        return processes
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return {}
