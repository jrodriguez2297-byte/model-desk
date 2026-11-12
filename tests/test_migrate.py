import json

import ghtrends, migrate, store


def test_old_dates_get_times_and_short_gap_is_flagged():
    hist = {"a/b": {"2026-11-07": 10, "2026-11-08": 12, "2026-11-10": 30}}
    times = migrate.snapshot_times(hist, evening={"2026-11-07"})
    assert sorted(times) == ["2026-11-07", "2026-11-08", "2026-11-10"]
    assert "T18:00" in times["2026-11-07"] and "T06:00" in times["2026-11-08"]
    # evening to next morning is 12 hours: long enough to keep, and counted as half a day
    assert ghtrends.short_gap_days(times) == set()
    assert ghtrends.span_days(times, "2026-11-07", "2026-11-08") == 0.5


def test_usage_history_shape_and_counts():
    rows = [{"day": "2026-11-01", "family": "opus", "cost": 2.5, "replies": 3, "in_audit": False},
            {"day": "2026-11-01", "family": "sonnet", "cost": 1.0, "replies": 1, "in_audit": False}]
    h = migrate.usage_history(rows)
    assert h == {"2026-11-01": {"opus": {"cost": 2.5, "replies": 3}, "sonnet": {"cost": 1.0, "replies": 1}}}


def test_migrate_copies_and_never_deletes(tmp_path):
    old = tmp_path / "old"
    (old / "cache").mkdir(parents=True)
    (old / "data").mkdir()
    (old / "cache" / "repo_history.json").write_text(json.dumps({"a/b": {"2026-11-07": 1}}))
    (old / "config.json").write_text(json.dumps({"mac_ram_gb": 32, "ollama_overrides": {"x": "skip"}}))
    for f in ("repo_history.json", "snapshot_times.json"):
        (store.HISTORY / f).unlink(missing_ok=True)
    (store.HOME / "config.json").unlink(missing_ok=True)
    migrate.migrate(old)
    assert (old / "cache" / "repo_history.json").exists()
    assert store.read_json(store.HISTORY / "repo_history.json") == {"a/b": {"2026-11-07": 1}}
    cfg = store.read_json(store.HOME / "config.json")
    assert cfg["ram_gb"] == 32 and cfg["ollama_overrides"] == {"x": "skip"} and cfg["usage"]["show_titles"] is False


def test_second_run_in_a_day_starts_from_the_same_ledger():
    ghtrends.LEDGER_START.unlink(missing_ok=True)
    first = ghtrends.day_start_ledger({"a/b": {"peak_spd": 1}}, today="2026-11-11")
    again = ghtrends.day_start_ledger({"a/b": {"peak_spd": 9, "cooling": "2026-11-11"}}, today="2026-11-11")
    assert first == again == {"a/b": {"peak_spd": 1}}
    assert ghtrends.day_start_ledger({"x": {}}, today="2026-11-12") == {"x": {}}
