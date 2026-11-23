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
import sys
import subprocess
import tempfile
import threading
import urllib.request

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from version import __version__

REPO = "PranavAnuvadia/Quotal"
PATCH_ASSET = "QuotalPatch.zip"
SETUP_ASSET = "QuotalSetup.exe"
STABLE_DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/{PATCH_ASSET}"
USER_AGENT = "Quotal-Updater/1.0"


def _get_api_url() -> str:
    """Check for local mock server first for testing, otherwise GitHub API."""
    if os.environ.get("QUOTAL_UPDATE_API"):
        return os.environ["QUOTAL_UPDATE_API"]
    try:
        import socket
        with socket.create_connection(("127.0.0.1", 49152), timeout=0.15):
            return "http://127.0.0.1:49152/releases/latest"
    except Exception:
        pass
    return f"https://api.github.com/repos/{REPO}/releases/latest"


_status_lock = threading.Lock()
_status = {"phase": "idle", "current": __version__, "latest": None,
           "url": None, "kind": "patch", "notes": "", "downloaded": 0, "total": 0,
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
        api_url = _get_api_url()
        rel = _get_json(api_url, timeout=timeout)
        tag = rel.get("tag_name", "")
        if not tag:
            return {"status": "error", "current": __version__, "error": "no releases found"}
        latest = ".".join(str(n) for n in _ver_tuple(tag))
        if _ver_tuple(tag) > _ver_tuple(__version__):
            # Auto-detect whether this is a small update (code patch) or a big update (full runtime)
            url = None
            kind = "patch"
            size_bytes = 0
            assets = rel.get("assets", []) or []
            # Check for patch first
            for a in assets:
                if a.get("name") == PATCH_ASSET and a.get("browser_download_url"):
                    url = a["browser_download_url"]
                    size_bytes = a.get("size", 0)
                    kind = "patch"
                    break
            # Fall back to full installer if no patch exists
            if not url:
                for a in assets:
                    if a.get("name") == SETUP_ASSET and a.get("browser_download_url"):
                        url = a["browser_download_url"]
                        size_bytes = a.get("size", 0)
                        kind = "setup"
                        break
            if not url:
                url = STABLE_DOWNLOAD_URL
                kind = "patch"

            update_type = "Minor Update" if kind == "patch" else "Major Update"
            size_mb = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes > 0 else ("Minor Update" if kind == "patch" else "~460 MB")

            return {"status": "available", "current": __version__, "latest": latest,
                    "url": url, "kind": kind, "update_type": update_type, "size_str": size_mb,
                    "notes": rel.get("body", "") or ""}
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
    """Download the update asset with optional progress callback(downloaded, total)."""
    if not dest:
        ext = ".zip" if "zip" in url.lower() else ".exe"
        filename = f"QuotalUpdate{ext}"
        dest = os.path.join(tempfile.gettempdir(), filename)
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


def apply_patch_and_restart(patch_zip: str, on_quit) -> bool:
    """Applies code-only patch zip over install directory and restarts app."""
    install_dir = _ROOT_DIR
    quotal_exe = os.path.join(install_dir, "Quotal.exe")
    if not os.path.exists(quotal_exe):
        quotal_exe = sys.executable

    helper_script = os.path.join(tempfile.gettempdir(), "_quotal_patcher.py")
    helper_code = '''
import time, os, zipfile, subprocess, sys, socket

PORT = 48291
# 1. Wait until old instance quits and kill any lingering Quotal instances
for _ in range(40):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", PORT))
        s.close()
        break
    except OSError:
        time.sleep(0.1)

try:
    flags = 0x08000000 if sys.platform == "win32" else 0
    subprocess.run(["taskkill", "/F", "/IM", "Quotal.exe"], capture_output=True, creationflags=flags)
except Exception:
    pass

time.sleep(0.5)

install_dir = INSTALL_DIR_PLACEHOLDER
patch_zip = PATCH_ZIP_PLACEHOLDER
exe_to_launch = EXE_TO_LAUNCH_PLACEHOLDER

# 2. Extract patch files
try:
    with zipfile.ZipFile(patch_zip, 'r') as z:
        z.extractall(install_dir)
except Exception as e:
    try:
        with open(os.path.join(install_dir, "patch_error.log"), "w", encoding="utf-8") as ef:
            ef.write(str(e))
    except Exception:
        pass

# 3. Relaunch Quotal
time.sleep(0.5)
try:
    flags = 0x08000000 if sys.platform == "win32" else 0
    subprocess.Popen([exe_to_launch], cwd=install_dir, creationflags=flags)
except Exception as e:
    try:
        with open(os.path.join(install_dir, "patch_error.log"), "a", encoding="utf-8") as ef:
            ef.write("\\nLaunch error: " + str(e))
    except Exception:
        pass

# 4. Clean up
time.sleep(0.5)
try:
    if os.path.exists(patch_zip):
        os.remove(patch_zip)
    if os.path.exists(__file__):
        os.remove(__file__)
except Exception:
    pass
'''.strip()

    helper_code = helper_code.replace("INSTALL_DIR_PLACEHOLDER", repr(install_dir))
    helper_code = helper_code.replace("PATCH_ZIP_PLACEHOLDER", repr(patch_zip))
    helper_code = helper_code.replace("EXE_TO_LAUNCH_PLACEHOLDER", repr(quotal_exe))

    with open(helper_script, "w", encoding="utf-8") as f:
        f.write(helper_code)

    try:
        py_exe = os.path.join(install_dir, ".venv", "Scripts", "python.exe")
        if not os.path.exists(py_exe):
            py_exe = sys.executable
        subprocess.Popen([py_exe, helper_script], creationflags=0x08000000)
    except Exception as e:
        print(f"[Updater Error] Failed to launch patcher: {e}")
        return False

    try:
        threading.Thread(target=on_quit, daemon=True).start()
    except Exception:
        pass
    return True


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
