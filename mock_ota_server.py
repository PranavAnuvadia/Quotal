"""
mock_ota_server.py - Lightweight local mock server to test Quotal OTA update flow.
Serves:
  - GET /releases/latest -> returns JSON announcing v1.0.1
  - GET /download/QuotalSetup.exe -> streams the compiled installer
"""

import http.server
import json
import os
import socketserver
import sys
import time

PORT = 49152
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALLER_PATH = os.path.join(BASE_DIR, "installer", "Output", "QuotalSetup.exe")
PATCH_PATH = os.path.join(BASE_DIR, "installer", "Output", "QuotalPatch.zip")

class MockOTAHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/releases/latest"):
            patch_sz = os.path.getsize(PATCH_PATH) if os.path.exists(PATCH_PATH) else 0
            setup_sz = os.path.getsize(INSTALLER_PATH) if os.path.exists(INSTALLER_PATH) else 0
            payload = {
                "tag_name": "v1.0.3",
                "assets": [
                    {
                        "name": "QuotalPatch.zip",
                        "size": patch_sz,
                        "browser_download_url": f"http://127.0.0.1:{PORT}/download/QuotalPatch.zip"
                    },
                    {
                        "name": "QuotalSetup.exe",
                        "size": setup_sz,
                        "browser_download_url": f"http://127.0.0.1:{PORT}/download/QuotalSetup.exe"
                    }
                ],
                "body": "Multilingual dictation (100+ languages, auto-detect), Languages sidebar tab, and Whisper Large-v3 Turbo support."
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/download/QuotalPatch.zip"):
            if not os.path.exists(PATCH_PATH):
                self.send_error(404, "Patch zip not found on disk")
                return
            size = os.path.getsize(PATCH_PATH)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="QuotalPatch.zip"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(PATCH_PATH, "rb") as f:
                while chunk := f.read(1024 * 512):
                    self.wfile.write(chunk)
                    time.sleep(0.04)
        elif self.path.startswith("/download/QuotalSetup.exe"):
            if not os.path.exists(INSTALLER_PATH):
                self.send_error(404, "Installer not found on disk")
                return
            size = os.path.getsize(INSTALLER_PATH)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="QuotalSetup.exe"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(INSTALLER_PATH, "rb") as f:
                while chunk := f.read(1024 * 512):
                    self.wfile.write(chunk)
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        # Clean logging
        print(f"[MockOTA] {self.address_string()} - {format % args}", flush=True)

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), MockOTAHandler) as httpd:
        print(f"[MockOTA] Server running on http://127.0.0.1:{PORT}", flush=True)
        print(f"[MockOTA] Serving installer: {INSTALLER_PATH} ({os.path.getsize(INSTALLER_PATH)/(1024*1024):.1f} MB)", flush=True)
        httpd.serve_forever()
