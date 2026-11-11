"""Build ~/.model-desk/dashboard.html: one self-contained file, no network needed to open it.

The page (page/index.html) is the same code the hosted dashboard uses. Here its runtime is
replaced by page/shim.js, d3 is inlined, and every dataset is inlined as JSON.
Third-party text (repo names and descriptions) only ever travels as JSON data: "<" is written
as \\u003c so nothing in it can close the script tag, and the page shows it with textContent.
"""
import datetime as dt, json

import store

PAGE = store.REPO / "page"
DATASETS = ["aa_models", "arena", "local", "crosswalk", "uncensored", "repos", "usage", "usage_daily",
            "gh_history", "gh_trends", "gh_findings", "gh_ledger", "status"]
HISTORY_DAYS = 90   # the page gets at most this much star history; the full history stays on disk


def safe_json(obj):
    """JSON that is safe inside a <script> tag."""
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def trim(name, payload, today=None):
    if name in ("gh_history", "gh_trends"):
        cut = ((today or dt.date.today()) - dt.timedelta(days=HISTORY_DAYS)).isoformat()
        payload = {**payload, "rows": [r for r in payload.get("rows", []) if str(r.get("day", "")) >= cut]}
    return payload


def build(out=None, today=None):
    cfg = store.config()
    blocks = []
    for name in DATASETS:
        payload = store.read_json(store.DATA / f"{name}.json")
        if payload is None:
            continue   # first run, or a source that has never worked: the page shows an empty state
        blocks.append(f'<script type="application/json" id="ds-{name}">{safe_json(trim(name, payload, today))}</script>')
    ram = store.ram_gb(cfg)
    settings = {"generated": store.now_iso(), "time_zone": store.time_zone_name(cfg), "ram_gb": ram,
                "fit_checked": ram is not None}
    body = (PAGE / "index.html").read_text()
    html = "\n".join([
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Model Desk</title>",
        "<style>" + (PAGE / "shell.css").read_text() + "</style>",
        "</head>", "<body>", '<div id="dash-root">',
        *blocks,
        f"<script>window.MODEL_DESK = {safe_json(settings)};</script>",
        "<script>" + (PAGE / "vendor" / "d3.min.js").read_text() + "</script>",
        "<script>" + (PAGE / "shim.js").read_text() + "</script>",
        body, "</div>", "</body>", "</html>", ""])
    out = out or store.HOME / "dashboard.html"
    store.write_text(out, html)
    return out


if __name__ == "__main__":
    print(build())
