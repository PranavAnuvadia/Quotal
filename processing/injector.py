"""
injector.py - Inserts text into the currently active Windows window via Ctrl+V.
Uses Win32 SendInput for reliable, instantaneous keystroke injection with
non-destructive clipboard restoration.
"""

import time
import threading
import ctypes
from typing import Optional
import pyperclip

# Win32 SendInput constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56
VK_MENU = 0x12
VK_RMENU = 0xA5
VK_LMENU = 0xA4
VK_SHIFT = 0x10

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# Struct definitions for Win32 API SendInput (64-bit and 32-bit compliant)
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


def _release_modifiers():
    """Ensure any lingering Alt/Ctrl/Shift modifier keys are released before sending paste."""
    mods = [VK_RMENU, VK_LMENU, VK_MENU, VK_CONTROL, VK_SHIFT]
    events = (INPUT * len(mods))()
    for i, vk in enumerate(mods):
        events[i].type = INPUT_KEYBOARD
        events[i].union.ki.wVk = vk
        events[i].union.ki.wScan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        events[i].union.ki.dwFlags = KEYEVENTF_KEYUP
        events[i].union.ki.time = 0
        events[i].union.ki.dwExtraInfo = ULONG_PTR(0)
    ctypes.windll.user32.SendInput(len(mods), ctypes.byref(events), ctypes.sizeof(INPUT))


def _send_ctrl_v() -> bool:
    """Sends synthetic Ctrl+V key combination using Win32 SendInput."""
    _release_modifiers()
    time.sleep(0.015)

    events = (INPUT * 4)()
    vsc_ctrl = ctypes.windll.user32.MapVirtualKeyW(VK_CONTROL, 0)
    vsc_v = ctypes.windll.user32.MapVirtualKeyW(VK_V, 0)

    # 0: Ctrl down
    events[0].type = INPUT_KEYBOARD
    events[0].union.ki.wVk = VK_CONTROL
    events[0].union.ki.wScan = vsc_ctrl
    events[0].union.ki.dwFlags = 0
    events[0].union.ki.time = 0
    events[0].union.ki.dwExtraInfo = ULONG_PTR(0)

    # 1: V down
    events[1].type = INPUT_KEYBOARD
    events[1].union.ki.wVk = VK_V
    events[1].union.ki.wScan = vsc_v
    events[1].union.ki.dwFlags = 0
    events[1].union.ki.time = 0
    events[1].union.ki.dwExtraInfo = ULONG_PTR(0)

    # 2: V up
    events[2].type = INPUT_KEYBOARD
    events[2].union.ki.wVk = VK_V
    events[2].union.ki.wScan = vsc_v
    events[2].union.ki.dwFlags = KEYEVENTF_KEYUP
    events[2].union.ki.time = 0
    events[2].union.ki.dwExtraInfo = ULONG_PTR(0)

    # 3: Ctrl up
    events[3].type = INPUT_KEYBOARD
    events[3].union.ki.wVk = VK_CONTROL
    events[3].union.ki.wScan = vsc_ctrl
    events[3].union.ki.dwFlags = KEYEVENTF_KEYUP
    events[3].union.ki.time = 0
    events[3].union.ki.dwExtraInfo = ULONG_PTR(0)

    sent = ctypes.windll.user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 4


def paste_text(text: str, restore_clipboard: bool = False, delay_ms: int = 35) -> bool:
    """
    Copies text to clipboard and triggers Ctrl+V directly at current cursor position.
    """
    if not text:
        return False

    old_clip = None
    if restore_clipboard:
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass

    # Copy new text to clipboard
    try:
        pyperclip.copy(text)
    except Exception as e:
        print(f"[Injector Error] pyperclip.copy failed: {e}")

    # Pause for clipboard propagation
    time.sleep(delay_ms / 1000.0)

    # Inject Ctrl+V
    success = _send_ctrl_v()
    if not success:
        print(f"[Injector Warning] SendInput Ctrl+V returned failure (code {ctypes.GetLastError()})")

    # Optional clipboard restore
    if restore_clipboard and old_clip is not None:
        def _restore():
            time.sleep(0.40)
            try:
                if pyperclip.paste() == text:
                    pyperclip.copy(old_clip)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()

    return success
