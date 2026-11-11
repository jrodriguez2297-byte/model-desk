"""One-time move from the old layout (files beside the code) to ~/.model-desk.

Old files are copied, never moved or deleted. Runs by itself on the first refresh when old
files sit beside the code, or by hand:
    python migrate.py --from /path/to/old/folder [--evening 2026-11-07]
--evening marks a day whose old snapshot was taken in the evening (18:00), so the gain to the
next morning is not counted as a full day.
"""
import argparse, datetime as dt, shutil, sys
from pathlib import Path

import store

MARK = store.HOME / "MIGRATED"


def convert_config(old):
    cfg = store.read_json(store.REPO / "defaults" / "config.json")
    if "mac_ram_gb" in old:
        cfg["ram_gb"] = old["mac_ram_gb"]
    cfg["ollama_overrides"] = old.get("ollama_overrides", {})
    cfg["extra_local"] = [{k: v for k, v in e.items() if k != "logo"} for e in old.get("extra_local", [])]
    cfg["uncensored_builds"] = bool(cfg["extra_local"])
    return cfg


def snapshot_times(hist, evening=()):
    """Old history has one date per snapshot and no time. Give each 06:00 local (18:00 for evening days)."""
    tz = dt.datetime.now().astimezone().tzinfo
    days = sorted({d for v in hist.values() for d in v})
    return {d: dt.datetime.combine(dt.date.fromisoformat(d), dt.time(18 if d in evening else 6), tz).isoformat() for d in days}


def usage_history(rows):
    out = {}
    for r in rows:
        out.setdefault(r["day"], {})[r["family"]] = {k: v for k, v in r.items() if k not in ("day", "family", "in_audit")}
    return dict(sorted(out.items()))


def migrate(src, evening=()):
    src = Path(src).expanduser()
    store.ensure_home()
    done = []
    defaults = store.REPO / "defaults"

    def untouched(name):
        p = store.HOME / name
        return not p.exists() or p.read_text() == (defaults / name).read_text()

    if (src / "config.json").exists() and untouched("config.json"):
        store.write_json(store.HOME / "config.json", convert_config(store.read_json(src / "config.json")), indent=2)
        done.append("config.json")
    if (src / "watchlist.json").exists() and untouched("watchlist.json"):
        shutil.copy(src / "watchlist.json", store.HOME / "watchlist.json")
        done.append("watchlist.json")
    for old, new in ((src / "cache" / "repo_history.json", store.HISTORY / "repo_history.json"),
                     (src / "cache" / "repo_ledger.json", store.HISTORY / "repo_ledger.json")):
        if old.exists() and not new.exists():
            shutil.copy(old, new)
            done.append(new.name)
    hist = store.read_json(store.HISTORY / "repo_history.json", {})
    if hist and not (store.HISTORY / "snapshot_times.json").exists():
        store.write_json(store.HISTORY / "snapshot_times.json", snapshot_times(hist, evening))
        done.append("snapshot_times.json")
    ud = store.read_json(src / "data" / "usage_daily.json")
    if ud and not (store.HISTORY / "usage_daily.json").exists():
        store.write_json(store.HISTORY / "usage_daily.json", usage_history(ud["rows"]))
        done.append("usage_daily.json")
    if (src / "findings").is_dir():
        for f in (src / "findings").iterdir():
            if f.is_file() and not (store.FINDINGS / f.name).exists():
                shutil.copy(f, store.FINDINGS / f.name)
        done.append("findings/")
    if (src / "data").is_dir():
        for f in (src / "data").glob("*.json"):
            if not (store.DATA / f.name).exists() and f.name != "logos.json":
                shutil.copy(f, store.DATA / f.name)
        done.append("data/")
    store.write_text(MARK, f"{dt.date.today().isoformat()} from {src}: {', '.join(done)}\n")
    return done


def run_if_needed():
    """First refresh only: old files beside the code and no MIGRATED marker yet."""
    if MARK.exists() or not (store.REPO / "cache" / "repo_history.json").exists():
        return None
    return migrate(store.REPO)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--evening", action="append", default=[])
    a = ap.parse_args()
    print("migrated:", ", ".join(migrate(a.src, set(a.evening))))
