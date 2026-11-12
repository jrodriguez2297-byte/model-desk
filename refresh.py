# /// script
# requires-python = ">=3.10"
# dependencies = ["pyarrow"]
# ///
"""Model Desk daily refresh. No LLM involved.

Run: uv run refresh.py            (the OS timer runs this once a day)
Writes ~/.model-desk/data/*.json and ~/.model-desk/dashboard.html.

Sources:
  - Artificial Analysis free Data API (your own key): intelligence, price, speed
  - LMArena leaderboard dataset on Hugging Face (CC-BY-4.0): human-vote Elo, licenses
  - Ollama library tag pages: which models run locally and their download size
  - OpenRouter models API: hosted prices, including Claude cache prices
  - GitHub API through the gh CLI: your watchlist and new repos
  - Your own Claude Code logs: the usage audit (never leaves this machine)

One source failing never stops the others. A failed source keeps its last good file and is
marked stale in data/status.json, which the dashboard shows on that section only.
"""
import datetime as dt, json, re, sys, traceback, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store

UA = {"User-Agent": "Mozilla/5.0 (model-desk)"}
MIN_ROWS = {"aa_models": 50, "arena": 100, "openrouter": 20, "local": 1, "crosswalk": 10,
            "repos": 1, "gh_history": 1, "gh_trends": 0, "usage": 0}


def get(url, binary=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


# ---------- LMArena ----------
ARENA_CATS = {"overall": "Overall", "coding": "Coding", "creative_writing": "Creative writing",
              "instruction_following": "Following instructions",
              "industry_business_and_management_and_financial_operations": "Business and finance",
              "hard_prompts": "Hard prompts"}


def arena():
    import pyarrow.parquet as pq
    p = store.CACHE / "arena_text.parquet"
    p.write_bytes(get("https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/text/latest-00000-of-00001.parquet", True))
    rows = []
    for r in pq.read_table(p).to_pylist():
        if r["category"] not in ARENA_CATS:
            continue
        rows.append({"model": r["model_name"], "org": r["organization"], "license": r["license"],
                     "category": ARENA_CATS[r["category"]], "rank": int(r["rank"]),
                     "elo": round(r["rating"]), "elo_low": round(r["rating_lower"]), "elo_high": round(r["rating_upper"]),
                     "votes": int(r["vote_count"]), "published": r["leaderboard_publish_date"],
                     "open": r["license"] not in ("Proprietary",)})
    return rows


# ---------- OpenRouter ----------
def openrouter():
    d = json.loads(get("https://openrouter.ai/api/v1/models"))["data"]
    rows = []
    for m in d:
        if m["id"].endswith(":batch") or m["id"].startswith("~"):
            continue
        p = m.get("pricing") or {}

        def per_m(k):
            try:
                return round(float(p[k]) * 1e6, 4)
            except (KeyError, TypeError, ValueError):
                return None
        pin, pout = per_m("prompt"), per_m("completion")
        if pin is None or pout is None or pin < 0 or pout < 0:
            continue
        rows.append({"id": m["id"], "name": m.get("name"), "hf": m.get("hugging_face_id") or None,
                     "price_in": round(pin, 3), "price_out": round(pout, 3),
                     "price_blended": round((3 * pin + pout) / 4, 3),
                     "price_cache_read": per_m("input_cache_read"), "price_cache_write": per_m("input_cache_write"),
                     "price_cache_write_1h": per_m("input_cache_write_1h"),
                     "context_k": round((m.get("context_length") or 0) / 1000)})
    return rows


def norm(x):
    return re.sub(r"[^a-z0-9.]", "", x.lower())


# ---------- Name crosswalk: Artificial Analysis model -> LMArena model ----------
def crosswalk(aa, arena_rows):
    """Order: mappings/arena_names.json first, then an automatic name match, else no match."""
    names = {}
    for r in arena_rows:
        if r["category"] == "Overall":
            names.setdefault(r["model"], r)

    def base(x):
        x = re.sub(r"\s*\((?:max|xhigh|high|medium|low|non-reasoning|reasoning|thinking)[^)]*\)", "", x, flags=re.I)
        return norm(x)
    fixed = {slug: name for name, slug in store.mapping("arena_names").get("models", {}).items() if name in names}
    by = {}
    for n in names:
        by.setdefault(base(n), []).append(n)
    rows = []
    for m in aa:
        if m["slug"] in fixed:
            rows.append({"slug": m["slug"], "arena_model": fixed[m["slug"]]})
            continue
        cands = by.get(base(m["model"])) or by.get(base(m["short"]))
        if cands:
            rows.append({"slug": m["slug"], "arena_model": max(cands, key=lambda n: names[n]["elo"])})
    return rows


# ---------- Uncensored builds (off unless "uncensored_builds": true in your config) ----------
def uncensored(local_rows):
    rows = []
    for r in local_rows:
        if r.get("uncensored_of"):
            continue
        q = re.sub(r"\s*\(.*?\)", "", r["model"]).strip()
        try:
            hits = json.loads(get("https://huggingface.co/api/models?author=huihui-ai&sort=downloads&limit=3&search="
                                  + urllib.request.quote(q.replace(" ", "-"))))
        except Exception:
            hits = []
        key = norm(q)
        hits = [h for h in hits if "abliterated" in h["id"].lower() and key in norm(h["id"].replace("huihui-ai/", "").replace("huihui-", ""))]
        if hits:
            rows.append({"slug": r["slug"], "hf_repo": hits[0]["id"], "downloads": hits[0].get("downloads"),
                         "url": "https://huggingface.co/" + hits[0]["id"]})
    return rows


# ---------- orchestration ----------
class Run:
    def __init__(self):
        self.status = {r["source"]: r for r in store.read_rows("status")}
        self.now = store.now_iso()
        self.failed = 0
        self.ran = 0

    def step(self, name, fn, datasets=None):
        """Run one source. On failure, or too few rows, the old files stay and the source goes stale."""
        self.ran += 1
        try:
            out = fn()
            for ds, (rows, source) in (out or {}).items():
                need = MIN_ROWS.get(ds, 0)
                if len(rows) < need:
                    raise RuntimeError(f"{ds}: {len(rows)} rows, need at least {need}")
            for ds, (rows, source) in (out or {}).items():
                store.write_dataset(ds, rows, source, self.now)
                store.log(f"{ds}: {len(rows)} rows")
            self.status[name] = {"source": name, "ok": True, "stale_since": None, "error": "", "checked": self.now,
                                 "last_ok": self.now,
                                 "datasets": ", ".join(datasets or (out or {}).keys())}
            return out
        except Exception as e:
            self.failed += 1
            prev = self.status.get(name) or {}
            msg = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            store.log(f"! {name} failed: {msg}")
            if not isinstance(e, (RuntimeError, OSError)):
                traceback.print_exc()
            self.status[name] = {"source": name, "ok": False, "stale_since": prev.get("stale_since") or self.now,
                                 "error": msg, "checked": self.now,
                                 # None means the source has never worked here: the page says "not set up yet"
                                 "last_ok": prev.get("last_ok") or (prev.get("checked") if prev.get("ok") else None),
                                 "datasets": ", ".join(datasets or prev.get("datasets", "").split(", "))}
            return None

    def save(self):
        store.write_dataset("status", list(self.status.values()), "Model Desk refresh", self.now)


def main():
    store.ensure_home()
    import migrate
    migrate.run_if_needed()
    store.backup()
    cfg = store.config()
    run = Run()
    import sources, local as local_mod

    def aa_step():
        rows = sources.aa_models()
        was_open = {r["slug"]: r.get("open_weights") for r in store.read_rows("aa_models")}
        for r in rows:   # keep yesterday's open flag until the Ollama step sets today's
            r["open_weights"] = bool(was_open.get(r["slug"]))
        return {"aa_models": (rows, sources.SOURCE)}
    run.step("Artificial Analysis", aa_step, ["aa_models"])
    run.step("LMArena", lambda: {"arena": (arena(), "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset (CC-BY-4.0)")}, ["arena"])
    run.step("OpenRouter", lambda: {"openrouter": (openrouter(), "https://openrouter.ai/api/v1/models")}, ["openrouter"])

    def models_step():
        aa, ar = store.read_rows("aa_models"), store.read_rows("arena")
        if not aa:
            raise RuntimeError("waiting for Artificial Analysis data (add your API key first)")
        cw = crosswalk(aa, ar)
        lic = {r["model"]: r["open"] for r in ar if r["category"] == "Overall"}
        arena_open = {c["slug"]: lic.get(c["arena_model"], False) for c in cw}
        loc, open_flag = local_mod.local_models(aa, arena_open, cfg)
        for m in aa:   # open weights: LMArena license, or a downloadable Ollama build
            m["open_weights"] = bool(arena_open.get(m["slug"]) or open_flag.get(m["slug"]))
        out = {"aa_models": (aa, sources.SOURCE),
               "crosswalk": (cw, "Name matching between Artificial Analysis and LMArena, made by refresh.py"),
               "local": (loc, "https://ollama.com/library")}
        out["uncensored"] = (uncensored(loc) if cfg.get("uncensored_builds") else [], "https://huggingface.co/huihui-ai")
        return out
    run.step("Ollama", models_step, ["local", "crosswalk", "uncensored"])

    def github_step():
        import github, ghtrends
        rows = github.build()
        store.write_dataset("repos", rows, github.SOURCE, run.now)
        ghtrends.build()
        return {"repos": (rows, github.SOURCE)}
    run.step("GitHub", github_step, ["repos", "gh_history", "gh_trends", "gh_findings", "gh_ledger"])

    if cfg.get("usage", {}).get("enabled", True):
        def usage_step():
            import usage
            usage.write()
            return {}
        run.step("Claude Code logs", usage_step, ["usage", "usage_daily"])

    run.save()
    try:
        import build_page
        out = build_page.build()
        store.log(f"dashboard: {out}")
    except Exception as e:
        run.failed += 1
        store.log(f"! dashboard build failed: {e}")
        traceback.print_exc()
    store.log(f"{run.now} refresh done: {run.ran - min(run.failed, run.ran)} of {run.ran} sources ok")
    todo = [r for r in run.status.values() if not r["ok"] and not r.get("last_ok")]
    if todo:
        store.log("Not set up yet (the page works without them): " + "; ".join(f"{r['source']}: {r['error']}" for r in todo))
    return 1 if run.failed >= run.ran else 0


if __name__ == "__main__":
    try:
        with store.lock():
            sys.exit(main())
    except store.AlreadyRunning as e:
        store.log(str(e))
        sys.exit(0)
