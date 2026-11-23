"""Capture the Windows dictation destination before the overlay appears."""

import ctypes
import ntpath
import sys
from ctypes import wintypes
from functools import lru_cache


@lru_cache(maxsize=1)
def _win32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetForegroundWindow.argtypes = ()
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return user, kernel


def capture_app_context() -> dict:
    if sys.platform != "win32":
        return {}
    user, kernel = _win32()
    window = user.GetForegroundWindow()
    if not window:
        return {}
    title = ctypes.create_unicode_buffer(512)
    user.GetWindowTextW(window, title, len(title))
    process_id = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    process_name = ""
    handle = kernel.OpenProcess(0x1000, False, process_id)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                process_name = ntpath.basename(path.value)
        finally:
            kernel.CloseHandle(handle)
    return {"process_name": process_name, "window_title": title.value}
