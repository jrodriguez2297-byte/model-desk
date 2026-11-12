"""Daily audit of how you use Claude models in Claude Code.

Reads local session logs (~/.claude/projects/**/*.jsonl, subagents included),
prices every API reply at list rates, classifies each session's task, and
flags sessions that a cheaper model or lower effort could likely have done.

Writes data/usage.json and data/usage_daily.json with aggregates only: no prompt text,
no file contents. Session titles and project names are left out unless you turn them on
in ~/.model-desk/config.json. Nothing here leaves your machine.
Costs are "API-equivalent": what the same tokens would cost on the API.
On a Claude plan they show share of usage, not a bill.
"""
import datetime as dt, json, os, pathlib, re, collections

import store

LOGS = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude").expanduser() / "projects"
CFG = store.config()
TZ = store.time_zone(CFG)
DAYS = 35      # 7 recent days plus the 28 before them. Claude Code keeps logs about 30 days by default.
RECENT_DAYS = 7
FAMILIES = ("fable", "opus", "sonnet", "haiku")
# Columns that may appear in usage.json. "title" and "project" only when turned on in config.
ALLOWED = {"session", "day", "task", "model", "family", "effort", "replies", "subagent_replies", "cost", "sub_cost",
           "sub_models", "cost_as_sonnet", "cost_as_haiku", "cost_as_opus", "output_tokens", "cache_hit",
           "peak_context_k", "start_context_k", "write_cost", "read_cost", "out_cost", "tool_calls", "reads", "edits",
           "read_share", "window", "unpriced_tokens", "finding", "advice", "saving", "flags"}
OPTIONAL = {"title": "show_titles", "project": "show_projects"}


def family(model):
    for f in FAMILIES:
        if f in (model or ""):
            return f
    return "other"


class Prices:
    """$ per 1M tokens as (input, cache write 5 min, cache write 1 hour, cache read, output).

    Order: the model's OpenRouter list price (through mappings/claude_models.json, longest
    prefix wins, then an automatic id match), then the mapping's own fallback price, then None.
    None means "unpriced": the tokens are counted, the cost is not guessed.
    """

    def __init__(self, openrouter_rows=None, mapping=None):
        m = mapping if mapping is not None else store.mapping("claude_models")
        self.models, self.compare_ids = m.get("models", {}), m.get("compare", {})
        self.router = {r["id"]: r for r in (openrouter_rows if openrouter_rows is not None else store.read_rows("openrouter"))}
        self.cache = {}

    def _from_router(self, rid):
        r = self.router.get(rid)
        if not r or r.get("price_in") is None or r.get("price_out") is None or r.get("price_cache_read") is None:
            return None
        pin = r["price_in"]
        w5 = r.get("price_cache_write") if r.get("price_cache_write") is not None else pin * 1.25
        w1 = r.get("price_cache_write_1h") if r.get("price_cache_write_1h") is not None else pin * 2
        return (pin, w5, w1, r["price_cache_read"], r["price_out"])

    def rate(self, model):
        if model in self.cache:
            return self.cache[model]
        model = model or ""
        key = max((k for k in self.models if model.startswith(k)), key=len, default=None)
        entry = self.models.get(key) or {}
        guess = "anthropic/" + re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", re.sub(r"-\d{8}$", "", model)) if model.startswith("claude-") else None
        out = (self._from_router(entry.get("openrouter")) or (tuple(entry["price"]) if entry.get("price") else None)
               or (self._from_router(guess) if guess else None))
        self.cache[model] = out
        return out

    def compare(self, fam):
        return self.rate(self.compare_ids.get(fam, ""))


def tokens(u):
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    w5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
    if not cc:
        w5m = u.get("cache_creation_input_tokens", 0) or 0
    return (u.get("input_tokens", 0) or 0, w5m, w1h, u.get("cache_read_input_tokens", 0) or 0, u.get("output_tokens", 0) or 0)


def cost(u, r):
    """Cost of one reply at rate r, or 0 when the model is unpriced."""
    if not r:
        return 0.0
    return sum(t * p for t, p in zip(tokens(u), r)) / 1e6


def excluded(path_text, cfg=None):
    """True when a session's folder matches one of the user's exclude_paths (plain substrings)."""
    pats = ((cfg or CFG).get("usage") or {}).get("exclude_paths") or []
    flat = re.sub(r"[^A-Za-z0-9]", "-", path_text or "")
    return any(p and (p in (path_text or "") or re.sub(r"[^A-Za-z0-9]", "-", p) in flat) for p in pats)


READ_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "ToolSearch", "get_page_text", "read_page", "search_screens", "search_sections"}
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

TASKS = [  # first match wins, checked against the session's first prompt (kept local, never written out)
    ("Review", r"\b(review|adversarial|poke holes|red team|critique|audit (this|my) (pr|code))\b"),
    ("Fix a bug", r"\b(fix|bug|broken|error|failing|fails|crash|not working|doesn'?t work|regression)\b"),
    ("Plan", r"\b(plan|roadmap|strategy|spec|architecture|approach)\b"),
    ("Design", r"\b(design|redesign|layout|ui|ux|mobbin|look(s)? (better|good)|artifact|dashboard|page)\b"),
    ("Research", r"\b(research|look into|find|compare|investigate|analy[sz]e|what('?s| is) the best|validated|sources?)\b"),
    ("Writing", r"\b(write|draft|email|caption|post|copy|reply|message)\b"),
    ("Setup and ops", r"\b(install|set ?up|config|update|upgrade|deploy|merge|branch|commit|pr\b|git|cron|schedule)\b"),
    ("Build", r"\b(build|add|create|implement|make|feature)\b"),
]


def classify(prompt, entry):
    if entry and "scheduled" in entry:
        return "Routine"
    p = (prompt or "").lower()
    if re.search(r"\((weekdays|daily|weekly|every|mondays?|\d{1,2}(:\d\d)? ?(am|pm))", p) or "heartbeat" in p:
        return "Routine"
    for name, rx in TASKS:
        if re.search(rx, p):
            return name
    return "Question" if len(p) < 160 else "Other"


def local_day(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).date()


def parse_file(path, since):
    """Yield one record per API reply: (day, model, effort, usage, tools, sidechain, ts)."""
    replies = {}
    first_prompt, title, entry, cwd = None, None, None, None
    with open(path, errors="ignore") as f:
        for line in f:
            if '"type":"assistant"' not in line and '"custom-title"' not in line and (first_prompt is not None or '"type":"user"' not in line):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            if t == "custom-title":
                title = d.get("customTitle") or d.get("title") or title
                continue
            if t == "user" and first_prompt is None:
                c = d.get("message", {}).get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                    first_prompt = c[:2000]
                    entry = (d.get("promptSource") or "") + " " + (d.get("entrypoint") or "")
                    cwd = d.get("cwd")
                continue
            if t != "assistant":
                continue
            ts = d.get("timestamp")
            if not ts or local_day(ts) < since:
                continue
            m = d.get("message", {})
            u = m.get("usage")
            if not u or not m.get("model") or m["model"] == "<synthetic>":
                continue
            key = d.get("requestId") or m.get("id")
            rec = replies.get(key)
            tools = [c.get("name") for c in m.get("content", []) if isinstance(c, dict) and c.get("type") == "tool_use"]
            if rec is None:
                replies[key] = rec = {"ts": ts, "model": m["model"], "effort": d.get("effort") or "default", "usage": u,
                                      "tools": [], "side": bool(d.get("isSidechain")), "cwd": d.get("cwd")}
            elif (u.get("output_tokens") or 0) >= (rec["usage"].get("output_tokens") or 0):
                rec["usage"] = u
            rec["tools"] += tools
            cwd = cwd or d.get("cwd")
    return list(replies.values()), first_prompt, title, entry, cwd


def project_of(cwd):
    if not cwd:
        return "unknown"
    p = pathlib.Path(cwd)
    parts = p.parts
    if ".claude" in parts and "worktrees" in parts:
        i = parts.index(".claude")
        return parts[i - 1] + " (worktree)"
    if "scratch-workspaces" in cwd:
        return "No folder"
    return p.name


def build(logs=None, cfg=None, prices=None, today=None):
    logs, cfg = logs or LOGS, cfg or CFG
    prices = prices or Prices()
    ucfg = cfg.get("usage") or {}
    today = today or dt.datetime.now(TZ).date()
    recent_from = today - dt.timedelta(days=RECENT_DAYS - 1)
    since = today - dt.timedelta(days=DAYS - 1)
    cutoff = dt.datetime.combine(since, dt.time(), TZ).timestamp() - 86400
    sessions = collections.defaultdict(lambda: {"replies": [], "prompt": None, "title": None, "entry": None, "cwd": None})
    for f in logs.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        if excluded(str(f), cfg):
            continue
        is_sub = f.parent.name == "subagents"
        sid = f.parent.parent.name if is_sub else f.stem
        reps, prompt, title, entry, cwd = parse_file(f, since)
        s = sessions[sid]
        for r in reps:
            r["sub"] = is_sub or r["side"]
        s["replies"] += reps
        if not is_sub:
            s["prompt"] = s["prompt"] or prompt
            s["title"] = s["title"] or title
            s["entry"] = s["entry"] or entry
            s["cwd"] = s["cwd"] or cwd
        else:
            s["cwd"] = s["cwd"] or cwd

    daily = collections.defaultdict(lambda: collections.Counter())
    srows = []
    for sid, s in sessions.items():
        reps = s["replies"]
        if not reps or excluded(s["cwd"], cfg):
            continue
        reps.sort(key=lambda r: r["ts"])
        main = [r for r in reps if not r["sub"]] or reps
        mc = collections.Counter(r["model"] for r in main)
        model = mc.most_common(1)[0][0]
        effort = collections.Counter(r["effort"] for r in main).most_common(1)[0][0]
        tot = collections.Counter()
        tools = collections.Counter()
        sub_cost, sub_models = 0.0, collections.Counter()
        peak_ctx = 0
        first_u = main[0]["usage"]
        start_ctx = first_u.get("input_tokens", 0) + (first_u.get("cache_read_input_tokens", 0) or 0) + (first_u.get("cache_creation_input_tokens", 0) or 0)
        for r in reps:
            u = r["usage"]
            rr = prices.rate(r["model"])
            c = cost(u, rr)
            if not rr:
                tot["unpriced"] += sum(tokens(u)); daily[(day_of(r), family(r["model"]))]["unpriced"] += sum(tokens(u))
            day = local_day(r["ts"]).isoformat()
            fam = family(r["model"])
            daily[(day, fam)]["cost"] += c
            daily[(day, fam)]["replies"] += 1
            daily[(day, fam)]["output"] += u.get("output_tokens", 0)
            daily[(day, fam)]["sub_cost"] += c if r["sub"] else 0
            tot["cost"] += c
            tk, r0 = tokens(u), rr or (0, 0, 0, 0, 0)
            wc = (tk[1] * r0[1] + tk[2] * r0[2]) / 1e6
            rc = tk[3] * r0[3] / 1e6
            oc = tk[4] * r0[4] / 1e6
            tot["write_cost"] += wc; tot["read_cost"] += rc; tot["out_cost"] += oc
            daily[(day, fam)]["write_cost"] += wc; daily[(day, fam)]["read_cost"] += rc; daily[(day, fam)]["out_cost"] += oc
            tot["in"] += u.get("input_tokens", 0)
            tot["write"] += u.get("cache_creation_input_tokens", 0) or 0
            tot["read"] += u.get("cache_read_input_tokens", 0) or 0
            tot["out"] += u.get("output_tokens", 0)
            for c2 in FAMILIES:   # the same tokens at another model's rates (rough: same cache behaviour assumed)
                tot["as_" + c2] += cost(u, prices.compare(c2))
            if r["sub"]:
                sub_cost += c
                sub_models[family(r["model"]) + " " + r["effort"]] += 1
            if not r["sub"]:
                peak_ctx = max(peak_ctx, u.get("input_tokens", 0) + (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0))
            for t in r["tools"]:
                tools[t] += 1
        n_tools = sum(tools.values())
        reads = sum(v for k, v in tools.items() if k in READ_TOOLS or k.endswith(("read_page", "get_page_text", "search_screens")))
        edits = sum(v for k, v in tools.items() if k in EDIT_TOOLS)
        task = classify((s["title"] or "") + " " + (s["prompt"] or ""), s["entry"])
        cache_in = tot["in"] + tot["write"] + tot["read"]
        start = local_day(reps[0]["ts"])
        srows.append({
            "session": sid[:8], "day": start.isoformat(), "title": (s["title"] or "")[:70],
            "project": project_of(s["cwd"]), "task": task, "model": model, "family": family(model), "effort": effort,
            "replies": len(main), "subagent_replies": len(reps) - len(main),
            "cost": round(tot["cost"], 2), "sub_cost": round(sub_cost, 2),
            "sub_models": ", ".join(f"{k} ×{v}" for k, v in sub_models.most_common(3)),
            "cost_as_sonnet": round(tot["as_sonnet"], 2), "cost_as_haiku": round(tot["as_haiku"], 2), "cost_as_opus": round(tot["as_opus"], 2),
            "output_tokens": tot["out"], "cache_hit": round(100 * tot["read"] / cache_in, 1) if cache_in else None,
            "peak_context_k": round(peak_ctx / 1000), "start_context_k": round(start_ctx / 1000),
            "write_cost": round(tot["write_cost"], 2), "read_cost": round(tot["read_cost"], 2), "out_cost": round(tot["out_cost"], 2), "tool_calls": n_tools, "reads": reads, "edits": edits,
            "read_share": round(100 * reads / n_tools) if n_tools else 0,
            "window": "recent" if start >= recent_from else "prior", "unpriced_tokens": tot["unpriced"],
        })

    for r in srows:
        r["finding"], r["advice"], r["saving"] = judge(r)
        r["flags"] = ", ".join(x for x in (
            "subagents on Opus/Fable" if any(k in r["sub_models"] for k in ("opus", "fable")) else "",
            "long session" if r["replies"] >= 400 else "",
            "Fable" if r["family"] == "fable" else "",
            "no edits" if r["edits"] == 0 else "") if x)

    for r in srows:   # privacy: titles and project names are left out unless turned on
        for col, flag in OPTIONAL.items():
            if not ucfg.get(flag):
                r.pop(col, None)
    drows = [{"day": d, "family": fam, "cost": round(v["cost"], 2), "sub_cost": round(v["sub_cost"], 2),
              "replies": v["replies"], "output_tokens": v["output"], "write_cost": round(v["write_cost"], 2),
              "read_cost": round(v["read_cost"], 2), "out_cost": round(v["out_cost"], 2),
              "unpriced_tokens": v["unpriced"]} for (d, fam), v in sorted(daily.items()) if v["replies"]]
    return srows, drows


def day_of(r):
    return local_day(r["ts"]).isoformat()


def merge_daily(history, drows, since):
    """history/usage_daily.json: {day: {family: totals}}. Days still covered by the logs are
    replaced with fresh numbers; older days are never touched."""
    fresh = {}
    for r in drows:
        fresh.setdefault(r["day"], {})[r["family"]] = {k: v for k, v in r.items() if k not in ("day", "family")}
    out = {d: v for d, v in history.items() if d < since}
    out.update({d: v for d, v in fresh.items() if d >= since})
    for d, v in history.items():   # a day the logs no longer hold stays as it was
        out.setdefault(d, v)
    return dict(sorted(out.items()))


def daily_rows(history, today):
    cut = (today - dt.timedelta(days=RECENT_DAYS - 1)).isoformat()
    first = (today - dt.timedelta(days=DAYS - 1)).isoformat()
    return [{"day": d, "family": fam, **v, "window": "recent" if d >= cut else "prior"}
            for d, fams in history.items() if d >= first for fam, v in sorted(fams.items())]


def judge(r):
    """One finding per session, most valuable first. Returns (finding, advice, est. saving $)."""
    fam, eff = r["family"], r["effort"]
    big = fam in ("opus", "fable")
    sub_big = any(x in r["sub_models"] for x in ("opus", "fable"))
    sonnet_save = r["cost"] - r["cost_as_sonnet"]
    if fam == "fable" and r["edits"] >= 5 and r["task"] not in ("Plan", "Review"):
        return ("Fable did the building", "Plan on Fable or Opus at high effort, then build on Opus. Fable costs more per token.",
                round(r["cost"] - r["cost_as_opus"], 2))
    if big and r["subagent_replies"] >= 10 and sub_big and r["sub_cost"] > 1:
        share = r["sub_cost"] / max(r["cost"], 0.01)
        return ("Subagents ran on " + ("Fable" if "fable" in r["sub_models"] else "Opus"),
                "Search and reading subagents rarely need Opus. Give them Sonnet (judgment) or Haiku (pure search).",
                round(r["sub_cost"] * 0.5 * min(share * 2, 1), 2))
    if big and r["task"] in ("Research", "Question", "Writing") and r["edits"] == 0 and r["read_share"] >= 50 and r["cost"] > 0.5:
        return ("Reading job on a big model", "Sonnet at medium effort handles research and reading well for less.",
                round(sonnet_save, 2))
    if big and r["replies"] <= 6 and r["edits"] == 0 and r["cost"] > 0.3:
        return ("Quick question on a big model", "Short questions: Sonnet at low effort, or Haiku for lookups.", round(sonnet_save, 2))
    if r["replies"] >= 400 and r["peak_context_k"] >= 250:
        return ("Very long session", str(r["replies"]) + " replies, each re-reading up to " + str(r["peak_context_k"]) + "K tokens. Hand off to a fresh session when a task is done.",
                round(r["read_cost"] * 0.4, 2))
    if eff in ("xhigh", "max") and r["task"] in ("Question", "Writing", "Setup and ops", "Routine"):
        return ("High effort on a simple job", "Drop to medium for routine work. Keep high for planning, debugging and review.", round(r["cost"] * 0.3, 2))
    if r["cache_hit"] is not None and r["cache_hit"] < 70 and r["cost"] > 1:
        return ("Low cache reuse", "Long pauses or a changed setup made Claude re-read everything. Keep related work in one sitting.", round(r["cost"] * 0.2, 2))
    if fam == "opus" and r["task"] in ("Plan", "Review", "Fix a bug", "Build"):
        return ("Good fit", "Opus for planning, building, debugging and review is the right call.", 0)
    return ("", "", 0)


def write():
    today = dt.datetime.now(TZ).date()
    s, d = build(today=today)
    since = (today - dt.timedelta(days=DAYS - 1)).isoformat()
    hist_path = store.HISTORY / "usage_daily.json"
    hist = merge_daily(store.read_json(hist_path, {}), d, since)
    store.write_json(hist_path, hist)
    src = "Your local Claude Code session logs (~/.claude/projects), priced at list rates from OpenRouter"
    store.write_dataset("usage", s, src)
    store.write_dataset("usage_daily", daily_rows(hist, today), src)
    return s, d


if __name__ == "__main__":
    s, d = write()
    print(len(s), "sessions,", len(d), "day rows, $", round(sum(x["cost"] for x in s), 2),
          "| unpriced tokens:", sum(x["unpriced_tokens"] for x in s))
