import os

import pytest

import store


def test_failed_write_leaves_old_file(tmp_path, monkeypatch):
    p = tmp_path / "a.json"
    store.write_json(p, {"v": 1})

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.write_json(p, {"v": 2})
    assert store.read_json(p) == {"v": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]   # no temp file left behind


def test_live_lock_blocks_and_dead_lock_is_taken():
    store.HOME.mkdir(parents=True, exist_ok=True)
    store.LOCK.write_text(str(os.getppid()))      # a process that is alive
    with pytest.raises(store.AlreadyRunning):
        with store.lock():
            pass
    store.LOCK.write_text("999999")               # a process that is gone
    with store.lock():
        assert store.LOCK.read_text() == str(os.getpid())
    assert not store.LOCK.exists()


def test_lists_become_text():
    assert store.flat({"a": ["x", "y"], "b": 1}) == {"a": "x, y", "b": 1}


def test_backups_keep_newest_seven():
    store.ensure_home()
    store.write_json(store.HISTORY / "x.json", {})
    for d in range(1, 11):
        store.backup(f"2026-01-{d:02d}")
    kept = sorted(p.name for p in store.BACKUPS.iterdir())
    assert len(kept) == 7 and kept[0] == "2026-01-04" and kept[-1] == "2026-01-10"
