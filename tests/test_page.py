import datetime as dt, shutil, subprocess
from pathlib import Path

import pytest

import build_page, store

HOSTILE = '</script><img src=x onerror=alert(1)><script>alert(2)</script>'


def chrome():
    for c in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(c):
            return shutil.which(c)
    for c in (Path("/Applications/Google Chrome.app"), Path.home() / "Applications/Google Chrome.app"):
        if (c / "Contents/MacOS/Google Chrome").exists():
            return str(c / "Contents/MacOS/Google Chrome")
    return None


def make_page(tmp_path):
    store.ensure_home()
    repo = {"slug": "evil/" + HOSTILE, "name": HOSTILE, "owner": "evil", "url": "https://example.com", "desc": HOSTILE,
            "stars": 999, "forks": 1, "license": "MIT", "archived": False, "pushed": "2026-11-01", "created": dt.date.today().isoformat(),
            "days_since_push": 1, "age_days": 1, "source": "new", "cat": "Agents", "status": "new", "note": HOSTILE,
            "release": None, "release_date": None, "stars_per_day": 500, "momentum_basis": "lifetime",
            "stars_change_1d": None, "flags": "", "new_release": False, "history": "", "avatar": None}
    store.write_dataset("repos", [repo], HOSTILE)
    out = tmp_path / "dashboard.html"
    build_page.build(out)
    return out


def test_data_cannot_close_the_script_tag(tmp_path):
    html = make_page(tmp_path).read_text()
    assert HOSTILE not in html and "<img src=x" not in html
    assert "\\u003c/script\\u003e\\u003cimg src=x onerror=alert(1)" in html
    assert "http://" not in html.split("<script>")[0]    # the shell loads nothing from the network
    assert 'src="http' not in html.replace(build_page.safe_json({}), "")


def test_history_is_cut_to_90_days():
    rows = [{"day": "2026-01-01", "stars": 1}, {"day": "2026-11-01", "stars": 2}]
    out = build_page.trim("gh_history", {"rows": rows}, today=dt.date(2026, 11, 10))
    assert out["rows"] == [{"day": "2026-11-01", "stars": 2}]


def test_hostile_text_renders_as_text_in_a_real_browser(tmp_path):
    exe = chrome()
    if not exe:
        pytest.skip("no Chrome or Chromium found")
    page = make_page(tmp_path)
    dom = subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={tmp_path / 'profile'}",
                          "--virtual-time-budget=4000", "--dump-dom", page.as_uri()],
                         capture_output=True, text=True, timeout=120).stdout
    assert len(dom) > 100000 and "Model Desk" in dom and "evil/" in dom           # the page drew the repo
    assert '<img src="x"' not in dom and "<img src=x" not in dom and "<script>alert(2)" not in dom   # no live elements
    assert "&lt;img src=x onerror=alert(1)&gt;" in dom       # shown as plain text
