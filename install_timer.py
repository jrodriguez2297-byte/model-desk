"""Install (or remove) the daily 6 AM refresh timer for this user. No admin rights needed.

    python install_timer.py            install and start
    python install_timer.py --remove   stop and remove
macOS uses launchd (~/Library/LaunchAgents); Linux uses a systemd user timer.
"""
import os, platform, shutil, subprocess, sys
from pathlib import Path

import store

LABEL = "com.modeldesk.refresh"


def fill(text):
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv is not installed. Get it from https://docs.astral.sh/uv/ and run this again.")
    path = os.pathsep.join(dict.fromkeys([str(Path(uv).parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    for k, v in {"{{UV}}": uv, "{{REPO}}": str(store.REPO), "{{HOME}}": str(store.HOME), "{{PATH}}": path}.items():
        text = text.replace(k, v)
    return text


def main(remove=False):
    store.ensure_home()
    src = store.REPO / "timer"
    if platform.system() == "Darwin":
        dest = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(dest)], capture_output=True)
        if remove:
            dest.unlink(missing_ok=True)
            return print("Removed the daily timer.")
        store.write_text(dest, fill((src / f"{LABEL}.plist").read_text()))
        subprocess.run(["launchctl", "bootstrap", domain, str(dest)], check=True)
        print(f"Installed. The refresh runs every day at 6 AM. Log: {store.HOME / 'refresh.log'}")
    elif platform.system() == "Linux":
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        if remove:
            subprocess.run(["systemctl", "--user", "disable", "--now", "model-desk.timer"], capture_output=True)
            for n in ("model-desk.service", "model-desk.timer"):
                (unit_dir / n).unlink(missing_ok=True)
            return print("Removed the daily timer.")
        for n in ("model-desk.service", "model-desk.timer"):
            store.write_text(unit_dir / n, fill((src / n).read_text()))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "model-desk.timer"], check=True)
        print(f"Installed. The refresh runs every day at 6 AM. Log: {store.HOME / 'refresh.log'}")
    else:
        sys.exit("Only macOS and Linux timers are included. Run `uv run refresh.py` from your own scheduler.")


if __name__ == "__main__":
    main(remove="--remove" in sys.argv)
