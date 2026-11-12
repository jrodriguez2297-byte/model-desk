import subprocess, types

import pytest

import github, ghtrends, refresh, store

# The real case behind this file: a snapshot late on Nov 10 (23:01), then the 6 AM run on Nov 11.
SNAPS = {"2026-11-08": 54567, "2026-11-10": 55883, "2026-11-11": 56014}
TIMES = {"2026-11-08": "2026-11-08T06:00:00-08:00", "2026-11-10": "2026-11-10T23:01:00-08:00",
         "2026-11-11": "2026-11-11T06:01:50-08:00"}


def test_span_uses_real_time_between_snapshots():
    assert round(github.span_days(TIMES, "2026-11-10", "2026-11-11"), 2) == 0.29   # 7 hours, not a day
    assert github.span_days({}, "2026-11-08", "2026-11-10") == 2                   # no times: calendar days
    assert github.span_days({"a": "bad"}, "2026-11-10", "2026-11-11") == 1
    close = {"2026-11-10": "2026-11-10T23:59:00-08:00", "2026-11-11": "2026-11-11T00:01:00-08:00"}
    assert github.span_days(close, "2026-11-10", "2026-11-11") == 0.25             # floor: no blown-up rates


def test_a_short_gap_is_not_read_as_a_slowdown():
    gains = dict(ghtrends.daily_gains(SNAPS, TIMES))
    assert 470 < gains["2026-11-10"] < 500 and 430 < gains["2026-11-11"] < 500    # the same pace on both days
    by_calendar = dict(ghtrends.daily_gains(SNAPS))
    assert by_calendar["2026-11-11"] == 131                                       # what the old maths said
    assert ghtrends.short_gap_days(TIMES) == {"2026-11-11"}                       # 7 hours: too short to judge


def fake_gh(monkeypatch, answers):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        code, out, err = answers[min(len(calls), len(answers)) - 1]
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)
    monkeypatch.setattr(github.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(github, "RETRY_WAIT", 0)
    return calls


def test_a_missing_repo_is_none_but_a_failed_call_is_an_error(monkeypatch):
    fake_gh(monkeypatch, [(1, "", "gh: Not Found (HTTP 404)")])
    assert github.gh("repos/gone/gone") is None
    calls = fake_gh(monkeypatch, [(1, "", "gh: API rate limit exceeded (HTTP 403)")])
    with pytest.raises(RuntimeError, match="rate limit"):
        github.gh("search/repositories?q=x")
    assert len(calls) == 3                                                         # tried three times first
    fake_gh(monkeypatch, [(1, "", "connection reset"), (0, '{"ok": true}', "")])
    assert github.gh("repos/a/b") == {"ok": True}                                  # one hiccup is retried


def test_status_tells_never_worked_from_stale(monkeypatch):
    monkeypatch.setattr(store, "log", lambda msg: None)
    run = refresh.Run()
    run.status = {}

    def boom():
        raise RuntimeError("no key")
    run.step("A", boom)
    assert run.status["A"]["ok"] is False and run.status["A"]["last_ok"] is None  # never worked: "not set up yet"
    run.step("A", lambda: {})
    worked = run.status["A"]["last_ok"]
    assert worked
    run.step("A", boom)
    assert run.status["A"]["last_ok"] == worked and run.status["A"]["stale_since"]  # worked before: "stale"
    run.status["B"] = {"source": "B", "ok": True, "checked": "2026-11-10T14:00:00Z"}   # status file from before last_ok
    run.step("B", boom)
    assert run.status["B"]["last_ok"] == "2026-11-10T14:00:00Z"
