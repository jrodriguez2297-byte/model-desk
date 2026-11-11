from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Built from pieces so this file does not match itself.
WORDS = ["jar" + "vis", "Jo" + "sh", "Antoi" + "nette", "Mad" + "ame", "Cafe" + " OS", "Ta" + "mp"]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "vendor", "data", "cache", "findings"}
TEXT = {".py", ".md", ".json", ".html", ".js", ".css", ".plist", ".service", ".timer", ".example", ".toml", ""}


def test_no_personal_names_or_paths_in_the_repo():
    hits = []
    for p in REPO.rglob("*"):
        if not p.is_file() or SKIP_DIRS & set(p.relative_to(REPO).parts) or p.suffix not in TEXT or p.name == "LICENSE":
            continue
        text = p.read_text(errors="ignore")
        hits += [f"{p.relative_to(REPO)}: {w}" for w in WORDS if w in text]
    assert not hits, hits
