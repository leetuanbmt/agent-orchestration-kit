"""Write confinement for verification commands: Linux Landlock and macOS Seatbelt.

Verification runs code the executor wrote. Under Landlock the command may write only to the
worktree, a private temporary directory and the human-configured extra paths; reads are not
restricted. `strict` also denies TCP connect and bind (Landlock ABI 4+).

The confining wrapper runs as `python -I -c WRAPPER`, a string fixed when the bridge starts: it
imports nothing from disk, so an agent that edits files in the worktree (even this module, when
the kit works on itself) cannot switch the confinement off.
"""

from __future__ import annotations

import ctypes
import sys
import subprocess
from functools import cache
from pathlib import Path
from typing import Literal

Mode = Literal["auto", "strict", "none"]

WRAPPER = r'''
import ctypes, os, struct, sys
args = sys.argv[1:]
cut = args.index("--")
writable, network, cmd = args[:cut - 1], args[cut - 1] == "net", args[cut + 1:]
libc = ctypes.CDLL(None, use_errno=True)
abi = libc.syscall(444, None, 0, 1)
if abi < 1:
    sys.exit("agent-bridge sandbox: Landlock is not available on this kernel")
fs = sum(1 << b for b in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12))  # every way to write or create
fs |= (1 << 13 if abi >= 2 else 0) | (1 << 14 if abi >= 3 else 0)
net = 0 if network or abi < 4 else 3  # TCP bind and connect
attr = struct.pack("QQ", fs, net) if abi >= 4 else struct.pack("Q", fs)
ruleset = libc.syscall(444, ctypes.create_string_buffer(attr, len(attr)), len(attr), 0)
if ruleset < 0:
    sys.exit(f"agent-bridge sandbox: landlock_create_ruleset failed ({ctypes.get_errno()})")
for path, rights in [*((p, fs) for p in writable), ("/dev", 1 << 1)]:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        continue
    rule = ctypes.create_string_buffer(struct.pack("=Qi", rights, fd), 12)
    if libc.syscall(445, ruleset, 1, rule, 0) < 0:
        sys.exit(f"agent-bridge sandbox: cannot allow {path} ({ctypes.get_errno()})")
    os.close(fd)
if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, ruleset, 0):
    sys.exit(f"agent-bridge sandbox: landlock_restrict_self failed ({ctypes.get_errno()})")
os.close(ruleset)
os.execvp(cmd[0], cmd)
'''


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, 0 when it has none (or this is not Linux)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return max(int(libc.syscall(444, None, 0, 1)), 0)
    except (OSError, AttributeError):
        return 0


@cache
def _seatbelt_available() -> bool:
    try:
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True, check=False,
        ).returncode == 0
    except OSError:
        return False


def backend() -> Literal["landlock", "seatbelt"] | None:
    if landlock_abi() > 0:
        return "landlock"
    if (sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists()
            and _seatbelt_available()):
        return "seatbelt"
    return None


def _seatbelt_profile(writable: list[str], mode: Mode) -> str:
    rules = []
    for path in writable:
        resolved = str(Path(path).resolve())
        if any(c in ('"', chr(92)) or ord(c) < 32 or ord(c) == 127
               for c in str(path) + resolved):
            raise ValueError("invalid Seatbelt writable path")
        rules.append(f'(subpath "{resolved}")')
    profile = ('(version 1)(allow default)(deny file-write*)(allow file-write* '
               + " ".join(rules)
               + ' (literal "/dev/null") (regex #"^/dev/(tty|fd/|stdout|stderr|ptmx|ttys)"))')
    # (allow default) keeps toolchains working; these close known launch-service escapes and the
    # Keychain, where an account token may live (`credential: keychain`). Denying the `security`
    # binary alone is not enough: Security.framework through ctypes reached it (probed), so the
    # Security services and the keychain files are denied too.
    # ponytail: an allow-default profile is not a full sandbox; upgrade to a deny-default allowlist.
    keychains = str((Path.home() / "Library" / "Keychains").resolve())
    if any(c in ('"', chr(92)) or ord(c) < 32 or ord(c) == 127 for c in keychains):
        raise ValueError("invalid Seatbelt keychain path")
    profile += ('(deny appleevent-send)(deny lsopen)'
                '(deny process-exec (literal "/usr/bin/open") (literal "/bin/launchctl")'
                ' (literal "/usr/bin/osascript") (literal "/usr/bin/security"))'
                '(deny mach-lookup (global-name "com.apple.SecurityServer")'
                ' (global-name "com.apple.securityd"))'
                f'(deny file-read* (subpath "{keychains}"))')
    if mode == "strict":
        profile += '(deny network-outbound (remote ip))(deny network-bind (local ip))'
    return profile


def wrap(argv: list[str], writable: list[str], mode: Mode) -> list[str] | None:
    """argv run under the sandbox, the argv itself for `none` (or `auto` without a sandbox), and
    None when `strict` cannot be honoured: the check must then fail rather than run unconfined."""
    if mode == "none":
        return argv
    selected = backend()
    if selected is None:
        return None if mode == "strict" else argv
    if selected == "seatbelt":
        return ["/usr/bin/sandbox-exec", "-p", _seatbelt_profile(writable, mode), *argv]
    return [sys.executable, "-I", "-c", WRAPPER, *writable,
            "net" if mode == "auto" else "nonet", "--", *argv]


def describe(mode: Mode) -> str:
    """One line for health: what verification commands run under here."""
    if mode == "none":
        return "none (verify_sandbox: none)"
    selected = backend()
    if selected is None:
        return ("unavailable: checks are refused (strict)" if mode == "strict" else
                "unavailable on this system: checks run unconfined (auto)")
    if selected == "seatbelt":
        return "seatbelt (sandbox-exec): writes confined, best effort" + (", TCP denied" if mode == "strict" else "")
    abi = landlock_abi()
    return f"landlock ABI {abi}: writes confined" + (", TCP denied" if mode == "strict" and abi >= 4
                                                     else "")
