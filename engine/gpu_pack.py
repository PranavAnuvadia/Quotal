"""Optional NVIDIA GPU acceleration pack for Quotal. Standard library only.

The installer ships CPU-only so every Windows PC gets a small, fast setup.
NVIDIA owners can fetch the CUDA wheel payload (cuBLAS + cuDNN, ~2 GB)
on demand from inside the dashboard.

Release convention (maintainer): attach QuotalGpuPack.zip — a zip of the
`nvidia/` folder from .venv/Lib/site-packages — to every GitHub release,
next to QuotalSetup.exe:
    https://github.com/<repo>/releases/latest/download/QuotalGpuPack.zip

Flow: refresh_status() probes driver + files + live CUDA. start_download()
fetches the zip with progress and extracts it over the app's site-packages.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

REPO = "PranavAnuvadia/Quotal"
PACK_ASSET = "QuotalGpuPack.zip"
PACK_URL = f"https://github.com/{REPO}/releases/latest/download/{PACK_ASSET}"
USER_AGENT = "Quotal-GpuPack/1.0"

_lock = threading.Lock()
_status = {"phase": "idle", "installed": False, "has_gpu": False,
          "live": False, "downloading": False, "downloaded": 0,
          "total": 0, "progress": "", "pct": None, "restart_required": False,
          "error": None}


def _site_packages() -> str:
    for cand in (os.path.join(sys.prefix, "Lib", "site-packages"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              ".venv", "Lib", "site-packages")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(sys.prefix, "Lib", "site-packages")


def _pack_dir() -> str:
    return os.path.join(_site_packages(), "nvidia")


def _files_present() -> bool:
    """cuBLAS core DLL unpacked (the one thing GPU inference can't do without)."""
    d = _pack_dir()
    try:
        return any(n.lower().startswith("cublas64_") and n.lower().endswith(".dll")
                   for n in os.listdir(os.path.join(d, "cublas", "bin")))
    except OSError:
        return False


def has_nvidia_gpu() -> bool:
    """Driver-level probe: nvidia-smi exists and lists a GPU."""
    if not shutil.which("nvidia-smi"):
        return False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                           text=True, timeout=10, creationflags=flags)
        return r.returncode == 0 and "GPU" in (r.stdout or "")
    except Exception:
        return False


def _ensure_dll_path() -> None:
    """Mirror of transcriber.setup_cuda_dlls (no heavy imports)."""
    base = os.path.join(_site_packages(), "nvidia")
    if not os.path.isdir(base):
        return
    try:
        for sub in os.listdir(base):
            for folder in ("bin", "lib"):
                d = os.path.join(base, sub, folder)
                if os.path.isdir(d):
                    try:
                        os.add_dll_directory(d)
                    except Exception:
                        pass
                    if d not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def libs_live() -> bool:
    """True if this process can actually see a CUDA device right now."""
    try:
        _ensure_dll_path()
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def refresh_status() -> dict:
    """Full probe (driver + files + live). Called on dashboard open / demand."""
    installed = _files_present()
    gpu = has_nvidia_gpu()
    live = libs_live() if (installed and gpu) else False
    with _lock:
        if not _status["downloading"]:
            _status.update({"installed": installed, "has_gpu": gpu, "live": live,
                            "restart_required": bool(installed and gpu and not live),
                            "phase": "idle", "error": None})
        else:
            _status.update({"installed": installed, "has_gpu": gpu, "live": live})
        return dict(_status)


def get_status() -> dict:
    with _lock:
        return dict(_status)


_refreshing = False


def refresh_async() -> None:
    """Asynchronously probe driver, files, and live CUDA in background."""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def _worker():
        global _refreshing
        try:
            refresh_status()
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=_worker, daemon=True).start()


def start_download() -> bool:
    with _lock:
        if _status["downloading"]:
            return True
        _status.update({"downloading": True, "downloaded": 0, "total": 0,
                        "progress": "Starting...", "pct": 0, "phase": "downloading",
                        "error": None})

    def _worker():
        tmp = None
        try:
            req = urllib.request.Request(PACK_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                total = int(r.headers.get("Content-Length") or 0)
                with _lock:
                    _status["total"] = total
                fd, tmp = tempfile.mkstemp(suffix=".zip")
                done = 0
                with os.fdopen(fd, "wb") as f:
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        with _lock:
                            _status["downloaded"] = done
                            if total > 0:
                                pct = max(0, min(99, round(done / total * 100)))
                                _status["pct"] = pct
                                _status["progress"] = f"{pct}%"
            with _lock:
                _status["progress"] = "Extracting..."
            with zipfile.ZipFile(tmp, "r") as z:
                z.extractall(_site_packages())
            with _lock:
                _status.update({"pct": 100, "progress": "Download complete!"})
            st = refresh_status()
            with _lock:
                _status["phase"] = "ready"
                _status["restart_required"] = not st["live"] and st["has_gpu"] and st["installed"]
        except Exception as e:
            with _lock:
                _status.update({"phase": "error", "error": str(e)[:160],
                                "progress": f"Download failed: {e}"[:160]})
        finally:
            with _lock:
                _status["downloading"] = False
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True).start()
    return True


def delete_pack() -> dict:
    """Remove the unpacked CUDA payload (~2 GB). Refuses mid-download.

    Note: Windows locks DLLs while the engine runs on GPU — switch Compute
    to CPU (or quit Quotal) first if removal reports locked files.
    """
    import shutil as _shutil

    with _lock:
        if _status["downloading"]:
            return {"ok": False, "reason": "downloading"}
    target = _pack_dir()
    try:
        if os.path.isdir(target):
            _shutil.rmtree(target)
    except Exception as e:
        return {"ok": False, "reason": str(e)[:120]}
    st = refresh_status()
    st["ok"] = True
    return st
