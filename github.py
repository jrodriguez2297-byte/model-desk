"""GitHub tracker for the Model Desk dashboard.

Builds the "repos" dataset (your watchlist plus new discoveries, with star momentum) and keeps
history/repo_history.json: one star snapshot per repo per day, kept forever.
history/snapshot_times.json records the exact time of each day's snapshot, so gains between
snapshots taken less than 20 hours apart are not counted as a full day.
Uses the gh CLI's login for API calls. Repo descriptions are third-party text: they are stored
as data and only ever shown as plain text.
"""
import base64, re, datetime as dt, json, shutil, subprocess, time, urllib.parse, urllib.request

import store

HIST = store.HISTORY / "repo_history.json"
TIMES = store.HISTORY / "snapshot_times.json"
TODAY = dt.date.today().isoformat()
SOURCE = "https://docs.github.com/en/rest (GitHub API via the gh CLI); your watchlist"


def gh(path):
    if not shutil.which("gh"):
        raise RuntimeError("the gh CLI is not installed (https://cli.github.com), so the GitHub section is skipped")
    out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def avatar(url):
    try:
        with urllib.request.urlopen(url + ("&" if "?" in url else "?") + "s=48", timeout=20) as r:
            data = r.read()
            mime = r.headers.get("Content-Type", "image/png").split(";")[0]
        return f"data:{mime};base64," + base64.b64encode(data).decode()
    except Exception:
        return None


def age_days(iso):
    if not iso:
        return None
    return max((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days, 0)


def base_row(r):
    return {
        "slug": r["full_name"],
        "name": r["name"],
        "owner": r["owner"]["login"],
        "url": r["html_url"],
        "desc": (r.get("description") or "")[:160],
        "stars": r["stargazers_count"],
        "forks": r["forks_count"],
        "license": (r.get("license") or {}).get("spdx_id") or "none",
        "archived": bool(r.get("archived")),
        "pushed": (r.get("pushed_at") or "")[:10],
        "created": (r.get("created_at") or "")[:10],
        "days_since_push": age_days(r.get("pushed_at")),
        "age_days": age_days(r.get("created_at")),
        "_avatar": r["owner"]["avatar_url"],
    }


def latest_release(slug):
    rel = gh(f"repos/{slug}/releases/latest")
    if not rel or "tag_name" not in rel:
        return None, None
    return rel["tag_name"], (rel.get("published_at") or "")[:10]


def momentum(hist, slug, stars, age):
    """Stars per day over the last 7 days of snapshots, else lifetime average."""
    snaps = hist.get(slug, {})
    past = sorted(d for d in snaps if d < TODAY)
    if past:
        cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
        older = [d for d in past if d >= cutoff] or past[-1:]
        d0 = older[0]
        days = max((dt.date.today() - dt.date.fromisoformat(d0)).days, 1)
        return round((stars - snaps[d0]) / days, 1), "measured"
    if age:
        return round(stars / max(age, 1), 1), "lifetime"
    return None, None


def health(row):
    flags = []
    if row["archived"]:
        flags.append("Archived")
    if row["days_since_push"] is not None and row["days_since_push"] > 120:
        flags.append("No commits in 4+ months")
    if row["license"] in ("none", "NOASSERTION"):
        flags.append("License unclear")
    if row["license"].startswith(("AGPL", "GPL")):
        flags.append(row["license"] + " (copyleft)")
    return flags


def areas(cfg=None):
    """Area keywords: mappings/areas.json, with same-named areas in your config replacing them."""
    m = store.mapping("areas")
    out = dict(m.get("areas", {}))
    out.update((cfg or store.config()).get("areas") or {})
    return out, [b.lower() for b in m.get("block", [])]


def relevant(row, cat, area_map, block):
    d = (row["desc"] + " " + row["name"]).lower()
    if not row["desc"]:
        return False
    cjk = sum(1 for c in row["desc"] if "\u3000" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af")
    if cjk > len(row["desc"]) * 0.2:
        return False  # README not in English
    area = area_map.get(cat) or {}
    if any(b in d for b in block + [x.lower() for x in area.get("block", [])]):
        return False
    return any(k in d for k in area.get("need", []))


def build():
    cfg = store.read_json(store.HOME / "watchlist.json") or store.read_json(store.REPO / "defaults" / "watchlist.json")
    hist = store.read_json(HIST, {})
    times = store.read_json(TIMES, {})
    area_map, block = areas()
    if gh("rate_limit") is None:
        raise RuntimeError("gh is not signed in (run: gh auth login), so the GitHub section is skipped")
    rows, seen = [], set()

    for w in cfg["repos"]:
        r = gh(f"repos/{w['slug']}")
        if not r:
            print("missing", w["slug"])
            continue
        row = base_row(r)
        row.update(source="watchlist", cat=w.get("cat", "Other"), status=w.get("status", "watch"), note=w.get("note", ""))
        row["release"], row["release_date"] = latest_release(row["slug"])
        rows.append(row)
        seen.add(row["slug"].lower())

    since = (dt.date.today() - dt.timedelta(days=cfg["discover_days"])).isoformat()
    for d in cfg["discover"]:
        q = f"{d['q']} created:>={since} stars:>={cfg['discover_min_stars']} archived:false"
        res = gh("search/repositories?per_page=12&sort=stars&order=desc&q=" + urllib.parse.quote(q))
        time.sleep(2.2)  # search API: 30 per minute
        for r in (res or {}).get("items", []):
            if r["full_name"].lower() in seen or r.get("fork"):
                continue
            row = base_row(r)
            if not relevant(row, d["cat"], area_map, block):
                continue
            row.update(source="new", cat=d["cat"], status="new", note="", release=None, release_date=None)
            rows.append(row)
            seen.add(row["slug"].lower())

    for row in rows:
        hist.setdefault(row["slug"], {})[TODAY] = row["stars"]
        row["stars_per_day"], row["momentum_basis"] = momentum(hist, row["slug"], row["stars"], row["age_days"])
        snaps = hist[row["slug"]]
        prev = sorted(d for d in snaps if d < TODAY)
        row["stars_change_1d"] = row["stars"] - snaps[prev[-1]] if prev else None
        row["flags"] = health(row)
        row["new_release"] = bool(row["release_date"]) and age_days(row["release_date"] + "T00:00:00Z") <= 7
        row["history"] = [[d, snaps[d]] for d in sorted(snaps)[-30:]]
        row["avatar"] = avatar(row.pop("_avatar"))
        # dashboard tables hold flat values: lists become text ("date=stars" pairs, so no ":" to misread)
        row["flags"] = ", ".join(row["flags"])
        row["history"] = ";".join(f"{d}={n}" for d, n in row["history"])

    # history is kept forever (ghtrends.py builds trends and the findings log from it)
    times[TODAY] = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    store.write_json(HIST, hist)
    store.write_json(TIMES, times)
    return rows


if __name__ == "__main__":
    rows = build()
    store.write_dataset("repos", rows, SOURCE)
    print(len(rows), "repos;", sum(r["source"] == "new" for r in rows), "new")
