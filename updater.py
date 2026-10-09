"""OTA updates for Quotal via GitHub Releases. Standard library only.

Release convention (the maintainer must follow this when shipping):
  - Git tag:            vX.Y.Z  (e.g. v1.1.0)
  - Release asset name: QuotalSetup.exe  (stable name, same every release)

The stable asset name is what makes "latest" downloads work without the app
having to know future version numbers:
  https://github.com/<repo>/releases/latest/download/QuotalSetup.exe

Flow: check_update() compares version.py against the latest GitHub release.
If newer, the dashboard offers one click: download_update() fetches the new
installer with progress, then launch_installer_and_quit() starts it silently
and quits the app so the installer can replace files.
"""

import json
import os
import subprocess
import tempfile
import threading
import urllib.request

from version import __version__

REPO = "PranavAnuvadia/Quotal"
ASSET_NAME = "QuotalSetup.exe"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
STABLE_DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/{ASSET_NAME}"
USER_AGENT = "Quotal-Updater/1.0"

_status_lock = threading.Lock()
_status = {"phase": "idle", "current": __version__, "latest": None,
           "url": None, "notes": "", "downloaded": 0, "total": 0,
           "path": None, "error": None}


def _ver_tuple(s: str):
    parts = []
    for p in str(s).strip().lstrip("vV").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _get_json(url: str, timeout: int = 8):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def check_update(timeout: int = 8) -> dict:
    """Ask GitHub for the latest release. Never raises; returns a status dict."""
    try:
        rel = _get_json(API_LATEST, timeout=timeout)
        tag = rel.get("tag_name", "")
        if not tag:
            return {"status": "error", "current": __version__, "error": "no releases found"}
        latest = ".".join(str(n) for n in _ver_tuple(tag))
        if _ver_tuple(tag) > _ver_tuple(__version__):
            url = STABLE_DOWNLOAD_URL
            for a in rel.get("assets", []) or []:
                if a.get("name") == ASSET_NAME and a.get("browser_download_url"):
                    url = a["browser_download_url"]
                    break
            return {"status": "available", "current": __version__, "latest": latest,
                    "url": url, "notes": rel.get("body", "") or ""}
        return {"status": "current", "current": __version__, "latest": latest}
    except Exception as e:
        return {"status": "error", "current": __version__, "error": str(e)[:160]}


def refresh_async() -> None:
    """Re-check in the background and stash the result for the dashboard."""
    def _w():
        r = check_update()
        with _status_lock:
            _status.update({"phase": "available" if r["status"] == "available" else "idle"})
            _status.update({k: v for k, v in r.items() if k != "status"})
            _status["error"] = r.get("error")
    threading.Thread(target=_w, daemon=True).start()


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def download_update(url: str, dest: str = None, progress_cb=None, timeout: int = 30) -> str:
    """Download the installer with optional progress callback(downloaded, total)."""
    if not dest:
        dest = os.path.join(tempfile.gettempdir(), ASSET_NAME)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass
    return dest


def download_async(on_done=None) -> None:
    """Download the available update in the background, updating shared status."""
    def _w():
        with _status_lock:
            url = _status.get("url")
            _status["phase"] = "downloading"
            _status["downloaded"] = 0
            _status["total"] = 0
        if not url:
            with _status_lock:
                _status["phase"] = "error"
                _status["error"] = "no update URL"
            return

        def _prog(done, total):
            with _status_lock:
                _status["downloaded"] = done
                _status["total"] = total

        try:
            path = download_update(url, progress_cb=_prog)
            with _status_lock:
                _status["phase"] = "ready"
                _status["path"] = path
        except Exception as e:
            with _status_lock:
                _status["phase"] = "error"
                _status["error"] = str(e)[:160]
        finally:
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
    threading.Thread(target=_w, daemon=True).start()


def launch_installer_and_quit(installer_path: str, on_quit) -> bool:
    """Start the new installer silently, then quit this app so files unlock."""
    try:
        subprocess.Popen([installer_path, "/SILENT", "/NORESTART"],
                         close_fds=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    try:
        on_quit()
    except Exception:
        pass
    return True
