"""Artificial Analysis (AA) models, from the free Data API.

Each user brings a free key from artificialanalysis.ai. The key is read from, in order:
  1. the AA_API_KEY environment variable
  2. AA_API_KEY=... in ~/.model-desk/.env
  3. the macOS Keychain item "aa-api-key"
AA's free tier is for your own internal use, with attribution. Do not redistribute the data.
"""
import colorsys, hashlib, json, os, platform, re, subprocess, urllib.request

import store

API = "https://artificialanalysis.ai/api/v2/language/models/free"
SOURCE = "https://artificialanalysis.ai (free Data API, used with attribution)"
MIN_ROWS = 50
MAX_PAGES = 10


class NoKey(RuntimeError):
    pass


def api_key():
    k = os.environ.get("AA_API_KEY")
    if k:
        return k.strip()
    env = store.HOME / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r"\s*AA_API_KEY\s*=\s*(.+)", line)
            if m:
                return m.group(1).strip().strip("'\"")
    if platform.system() == "Darwin":
        out = subprocess.run(["security", "find-generic-password", "-s", "aa-api-key", "-w"],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    raise NoKey("No Artificial Analysis API key found. Get a free one at https://artificialanalysis.ai/data-api "
                "and put AA_API_KEY=... in ~/.model-desk/.env")


def fetch(key):
    """All pages of the free endpoint. About 4 requests; the free limit is 100 a day."""
    out, page = [], 1
    while page <= MAX_PAGES:
        req = urllib.request.Request(f"{API}?page={page}", headers={"x-api-key": key, "User-Agent": "model-desk"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        out += d.get("data") or []
        if not (d.get("pagination") or {}).get("has_more"):
            break
        page += 1
    return out


def color_for(name):
    """A stable color per model creator (the free API has no brand colors)."""
    h = int(hashlib.md5((name or "?").encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    r, g, b = colorsys.hls_to_rgb(h, 0.45, 0.55)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def short_name(name):
    return re.sub(r"\s*\((?:[^()]*)\)\s*$", "", name).strip() or name


def to_rows(models):
    """Map API objects to the dashboard's columns. Models with no Intelligence Index are left out."""
    rows = []
    for o in models:
        ev, pr, pf = o.get("evaluations") or {}, o.get("pricing") or {}, o.get("performance") or {}
        ii = ev.get("artificial_analysis_intelligence_index")
        if ii is None or not o.get("slug"):
            continue
        pin, pout = pr.get("price_1m_input_tokens"), pr.get("price_1m_output_tokens")
        creator = (o.get("model_creator") or {}).get("name")
        tps = pf.get("median_output_tokens_per_second")
        rnd = lambda v, n=1: None if v is None else round(v, n)
        rows.append({
            "slug": o["slug"], "model": o["name"], "short": short_name(o["name"]),
            "creator": creator, "color": color_for(creator), "released": o.get("release_date"),
            "reasoning": "non-reasoning" not in o["name"].lower(),
            "intelligence": round(ii, 1),
            "coding_index": rnd(ev.get("artificial_analysis_coding_index")),
            "agentic_index": rnd(ev.get("artificial_analysis_agentic_index")),
            "price_in": pin, "price_out": pout,
            "price_blended": round((3 * pin + pout) / 4, 4) if pin is not None and pout is not None else None,
            "price_cache_read": pr.get("price_1m_cache_hit_tokens"),
            "price_cache_write": pr.get("price_1m_cache_write_tokens"),
            "speed_tps": None if not tps else round(tps),
            "ttft_s": rnd(pf.get("median_time_to_first_token_seconds"), 2),
        })
    rows.sort(key=lambda x: -x["intelligence"])
    return rows


def aa_models():
    rows = to_rows(fetch(api_key()))
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"Artificial Analysis: only {len(rows)} models returned")
    return rows


if __name__ == "__main__":
    r = aa_models()
    print(len(r), "models; top:", r[0]["model"], r[0]["intelligence"])
