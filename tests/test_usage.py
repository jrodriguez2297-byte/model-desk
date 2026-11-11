import datetime as dt, json

import usage

TODAY = dt.date(2026, 11, 10)
ROUTER = [{"id": "anthropic/claude-opus-5.5", "price_in": 4.0, "price_out": 20.0, "price_cache_read": 0.2,
           "price_cache_write": 5.0, "price_cache_write_1h": 8.0}]
MAPPING = {"models": {"claude-opus-5-5": {"openrouter": "anthropic/claude-opus-5.5", "price": [9, 9, 9, 9, 9]},
                      "claude-old-1": {"openrouter": "anthropic/gone", "price": [1.0, 1.25, 2.0, 0.1, 5.0]}},
           "compare": {"opus": "claude-opus-5-5"}}
SECRET = "my secret prompt about project falcon"


def write_session(root, folder, sid, cwd, model, title="Falcon launch plan"):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "custom-title", "customTitle": title},
        {"type": "user", "cwd": cwd, "message": {"content": SECRET}},
        {"type": "assistant", "cwd": cwd, "timestamp": "2026-11-09T18:00:00Z", "requestId": "r1",
         "message": {"id": "m1", "model": model, "content": [{"type": "tool_use", "name": "Read"}],
                     "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                               "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}}},
    ]
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(l, separators=(",", ":")) for l in lines) + "\n")


def build(tmp_path, **usage_cfg):
    cfg = {"usage": {"show_titles": False, "show_projects": False, "exclude_paths": [], **usage_cfg}}
    return usage.build(logs=tmp_path, cfg=cfg, prices=usage.Prices(ROUTER, MAPPING), today=TODAY)


def test_only_allowed_columns_and_no_titles_by_default(tmp_path):
    write_session(tmp_path, "-Users-me-work", "aaaaaaaa-1", "/Users/me/work", "claude-opus-5-5")
    rows, daily = build(tmp_path)
    assert len(rows) == 1
    assert set(rows[0]) <= usage.ALLOWED
    assert "title" not in rows[0] and "project" not in rows[0]
    blob = json.dumps([rows, daily])
    assert "Falcon" not in blob and "falcon" not in blob and "/Users/me" not in blob   # no titles, prompts or paths


def test_titles_and_projects_only_when_turned_on(tmp_path):
    write_session(tmp_path, "-Users-me-work", "aaaaaaaa-1", "/Users/me/work", "claude-opus-5-5")
    rows, _ = build(tmp_path, show_titles=True, show_projects=True)
    assert rows[0]["title"] == "Falcon launch plan" and rows[0]["project"] == "work"
    assert set(rows[0]) <= usage.ALLOWED | set(usage.OPTIONAL)
    assert SECRET not in json.dumps(rows)


def test_excluded_folder_is_skipped(tmp_path):
    write_session(tmp_path, "-Users-me-work", "aaaaaaaa-1", "/Users/me/work", "claude-opus-5-5")
    write_session(tmp_path, "-Users-me-Private-Stuff", "bbbbbbbb-2", "/Users/me/Private Stuff", "claude-opus-5-5")
    rows, daily = build(tmp_path, exclude_paths=["Private Stuff"])
    assert [r["session"] for r in rows] == ["aaaaaaaa"]
    assert sum(d["replies"] for d in daily) == 1


def test_known_model_uses_openrouter_price(tmp_path):
    write_session(tmp_path, "-Users-me-work", "aaaaaaaa-1", "/Users/me/work", "claude-opus-5-5-20261001")
    rows, _ = build(tmp_path)
    assert rows[0]["cost"] == 4.0 + 5.0 + 8.0 + 0.2 + 20.0 and rows[0]["unpriced_tokens"] == 0


def test_fallback_price_then_unpriced_never_opus(tmp_path):
    p = usage.Prices(ROUTER, MAPPING)
    assert p.rate("claude-old-1") == (1.0, 1.25, 2.0, 0.1, 5.0)     # not on OpenRouter: the mapping's own price
    assert p.rate("claude-future-9-9") is None and p.rate("gpt-x") is None
    write_session(tmp_path, "-Users-me-work", "aaaaaaaa-1", "/Users/me/work", "claude-future-9-9")
    rows, daily = build(tmp_path)
    assert rows[0]["cost"] == 0 and rows[0]["unpriced_tokens"] == 5_000_000
    assert daily[0]["unpriced_tokens"] == 5_000_000


def test_daily_history_replaces_fresh_days_and_keeps_old_ones():
    hist = {"2026-09-01": {"opus": {"cost": 1}}, "2026-11-09": {"opus": {"cost": 5}}}
    fresh = [{"day": "2026-11-09", "family": "opus", "cost": 7}]
    out = usage.merge_daily(hist, fresh, since="2026-10-07")
    assert out == {"2026-09-01": {"opus": {"cost": 1}}, "2026-11-09": {"opus": {"cost": 7}}}
    rows = usage.daily_rows(out, TODAY)
    assert rows == [{"day": "2026-11-09", "family": "opus", "cost": 7, "window": "recent"}]
