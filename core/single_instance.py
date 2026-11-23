"""
single_instance.py - Guarantees only one instance of Quotal runs at any time.
If another instance is launched (e.g. user double-clicks desktop shortcut),
it signals the active instance to display the dashboard and immediately exits.
"""

import socket
import threading
from typing import Callable, Optional

PORT = 48291


class SingleInstance:
    def __init__(self, on_show_requested: Optional[Callable] = None):
        self.on_show_requested = on_show_requested
        self.sock = None
        self._running = True

    def check(self) -> bool:
        """
        Returns True if this is the primary instance (can proceed to run).
        Returns False if another instance is already running (signals it and should exit).
        """
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Bind to localhost port
            self.sock.bind(("127.0.0.1", PORT))
            self.sock.listen(2)
            
            # Start listener thread for subsequent launch signals
            t = threading.Thread(target=self._listen_loop, daemon=True)
            t.start()
            return True
        except OSError:
            # Port already in use -> existing instance is running!
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(2.0)
                client.connect(("127.0.0.1", PORT))
                client.sendall(b"show\n")
                client.close()
            except Exception:
                pass
            return False

    def _listen_loop(self):
        while self._running and self.sock:
            try:
                conn, _ = self.sock.accept()
                data = conn.recv(1024)
                msg = data.decode("utf-8", "ignore").strip()
                if msg == "version?":
                    # Handshake probe: lets the dashboard detect a live, up-to-date engine.
                    try:
                        conn.sendall(b"quotal:3\n")
                    except Exception:
                        pass
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                conn.close()
                if self.on_show_requested:
                    try:
                        self.on_show_requested(msg)
                    except TypeError:
                        self.on_show_requested()
            except Exception:
                break

    def close(self):
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
