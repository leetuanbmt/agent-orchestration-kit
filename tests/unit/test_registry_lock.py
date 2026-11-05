"""Concurrent updates to the machine-wide project registry."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

from bridge import accounts, osutil, registry, storage


# @pt.id T0042-concurrent-additions
# @pt.item Concurrent registry additions
# @pt.condition Thirty-two writers start together with distinct roots
# @pt.expected Every root remains registered
# @pt.target bridge.accounts.remember_project
# @pt.method unit
# @pt.legacy None
def test_concurrent_additions(tmp_path):
    roots = [tmp_path / str(index) for index in range(32)]
    barrier = Barrier(len(roots))

    def remember(root):
        barrier.wait(timeout=5)
        accounts.remember_project(root)

    with ThreadPoolExecutor(max_workers=len(roots)) as pool:
        list(pool.map(remember, roots))
    assert set(accounts.known_projects()) == {str(root) for root in roots}


# @pt.id T0042-blocking-addition
# @pt.item Registry lock contention
# @pt.condition Another handle holds the registry lock
# @pt.expected Remember waits for release and then adds the root
# @pt.target bridge.accounts.remember_project
# @pt.method unit
# @pt.legacy None
def test_remember_waits_for_lock(tmp_path, monkeypatch):
    attempted = Event()
    finished = Event()
    original = osutil.lock

    def lock(handle):
        attempted.set()
        original(handle)

    root = tmp_path / "new"
    with ThreadPoolExecutor(max_workers=1) as pool:
        with accounts._projects_lock():
            monkeypatch.setattr(osutil, "lock", lock)

            def remember():
                accounts.remember_project(root)
                finished.set()

            future = pool.submit(remember)
            assert attempted.wait(5)
            assert not finished.wait(0.1)
        future.result(timeout=5)
    assert finished.is_set()
    assert accounts.known_projects() == [str(root)]


# @pt.id T0042-prune-addition
# @pt.item Concurrent pruning and addition
# @pt.condition Remember starts after prune reads the registry
# @pt.expected Prune removes stale roots while preserving the new addition
# @pt.target bridge.storage.prune bridge.registry.remember_project
# @pt.method unit
# @pt.legacy None
def test_prune_with_remember(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    missing = tmp_path / "missing"
    root = tmp_path / "new"
    root.mkdir()
    accounts.remember_project(missing)
    read = Event()
    release = Event()
    attempted = Event()
    original_read = registry.known_projects
    original_lock = osutil.lock
    prune_thread = None

    def known_projects():
        roots = original_read()
        if current_thread() is prune_thread:
            read.set()
            assert release.wait(5)
        return roots

    def lock(handle):
        if current_thread() is not prune_thread:
            attempted.set()
        original_lock(handle)

    def prune():
        nonlocal prune_thread
        prune_thread = current_thread()
        return storage.prune(apply=True)

    monkeypatch.setattr(registry, "known_projects", known_projects)
    monkeypatch.setattr(osutil, "lock", lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pruning = pool.submit(prune)
        try:
            assert read.wait(5)
            remembering = pool.submit(accounts.remember_project, root)
            assert attempted.wait(5)
            assert not remembering.done()
        finally:
            release.set()
        assert pruning.result(timeout=5)["removed"] == [str(missing)]
        remembering.result(timeout=5)
    assert original_read() == [str(root)]
