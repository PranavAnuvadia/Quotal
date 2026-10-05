"""
injector.py - Inserts text into the currently active Windows window via Ctrl+V.
Uses Win32 SendInput for reliable, instantaneous keystroke injection with
non-destructive clipboard restoration.
"""

import time
import threading
import ctypes
import pyperclip

# Win32 SendInput constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

# Struct definitions for Win32 API SendInput
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    ]


def _send_ctrl_v():
    """Sends synthetic Ctrl+V key combination using Win32 SendInput."""
    # Key down Ctrl, Key down V, Key up V, Key up Ctrl
    events = (INPUT * 4)()
    
    # 0: Ctrl down
    events[0].type = INPUT_KEYBOARD
    events[0].ki.wVk = VK_CONTROL
    events[0].ki.dwFlags = 0

    # 1: V down
    events[1].type = INPUT_KEYBOARD
    events[1].ki.wVk = VK_V
    events[1].ki.dwFlags = 0

    # 2: V up
    events[2].type = INPUT_KEYBOARD
    events[2].ki.wVk = VK_V
    events[2].ki.dwFlags = KEYEVENTF_KEYUP

    # 3: Ctrl up
    events[3].type = INPUT_KEYBOARD
    events[3].ki.wVk = VK_CONTROL
    events[3].ki.dwFlags = KEYEVENTF_KEYUP

    ctypes.windll.user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))


def paste_text(text: str, restore_clipboard: bool = False, delay_ms: int = 15):
    """
    Copies text to clipboard and triggers Ctrl+V.
    Leaves the dictated text in the clipboard so the user can paste it anywhere.
    """
    if not text:
        return

    old_clip = None
    if restore_clipboard:
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass

    # Copy new text to clipboard
    pyperclip.copy(text)
    
    # Brief pause so the OS clipboard registers the new content (15ms is optimal on Win32)
    time.sleep(delay_ms / 1000.0)
    
    # Inject Ctrl+V
    _send_ctrl_v()

    # Asynchronously restore previous clipboard so user doesn't lose copied content
    if restore_clipboard and old_clip is not None:
        def _restore():
            time.sleep(0.35)
            try:
                # Only restore if clipboard still holds what we pasted.
                # If the user copied something fresh in the meantime, preserve their new copy!
                current = pyperclip.paste()
                if current == text:
                    pyperclip.copy(old_clip)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()
