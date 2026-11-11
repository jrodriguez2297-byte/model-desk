"""History, trends and a findings log for the 'Rising on GitHub' section.

Runs after github.build(). Reads the "repos" dataset and history/repo_history.json, then:
  - keeps history/repo_ledger.json: every repo ever found (first seen, last seen, peak stars/day...)
  - keeps findings/log.json: every finding ever written, never pruned
  - writes findings/YYYY-MM-DD.md (today's analysis) and findings/README.md (index)
  - writes data/gh_history.json, data/gh_trends.json, data/gh_findings.json for the dashboard
"""
import datetime as dt, json, statistics

import store

HIST = store.HISTORY / "repo_history.json"
TIMES = store.HISTORY / "snapshot_times.json"
LEDGER = store.HISTORY / "repo_ledger.json"
LEDGER_START = store.HISTORY / "repo_ledger_day_start.json"
FIND = store.FINDINGS
LOG = FIND / "log.json"
TODAY = dt.date.today().isoformat()
MIN_GAP_HOURS = 20  # snapshots closer than this are not a full day: their gain is left out of trends
BREAKOUT_MIN = 50   # stars/day before a jump counts as a breakout
# A breakout needs 3+ earlier daily gains to compare against, so a repo's first days never trigger one.
COOL_SHARE = 0.3    # below 30% of its peak pace counts as cooling


def load(p, default):
    return store.read_json(p, default)


def day_start_ledger(ledger, today=None):
    """Running twice in one day must give the same findings as running once, so every run starts
    from the ledger as it was before today's first run (kept in history/repo_ledger_day_start.json)."""
    today = today or TODAY
    saved = load(LEDGER_START, None)
    if saved and saved.get("day") == today:
        return saved["ledger"]
    store.write_json(LEDGER_START, {"day": today, "ledger": ledger})
    return ledger


def short_gap_days(times):
    """Days whose snapshot came less than 20 hours after the previous one."""
    out, ds = set(), sorted(times)
    for p, d in zip(ds, ds[1:]):
        try:
            hours = (dt.datetime.fromisoformat(times[d]) - dt.datetime.fromisoformat(times[p])).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if hours < MIN_GAP_HOURS:
            out.add(d)
    return out


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def daily_gains(snaps):
    """[(day, stars gained per day since the previous snapshot)] for each snapshot after the first."""
    ds = sorted(snaps)
    return [(d, (snaps[d] - snaps[p]) / max(days_between(p, d), 1)) for p, d in zip(ds, ds[1:])]


def build():
    rows = store.read_rows("repos")
    hist = load(HIST, {})
    partial = short_gap_days(load(TIMES, {}))
    ledger = day_start_ledger(load(LEDGER, {}))
    log = load(LOG, [])
    log = [f for f in log if f["day"] != TODAY]  # re-running today replaces today's findings
    run_days = sorted({d for v in hist.values() for d in v})
    prev_run = max([d for d in run_days if d < TODAY], default=None)
    seen_today = set()

    # ---- ledger: every repo ever found
    for r in rows:
        snaps = hist.get(r["slug"], {})
        e = ledger.get(r["slug"]) or {"first_seen": min(snaps) if snaps else TODAY, "first_stars": snaps[min(snaps)] if snaps else r["stars"],
                                      "peak_spd": 0, "peak_day": None}
        gains = daily_gains(snaps)
        today_gain = gains[-1][1] if gains and gains[-1][0] == TODAY else None
        for d, v in gains:
            if v > e["peak_spd"]:
                e["peak_spd"], e["peak_day"] = round(v, 1), d
        e.update(slug=r["slug"], cat=r["cat"], source=r["source"], status=r["status"], url=r["url"], desc=r["desc"],
                 created=r["created"], last_seen=TODAY, last_stars=r["stars"], days_tracked=len(snaps),
                 gained_since_found=r["stars"] - e["first_stars"], dropped_on=None)
        e["_today_gain"], e["_gains"] = today_gain, gains
        ledger[r["slug"]] = e
        seen_today.add(r["slug"])

    finds = []

    def add(kind, slug, cat, text, value=None):
        finds.append({"day": TODAY, "kind": kind, "slug": slug or "", "cat": cat or "", "text": text,
                      "value": None if value is None else round(value, 1)})

    # ---- findings
    for slug, e in ledger.items():
        if slug not in seen_today:
            if e.get("last_seen") == prev_run and not e.get("dropped_on"):
                e["dropped_on"] = TODAY
                add("Dropped off", slug, e["cat"], f"{slug} left the list (older than 60 days, or no longer matches your areas). Last seen with {e['last_stars']:,} stars.")
            continue
        g, gains = e.pop("_today_gain"), e.pop("_gains")
        if e["source"] == "new" and e["first_seen"] == TODAY and prev_run:
            add("New find", slug, e["cat"], f"{slug} ({e['cat']}): {e['desc'][:110]}", e["last_stars"])
        if g is None:
            continue
        past = [v for d, v in gains[:-1] if days_between(d, TODAY) <= 7]
        if len(past) >= 3 and g >= BREAKOUT_MIN and g >= 2 * statistics.mean(past):
            add("Breakout", slug, e["cat"], f"{slug} gained {g:,.0f} stars/day, over 2x its recent pace of {statistics.mean(past):,.0f}.", g)
        if e["peak_spd"] >= BREAKOUT_MIN and e["peak_day"] != TODAY and g < COOL_SHARE * e["peak_spd"] and not e.get("cooling"):
            e["cooling"] = TODAY
            add("Cooling", slug, e["cat"], f"{slug} slowed to {g:,.0f} stars/day from a peak of {e['peak_spd']:,.0f} on {e['peak_day']}.", g)
        elif g >= COOL_SHARE * e["peak_spd"]:
            e.pop("cooling", None)

    live = [ledger[s] for s in seen_today]
    for r in rows:
        if r["source"] == "watchlist" and r.get("new_release") and r.get("release_date") == TODAY:
            add("Release", r["slug"], r["cat"], f"{r['slug']} shipped {r['release']} (you have it as '{r['status']}').")
    gainers = sorted((e for e in live if e["source"] == "new" and gain_today(hist, e["slug"])),
                     key=lambda e: -(gain_today(hist, e["slug"]) or 0))[:3]
    for i, e in enumerate(gainers):
        v = gain_today(hist, e["slug"])
        if v:
            add("Top riser", e["slug"], e["cat"], f"#{i + 1} today: {e['slug']} +{v:,.0f} stars/day ({e['cat']}).", v)

    # ---- trends per day and area
    trends = {}
    for slug, snaps in hist.items():
        e = ledger.get(slug)
        if not e:
            continue
        for d, v in daily_gains(snaps):
            if d in partial:
                continue  # taken hours after the previous snapshot, not a day: skip so the trend isn't understated
            t = trends.setdefault((d, e["cat"]), {"day": d, "cat": e["cat"], "stars_gained": 0.0, "repos": 0, "new_found": 0})
            t["stars_gained"] += max(v, 0)
            t["repos"] += 1
    for e in ledger.values():
        t = trends.setdefault((e["first_seen"], e["cat"]), {"day": e["first_seen"], "cat": e["cat"], "stars_gained": 0.0, "repos": 0, "new_found": 0})
        if e["source"] == "new" and e["first_seen"] != min(run_days or [TODAY]):
            t["new_found"] += 1
    trend_rows = sorted(({**t, "stars_gained": round(t["stars_gained"], 1)} for t in trends.values()), key=lambda t: (t["day"], t["cat"]))

    today_by_cat = {t["cat"]: t["stars_gained"] for t in trend_rows if t["day"] == TODAY}
    if today_by_cat:
        top = max(today_by_cat, key=today_by_cat.get)
        tot = sum(today_by_cat.values()) or 1
        add("Area trend", "", top, f"{top} drew the most attention today: {today_by_cat[top]:,.0f} stars/day, {today_by_cat[top] / tot:.0%} of all tracked gains.", today_by_cat[top])
    week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    wk = {}
    for t in trend_rows:
        if t["day"] > week_ago:
            wk[t["cat"]] = wk.get(t["cat"], 0) + t["new_found"]
    if wk and max(wk.values()) >= 3:
        top = max(wk, key=wk.get)
        add("Area trend", "", top, f"{top} had the most new finds this week ({wk[top]}).", wk[top])

    log += finds
    log.sort(key=lambda f: (f["day"], f["kind"]))

    # ---- save
    store.write_json(LEDGER, ledger, indent=1)
    store.write_json(LOG, log, indent=1)
    first_day = min(run_days or [TODAY])
    write_markdown(finds, ledger, live, today_by_cat, first_day)
    write_index(log, ledger)

    history_rows = [{"day": d, "slug": s, "cat": ledger[s]["cat"], "source": ledger[s]["source"], "stars": n}
                    for s, snaps in hist.items() if s in ledger for d, n in sorted(snaps.items())]
    ledger_rows = [{k: v for k, v in e.items() if not k.startswith("_")} for e in ledger.values()]
    src = f"Daily star snapshots from the GitHub API (via the gh CLI), kept since {first_day}"
    store.write_dataset("gh_history", history_rows, src)
    store.write_dataset("gh_trends", trend_rows, src)
    store.write_dataset("gh_findings", log, src)
    store.write_dataset("gh_ledger", ledger_rows, src)
    print(f"ledger {len(ledger)} repos; {len(finds)} findings today; {len(log)} in log; {len(trend_rows)} trend rows")
    return finds


def gain_today(hist, slug):
    g = daily_gains(hist.get(slug, {}))
    return g[-1][1] if g and g[-1][0] == TODAY else None


def write_markdown(finds, ledger, live, today_by_cat, first_day):
    lines = [f"# Rising on GitHub: findings for {TODAY}", "",
             f"Tracking {len(live)} repos today, {len(ledger)} found since {first_day}.", ""]
    if today_by_cat:
        lines += ["## Stars gained per day, by area", "", "| Area | Stars/day |", "|---|---|"]
        lines += [f"| {c} | {v:,.0f} |" for c, v in sorted(today_by_cat.items(), key=lambda x: -x[1])] + [""]
    by_kind = {}
    for f in finds:
        by_kind.setdefault(f["kind"], []).append(f)
    for k in ["Top riser", "Breakout", "New find", "Release", "Area trend", "Cooling", "Dropped off"]:
        if k in by_kind:
            lines += [f"## {k}", ""] + [f"- {f['text']}" for f in by_kind[k]] + [""]
    if not finds:
        lines += ["No findings today (first snapshot, or nothing moved enough)."]
    store.write_text(FIND / f"{TODAY}.md", "\n".join(lines) + "\n")


def write_index(log, ledger):
    days = sorted({f["day"] for f in log}, reverse=True)
    lines = ["# Rising on GitHub: findings log", "",
             "One file per day with that day's analysis. `log.json` holds every finding; `../history/repo_ledger.json` every repo ever found.", "",
             "| Day | Findings | New finds | Breakouts |", "|---|---|---|---|"]
    for d in days:
        fs = [f for f in log if f["day"] == d]
        lines.append(f"| [{d}]({d}.md) | {len(fs)} | {sum(f['kind'] == 'New find' for f in fs)} | {sum(f['kind'] == 'Breakout' for f in fs)} |")
    top = sorted(ledger.values(), key=lambda e: -e.get("peak_spd", 0))[:10]
    lines += ["", "## Biggest peaks so far", "", "| Repo | Area | Peak stars/day | Day |", "|---|---|---|---|"]
    lines += [f"| [{e['slug']}]({e['url']}) | {e['cat']} | {e['peak_spd']:,.0f} | {e['peak_day'] or '–'} |" for e in top if e.get("peak_spd")]
    store.write_text(FIND / "README.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    build()
