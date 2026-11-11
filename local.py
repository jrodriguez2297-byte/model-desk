"""Which models can run on this machine, from the Ollama library.

The free Artificial Analysis API has no open-weights flag, so a model counts as open when
  - Ollama offers a downloadable (non-cloud) build of it, or
  - LMArena lists it with an open license.
Name matching order: your config's ollama_overrides, then mappings/ollama_names.json, then an
automatic guess from the model name. No match means the model is left out, never guessed.
"""
import datetime as dt, re, subprocess, urllib.request

import store

UA = {"User-Agent": "Mozilla/5.0 (model-desk)"}
HIT_DAYS, MISS_DAYS = 3, 14   # how long a cached Ollama lookup is trusted
FITS, TIGHT = 0.4, 0.7        # share of RAM: at or under 40% runs great, at or under 70% runs


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def parse_tags(name, h):
    out = {}
    for m in re.finditer(r'href="/library/' + re.escape(name) + r':([A-Za-z0-9._-]+)"', h):
        tag = m.group(1)
        if tag in out:
            continue
        sz = re.search(r'•\s*([0-9.]+)\s*(GB|MB|TB)\s*•', h[m.start(): m.start() + 1600])
        gb = float(sz.group(1)) * {"GB": 1, "MB": 0.001, "TB": 1000}[sz.group(2)] if sz else None
        out[tag] = {"gb": gb, "mlx": "mlx" in tag, "cloud": tag.endswith("cloud")}
    return out


def ollama_tags(name, cache, today=None):
    """Tags for one Ollama library name, cached so a daily run makes few requests."""
    today = today or dt.date.today()
    c = cache.get(name)
    if c:
        age = (today - dt.date.fromisoformat(c["checked"])).days
        if age < (HIT_DAYS if c["tags"] else MISS_DAYS):
            return c["tags"]
    try:
        tags = parse_tags(name, get(f"https://ollama.com/library/{name}/tags"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return c["tags"] if c else None   # Ollama is having trouble: keep what we knew
        tags = None
    except Exception:
        return c["tags"] if c else None
    cache[name] = {"tags": tags or None, "checked": today.isoformat()}
    return tags or None


def name_guesses(short):
    fam = re.sub(r"\s*\(.*?\)", "", short).strip()
    size = None
    m = re.search(r"\s(\d+(?:\.\d+)?[BbMm])(?:\s?A\d+(?:\.\d+)?[Bb])?$", fam)
    if m:
        size = m.group(1).lower()
        fam = fam[: m.start()].strip()
    low = fam.lower()
    names = [low.replace(" ", ""), low.replace(" ", "-"), re.sub(r"\s+(\d)", r"\1", low).replace(" ", "-")]
    return [n for n in dict.fromkeys(names) if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", n)], size


def fit_label(gb, ram):
    if gb is None:
        return "Cloud only"
    if ram is None:
        return "Not checked"
    if gb <= ram * FITS:
        return "Runs great"
    if gb <= ram * TIGHT:
        return "Runs"
    return "Too big"


def installed_models():
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20).stdout
        return {l.split()[0] for l in out.splitlines()[1:] if l.strip()}
    except Exception:
        return set()


def pick_tag(tags, size, ram):
    local = {t: v for t, v in tags.items() if not v["cloud"] and v["gb"]}
    pick = None
    if size:
        cands = [t for t in local if t == size or t.startswith(size + "-")]
        mlx = [t for t in cands if local[t]["mlx"]]
        pick = min(mlx, key=lambda t: local[t]["gb"]) if mlx else (cands or [None])[0]
    elif local:
        limit = (ram or 16) * TIGHT
        fits = [t for t in local if local[t]["gb"] <= limit]
        pick = max(fits, key=lambda t: local[t]["gb"]) if fits else min(local, key=lambda t: local[t]["gb"])
    return pick, local


def local_models(aa, arena_open, cfg=None, cache=None, installed=None):
    """aa: rows from sources.aa_models(). arena_open: {aa slug: True/False} from LMArena licenses.
    Returns (rows for the 'local' dataset, {slug: open flag})."""
    cfg = cfg or store.config()
    ram = store.ram_gb(cfg)
    overrides = {**store.mapping("ollama_names").get("models", {}), **cfg.get("ollama_overrides", {})}
    cache_path = store.CACHE / "ollama_tags.json"
    own_cache = cache is None
    if own_cache:
        cache = store.read_json(cache_path, {})
    installed = installed_models() if installed is None else installed
    rows, open_flag = [], {}
    for m in aa:
        ov = overrides.get(m["slug"])
        if ov == "skip":
            continue
        names, size = ([ov["name"]], ov.get("tag")) if ov else name_guesses(m["short"])
        tags = lib = None
        for n in names:
            t = ollama_tags(n, cache)
            if t:
                tags, lib = t, n
                break
        if not tags:
            continue
        pick, local = pick_tag(tags, size, ram)
        if size and not pick and local:
            continue   # Ollama has this family, but not this size
        is_open = bool(pick) or bool(arena_open.get(m["slug"]))
        open_flag[m["slug"]] = is_open
        if not pick and not (is_open or ov):
            continue   # hosted-only on Ollama and not known to be open: not a local model
        gb = local[pick]["gb"] if pick else None
        pull = f"{lib}:{pick}" if pick else f"{lib}:cloud"
        rows.append({"slug": m["slug"], "model": m["model"], "ollama": pull, "download_gb": gb,
                     "mac_fit": fit_label(gb, ram), "mlx": bool(pick and local[pick]["mlx"]),
                     "installed": pull in installed or any(i.startswith(lib + ":") and (pick or "") in i for i in installed),
                     "ollama_url": f"https://ollama.com/library/{lib}"})
    for e in cfg.get("extra_local", []):   # your own extra rows, from your config only
        rows.append({**e, "installed": e["ollama"] in installed or e["ollama"].split(":")[0] + ":latest" in installed})
    if own_cache:
        store.write_json(cache_path, cache)
    return rows, open_flag
