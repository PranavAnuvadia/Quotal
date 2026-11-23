"""Build the optional GPU pack: QuotalGpuPack.zip.

Zips .venv/Lib/site-packages/nvidia (cuBLAS + cuDNN) with an `nvidia/`
root folder, so the dashboard can extract it straight over the installed
app's own site-packages (see gpu_pack.py).

Upload the resulting installer/Output/QuotalGpuPack.zip as a GitHub
release asset next to QuotalSetup.exe.

Usage:
    python installer/build_gpu_pack.py
"""

import os
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, ".venv", "Lib", "site-packages", "nvidia")
OUT_DIR = os.path.join(BASE_DIR, "installer", "Output")
OUT_ZIP = os.path.join(OUT_DIR, "QuotalGpuPack.zip")


def main() -> None:
    if not os.path.isdir(SRC_DIR):
        raise SystemExit(f"nvidia payload not found: {SRC_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(SRC_DIR):
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.join("nvidia", os.path.relpath(full, SRC_DIR))
                z.write(full, arc)
                count += 1
    size_gb = os.path.getsize(OUT_ZIP) / (1024 ** 3)
    print(f"[gpu-pack] {count} files -> {OUT_ZIP} ({size_gb:.2f} GB)")


if __name__ == "__main__":
    sys.exit(main())
