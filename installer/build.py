"""Build QuotalSetup.exe with Inno Setup 6.

- Reads the version from version.py (single source of truth).
- Compiles installer/quotal.iss -> installer/Output/QuotalSetup.exe
  (the exact asset name updater.py downloads for OTA updates).

Usage:
    python installer/build.py
"""

import os
import re
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISS_PATH = os.path.join(BASE_DIR, "installer", "quotal.iss")
OUT_DIR = os.path.join(BASE_DIR, "installer", "Output")

ISCC_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def read_version() -> str:
    with open(os.path.join(BASE_DIR, "version.py"), encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', src)
    if not m:
        raise SystemExit("version.py: __version__ not found")
    return m.group(1)


def find_iscc() -> str:
    for p in ISCC_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise SystemExit("ISCC.exe not found (install Inno Setup 6: winget install JRSoftware.InnoSetup)")


def build_patch() -> str:
    """Build lightweight QuotalPatch.zip (~1 MB) containing only app code."""
    out_zip = os.path.join(OUT_DIR, "QuotalPatch.zip")
    EXCLUDE_DIRS = {'.git', '.venv', 'installer', '__pycache__', 'models', 'Lib', 'download-page'}
    EXCLUDE_EXTS = {'.log', '.pyc', '.exe'}
    EXCLUDE_FILES = {'history.jsonl', 'settings.json', 'smart_enhancer_new.py', 'sitecustomize.py'}

    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in EXCLUDE_EXTS or f in EXCLUDE_FILES:
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, BASE_DIR)
                z.write(full, rel)
    size_mb = os.path.getsize(out_zip) / (1024 * 1024)
    print(f"[build] Patch created: {out_zip} ({size_mb:.2f} MB)")
    return out_zip


def main() -> None:
    ver = read_version()
    iscc = find_iscc()
    os.makedirs(OUT_DIR, exist_ok=True)
    build_patch()
    print(f"[build] Quotal v{ver} -> installer/Output/QuotalSetup.exe")
    print(f"[build] compiler: {iscc}")
    subprocess.run([iscc, f"/DMyAppVersion={ver}", f"/O{OUT_DIR}", ISS_PATH], check=True)
    out = os.path.join(OUT_DIR, "QuotalSetup.exe")
    size_gb = os.path.getsize(out) / (1024 ** 3)
    print(f"[build] done: {out} ({size_gb:.2f} GB)")


if __name__ == "__main__":
    sys.exit(main())
