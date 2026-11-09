"""Seatbelt profiles and macOS process identification."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from bridge import sandbox
from bridge.security import process_started

needs_seatbelt = pytest.mark.skipif(
    sandbox.backend() != "seatbelt", reason="Seatbelt unavailable (including nested sandboxes)"
)


# @pt.id KIT-SEATBELT-001-profile
# @pt.item Seatbelt argv and resolved writable profile
# @pt.condition auto and strict with a symlinked writable directory
# @pt.expected absolute sandbox-exec argv, resolved paths, device rules and strict-only TCP denies
# @pt.target bridge.sandbox.wrap, bridge.sandbox.describe
# @pt.method unit
# @pt.legacy macOS verification ran unconfined
@pytest.mark.parametrize("mode", ["auto", "strict"])
def test_seatbelt_profile(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(sandbox, "backend", lambda: "seatbelt")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    argv = ["python3", "-c", "raise SystemExit(7)"]
    wrapped = sandbox.wrap(argv, [str(link)], mode)
    assert wrapped[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert wrapped[3:] == argv
    profile = wrapped[2]
    assert '(version 1)(allow default)(deny file-write*)' in profile
    assert f'(subpath "{target.resolve()}")' in profile
    assert str(link) not in profile
    assert '(literal "/dev/null")' in profile
    assert '(regex #"^/dev/(tty|fd/|stdout|stderr|ptmx|ttys)")' in profile
    assert '(deny appleevent-send)' in profile
    assert '(deny lsopen)' in profile
    assert ('(deny process-exec (literal "/usr/bin/open") (literal "/bin/launchctl")'
            ' (literal "/usr/bin/osascript") (literal "/usr/bin/security"))') in profile
    assert ('(deny mach-lookup (global-name "com.apple.SecurityServer")'
            ' (global-name "com.apple.securityd"))') in profile
    assert f'(deny file-read* (subpath "{(Path.home() / "Library/Keychains").resolve()}"))' in profile
    denies = '(deny network-outbound (remote ip))(deny network-bind (local ip))'
    assert (denies in profile) == (mode == "strict")
    assert sandbox.describe(mode) == (
        "seatbelt (sandbox-exec): writes confined, best effort" + (", TCP denied" if mode == "strict" else "")
    )


# @pt.id KIT-SEATBELT-009-git-internals
# @pt.item The profile keeps the repository's config, hooks and info unwritable inside the worktree
# @pt.condition a worktree root allowed for writes and the deny paths of its repository
# @pt.expected the root stays in the allow rule; config, config.worktree, hooks and info are denied after it; the whole .git is not
# @pt.target bridge.sandbox.wrap, bridge.review.git_write_denies
# @pt.method unit
# @pt.legacy F-01: a check could rewrite .git/config inside the writable worktree
def test_seatbelt_denies_git_internals(monkeypatch, tmp_path):
    from bridge import review
    monkeypatch.setattr(sandbox, "backend", lambda: "seatbelt")
    root = (tmp_path / "worktree").resolve()
    (root / ".git" / "hooks").mkdir(parents=True)
    denies = review.git_write_denies(root)
    profile = sandbox.wrap(["true"], [str(root)], "auto", denies)[2]
    git_dir = root / ".git"
    allow = profile.index(f'(allow file-write* (subpath "{root}")')
    deny = profile.index("(deny file-write* (subpath")
    assert allow < deny
    for name in ("config", "config.worktree", "hooks", "info"):
        assert f'(subpath "{git_dir / name}")' in profile[deny:]
    assert f'(subpath "{git_dir}")' not in profile
    assert "(deny file-write* (subpath" not in sandbox.wrap(["true"], [str(root)], "auto")[2]


# @pt.id KIT-SEATBELT-002-path-validation
# @pt.item Writable paths cannot inject profile rules
# @pt.condition a quote, backslash or control character in a writable path
# @pt.expected ValueError before launching a command
# @pt.target bridge.sandbox.wrap
# @pt.method unit
# @pt.legacy no Seatbelt profile existed
@pytest.mark.parametrize("bad", ['quote"', "back" + chr(92), "line\n", "tab\t", "del" + chr(127)])
def test_seatbelt_rejects_path(monkeypatch, tmp_path, bad):
    monkeypatch.setattr(sandbox, "backend", lambda: "seatbelt")
    with pytest.raises(ValueError, match="writable path"):
        sandbox.wrap(["true"], [str(tmp_path / bad)], "auto")


# @pt.id KIT-SEATBELT-003-writes
# @pt.item Seatbelt permits only configured writes and preserves exit codes
# @pt.condition worktree and private temporary directory allowed, sibling outside denied
# @pt.expected allowed writes succeed, outside write fails and exit 7 is preserved
# @pt.target bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy macOS checks could write outside their worktree
@needs_seatbelt
def test_seatbelt_confines_writes(tmp_path):
    root, temporary = tmp_path / "worktree", tmp_path / "temporary"
    root.mkdir()
    temporary.mkdir()
    outside = tmp_path / "outside"
    script = (
        "from pathlib import Path; "
        f"Path({str(root / 'inside')!r}).write_text('ok'); "
        f"Path({str(temporary / 'inside')!r}).write_text('ok'); "
        f"Path({str(outside)!r}).write_text('denied')"
    )
    result = subprocess.run(
        sandbox.wrap([sys.executable, "-c", script], [str(root), str(temporary)], "auto"),
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and "Operation not permitted" in result.stderr
    assert (root / "inside").read_text() == "ok"
    assert (temporary / "inside").read_text() == "ok"
    assert not outside.exists()
    result = subprocess.run(sandbox.wrap(
        [sys.executable, "-c", "raise SystemExit(7)"], [str(root), str(temporary)], "auto"
    ))
    assert result.returncode == 7


# @pt.id KIT-SEATBELT-008-launch-services
# @pt.item Seatbelt denies launch-service executables while allowing ordinary commands
# @pt.condition auto and strict modes execute open, osascript, security and true
# @pt.expected open, osascript and security fail (no app launch, no Keychain read); true succeeds
# @pt.target bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy allow-default permitted launch-service escapes
@needs_seatbelt
@pytest.mark.parametrize("mode", ["auto", "strict"])
def test_seatbelt_denies_launch_services(tmp_path, mode):
    for argv in (["/usr/bin/open"], ["/usr/bin/osascript", "-e", "return 1"],
                 ["/usr/bin/security", "list-keychains"]):
        result = subprocess.run(
            sandbox.wrap(argv, [str(tmp_path)], mode),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
    result = subprocess.run(
        sandbox.wrap(["/usr/bin/true"], [str(tmp_path)], mode),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0


# @pt.id KIT-SEATBELT-004-network
# @pt.item Strict Seatbelt denies TCP connections
# @pt.condition a local TCP server is listening
# @pt.expected connection fails with PermissionError
# @pt.target bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy macOS strict verification was unavailable
@needs_seatbelt
def test_seatbelt_strict_denies_tcp(tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as server:
        script = f"import socket; socket.create_connection({server.getsockname()!r}, timeout=2)"
        result = subprocess.run(
            sandbox.wrap([sys.executable, "-c", script], [str(tmp_path)], "strict"),
            capture_output=True, text=True, timeout=10,
        )
    assert result.returncode != 0 and "PermissionError" in result.stderr


# @pt.id KIT-SEATBELT-005-process-start
# @pt.item macOS process identity uses libproc with ps-compatible formatting
# @pt.condition current process and non-existent pid
# @pt.expected current start time matches C locale UTC ps and missing process returns None
# @pt.target bridge.security.process_started
# @pt.method integration
# @pt.legacy ps could not execute inside Seatbelt
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS libproc only")
def test_darwin_process_started():
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())],
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        assert process_started(os.getpid()) is not None
        assert process_started(2147483647) is None
        pytest.skip("ps unavailable inside Seatbelt; libproc identity checked")
    assert process_started(os.getpid()) == result.stdout.strip()
    assert process_started(2147483647) is None


# @pt.id KIT-SEATBELT-007-probe
# @pt.item Seatbelt availability probes once and refuses nested sandbox execution
# @pt.condition darwin with sandbox-exec present and probe success or failure
# @pt.expected exact probe argv, cached result, unavailable strict refusal on failure
# @pt.target bridge.sandbox.backend, bridge.sandbox._seatbelt_available
# @pt.method unit
# @pt.legacy no macOS availability probe existed
@pytest.mark.parametrize("returncode", [0, 71])
def test_seatbelt_probe_cached(monkeypatch, returncode):
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.Path, "exists", lambda self: True)
    calls = []

    def probe(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(sandbox.subprocess, "run", probe)
    sandbox._seatbelt_available.cache_clear()
    try:
        expected = "seatbelt" if returncode == 0 else None
        assert sandbox.backend() == expected
        assert sandbox.backend() == expected
        assert calls == [[
            "/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"
        ]]
        if returncode:
            assert sandbox.wrap(["true"], [], "strict") is None
            assert sandbox.describe("auto") == "unavailable on this system: checks run unconfined (auto)"
    finally:
        sandbox._seatbelt_available.cache_clear()




KEYCHAIN_PROBE = """
import ctypes
sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
svc, acc = b"agent-bridge-probe-none", b"none"
n, data = ctypes.c_uint32(), ctypes.c_void_p()
print(sec.SecKeychainFindGenericPassword(None, len(svc), svc, len(acc), acc,
                                         ctypes.byref(n), ctypes.byref(data), None))
"""


# @pt.id KIT-SEATBELT-KEYCHAIN
# @pt.item Verification code cannot reach the Keychain, even without the security binary
# @pt.condition a lookup of a nonexistent generic password through Security.framework (ctypes), outside and inside the profile
# @pt.expected outside, the keychain answers not found (-25300); inside, the lookup never reaches it
# @pt.target bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy security review: the exec deny on /usr/bin/security alone was bypassed this way
@needs_seatbelt
@pytest.mark.parametrize("mode", ["auto", "strict"])
def test_seatbelt_denies_keychain(tmp_path, mode):
    argv = ["/usr/bin/python3", "-c", KEYCHAIN_PROBE]
    outside = subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout.strip()
    if outside != "-25300":
        pytest.skip(f"no usable login keychain here ({outside or 'no output'})")
    inside = subprocess.run(sandbox.wrap(argv, [str(tmp_path)], mode), capture_output=True,
                            text=True, timeout=20).stdout.strip()
    assert inside != "-25300", inside
