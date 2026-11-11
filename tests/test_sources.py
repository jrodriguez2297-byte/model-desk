import pytest

import sources


def model(i, index=50.0):
    return {"id": str(i), "name": f"Test Model {i} (High)", "slug": f"test-model-{i}", "release_date": "2026-01-01",
            "model_creator": {"id": "c", "name": "Test Lab"},
            "evaluations": {"artificial_analysis_intelligence_index": index, "artificial_analysis_coding_index": 40.26},
            "pricing": {"price_1m_input_tokens": 2.0, "price_1m_output_tokens": 10.0,
                        "price_1m_cache_hit_tokens": 0.2, "price_1m_cache_write_tokens": None},
            "performance": {"median_output_tokens_per_second": 65.89, "median_time_to_first_token_seconds": 3.361}}


COLUMNS = {"slug", "model", "short", "creator", "color", "released", "reasoning", "intelligence", "coding_index",
           "agentic_index", "price_in", "price_out", "price_blended", "price_cache_read", "price_cache_write",
           "speed_tps", "ttft_s"}


def test_api_rows_map_to_dashboard_columns():
    rows = sources.to_rows([model(1, 30.04), model(2, 60.0), model(3, None)])
    assert [r["slug"] for r in rows] == ["test-model-2", "test-model-1"]      # sorted, unscored model dropped
    r = rows[1]
    assert set(r) == COLUMNS
    assert (r["short"], r["intelligence"], r["price_blended"], r["speed_tps"], r["ttft_s"]) == ("Test Model 1", 30.0, 4.0, 66, 3.36)
    assert r["color"].startswith("#") and r["price_cache_read"] == 0.2


def test_too_few_rows_is_an_error_so_the_old_file_stays(monkeypatch):
    monkeypatch.setattr(sources, "api_key", lambda: "k")
    monkeypatch.setattr(sources, "fetch", lambda key: [model(i) for i in range(49)])
    with pytest.raises(RuntimeError):
        sources.aa_models()
    monkeypatch.setattr(sources, "fetch", lambda key: [model(i) for i in range(50)])
    assert len(sources.aa_models()) == 50


def test_key_from_env_file(monkeypatch):
    import store
    monkeypatch.delenv("AA_API_KEY", raising=False)
    store.HOME.mkdir(parents=True, exist_ok=True)
    (store.HOME / ".env").write_text("# comment\nAA_API_KEY='abc123'\n")
    assert sources.api_key() == "abc123"
    (store.HOME / ".env").unlink()
