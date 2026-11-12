"""Shared storage helpers for Model Desk.

All user data lives in ~/.model-desk (override with MODEL_DESK_HOME):
  config.json, watchlist.json, data/, history/, findings/, cache/, backups/, dashboard.html
Every writer goes through write_json() so a crash never leaves a half-written file.
"""
import contextlib, datetime as dt, json, os, platform, shutil, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOME = Path(os.environ.get("MODEL_DESK_HOME") or Path.home() / ".model-desk").expanduser()
DATA, HISTORY, FINDINGS, CACHE, BACKUPS = (HOME / n for n in ("data", "history", "findings", "cache", "backups"))
LOCK = HOME / "refresh.lock"
KEEP_BACKUPS = 7
SCHEMA_VERSION = 1


def ensure_home():
    for p in (HOME, DATA, HISTORY, FINDINGS, CACHE, BACKUPS):
        p.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "watchlist.json"):
        if not (HOME / name).exists():
            shutil.copy(REPO / "defaults" / name, HOME / name)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_text(path, text):
    """Atomic write: temp file in the same folder, then rename over the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_json(path, obj, **kw):
    write_text(path, json.dumps(obj, **kw))


def flat(row):
    """Dashboard tables hold flat values: lists become ', '-joined text."""
    return {k: ", ".join(map(str, v)) if isinstance(v, (list, tuple, set)) else v for k, v in row.items()}


def write_dataset(name, rows, source, fetched=None):
    write_json(DATA / f"{name}.json", {"rows": [flat(r) for r in rows], "source": source,
                                       "fetched": fetched or now_iso()}, separators=(",", ":"))


def read_rows(name):
    return (read_json(DATA / f"{name}.json") or {}).get("rows", [])


# ---------- lock ----------
def _alive(pid):
    if os.name == "nt":   # os.kill(pid, 0) would end the process on Windows: trust a lock for 2 hours instead
        try:
            return time.time() - LOCK.stat().st_mtime < 7200
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class AlreadyRunning(Exception):
    pass


@contextlib.contextmanager
def lock():
    """One refresh at a time. A lock left by a dead process is taken over."""
    HOME.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and _alive(pid):
            raise AlreadyRunning(f"refresh already running (pid {pid})")
    write_text(LOCK, str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            LOCK.unlink()


# ---------- backups ----------
def backup(today=None):
    """Copy history/ and findings/log.json to backups/YYYY-MM-DD/ and keep the newest 7."""
    today = today or dt.date.today().isoformat()
    dest = BACKUPS / today
    if not dest.exists() and HISTORY.exists() and any(HISTORY.iterdir()):
        shutil.copytree(HISTORY, dest / "history")
        if (FINDINGS / "log.json").exists():
            shutil.copy(FINDINGS / "log.json", dest / "findings_log.json")
    if BACKUPS.exists():
        for old in sorted(p for p in BACKUPS.iterdir() if p.is_dir())[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)


# ---------- config ----------
def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) and k == "usage" else v
    return out


def config():
    """Defaults from the repo, with the user's ~/.model-desk/config.json winning key by key."""
    return _merge(read_json(REPO / "defaults" / "config.json"), read_json(HOME / "config.json", {}))


def is_apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def ram_gb(cfg=None):
    """RAM used for the fit label: a number in config wins; 'auto' detects it on Apple Silicon only."""
    v = (cfg or config()).get("ram_gb", "auto")
    if isinstance(v, (int, float)):
        return v
    if is_apple_silicon():
        try:
            return round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                            timeout=10).stdout) / 2**30)
        except Exception:
            return None
    return None


def time_zone(cfg=None):
    from zoneinfo import ZoneInfo
    v = (cfg or config()).get("time_zone", "auto")
    if v and v != "auto":
        return ZoneInfo(v)
    return dt.datetime.now().astimezone().tzinfo


def time_zone_name(cfg=None):
    v = (cfg or config()).get("time_zone", "auto")
    if v and v != "auto":
        return v
    try:
        return os.readlink("/etc/localtime").split("zoneinfo/")[-1]
    except OSError:
        return "UTC"


def mapping(name):
    return read_json(REPO / "mappings" / f"{name}.json", {})


def log(msg):
    print(msg, file=sys.stderr if msg.startswith("!") else sys.stdout, flush=True)
