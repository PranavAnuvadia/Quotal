"""
web_pill_host.py - Windows Overlay Host for WebGL Fluid Orb Pill.
Guarantees:
- 100% per-pixel DWM transparency (ZERO white background, ZERO black box).
- Interactive, native smooth dragging anywhere on screen (left-click + drag).
- Double-click pill to reset position back to bottom-center.
- Persistent placement: remembers where the user positioned it in settings.json.
- Only visible while speaking/processing; automatically vanishes when finished.
- Hidden from Alt+Tab and Taskbar (WS_EX_TOOLWINDOW).
- Never steals typing focus (WS_EX_NOACTIVATE).
"""

import sys
import os
import threading
import time
import ctypes
from ctypes import wintypes

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr:
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Enable per-monitor DPI awareness so Win32 APIs return true physical coordinates
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Set Application User Model ID so Windows associates the process with Quotal
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App")
except Exception:
    pass

import tempfile
import webview
import webview.platforms.winforms as wf
wf.cache_dir = os.path.join(tempfile.gettempdir(), "quotal_pill_wv")
from webview.platforms.winforms import BrowserView
import System
import System.Windows.Forms as WinForms
from System.Drawing import Color, Point, Size, Region
from System.Drawing.Drawing2D import GraphicsPath

from settings_manager import load_settings, save_settings

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pill.html")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_pill.log")


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = wintypes.INT
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

class _MARGINS(ctypes.Structure):
    _fields_ = [
        ('cxLeftWidth', ctypes.c_int),
        ('cxRightWidth', ctypes.c_int),
        ('cyTopHeight', ctypes.c_int),
        ('cyBottomHeight', ctypes.c_int),
    ]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080

win = None
form = None
hwnd = None
ready_event = threading.Event()

work_rect = wintypes.RECT()
user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_rect), 0)

pill_css_w = 296
pill_css_h = 56
pos_x = 0
pos_y = 0
phys_w = 0
phys_h = 0


def update_placement():
    global pos_x, pos_y, phys_w, phys_h
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_rect), 0)
    if form:
        phys_w = form.Size.Width
        phys_h = form.Size.Height
    else:
        phys_w = int(pill_css_w * 1.25)
        phys_h = int(pill_css_h * 1.25)

    settings = load_settings()
    custom_x = settings.get("pill_x")
    custom_y = settings.get("pill_y")

    if custom_x is not None and custom_y is not None:
        # User defined position clamped to work area
        pos_x = max(10, min(work_rect.right - phys_w - 10, int(custom_x)))
        pos_y = max(10, min(work_rect.bottom - phys_h - 10, int(custom_y)))
    else:
        # Default: Bottom center, just above taskbar
        pos_x = (work_rect.right - phys_w) // 2
        pos_y = work_rect.bottom - phys_h - 20


def save_current_position():
    """Saves user's dragged position to settings.json using real-time OS coordinates."""
    global pos_x, pos_y
    cur_hwnd = get_hwnd()
    if not cur_hwnd:
        return
    try:
        rect = wintypes.RECT()
        if user32.GetWindowRect(cur_hwnd, ctypes.byref(rect)):
            cur_x = int(rect.left)
            cur_y = int(rect.top)
            if cur_x > -500 and cur_y > -500:
                pos_x = cur_x
                pos_y = cur_y
                if form:
                    try:
                        def _sync():
                            form.Location = Point(cur_x, cur_y)
                        if form.InvokeRequired:
                            form.BeginInvoke(System.Action(_sync))
                        else:
                            _sync()
                    except Exception:
                        pass
                settings = load_settings()
                settings["pill_x"] = cur_x
                settings["pill_y"] = cur_y
                save_settings(settings)
                log(f"Saved custom pill position from GetWindowRect: ({cur_x}, {cur_y})")
    except Exception as e:
        log(f"Error saving pill position: {e}")


def reset_pill_position():
    """Resets pill back to default bottom-center."""
    global pos_x, pos_y
    try:
        settings = load_settings()
        settings["pill_x"] = None
        settings["pill_y"] = None
        save_settings(settings)
        update_placement()
        if form:
            def _move():
                form.Location = Point(pos_x, pos_y)
            form.Invoke(System.Action(_move))
        log("Pill position reset to bottom-center default.")
    except Exception as e:
        log(f"Error resetting pill position: {e}")


def get_hwnd():
    """Returns the current valid Win32 HWND for the Form."""
    global hwnd, form
    if form:
        try:
            h = form.Handle.ToInt64()
            if h and user32.IsWindow(h):
                hwnd = h
                api._hwnd = h
                return h
        except Exception:
            pass
    return hwnd


class DragApi:
    """Exposed to JavaScript for smooth native drag and repositioning."""
    def __init__(self):
        self._hwnd = None

    def start_drag(self):
        cur_hwnd = get_hwnd()
        if cur_hwnd:
            user32.ReleaseCapture()
            user32.SendMessageW(cur_hwnd, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
            save_current_position()
            apply_all_regions(pad=1)

    def stop_drag(self):
        save_current_position()
        apply_all_regions(pad=1)

    def reset_position(self):
        reset_pill_position()
        apply_all_regions(pad=1)


api = DragApi()


def create_capsule_region(w, h, pad=1):
    """Creates a GDI+ capsule Region with pad pixels to preserve anti-aliased borders."""
    pad_int = int(pad)
    hrgn = gdi32.CreateRoundRectRgn(
        -pad_int, -pad_int,
        int(w) + pad_int + 1, int(h) + pad_int + 1,
        int(h) + 2 * pad_int, int(h) + 2 * pad_int
    )
    r = Region.FromHrgn(System.IntPtr(hrgn))
    gdi32.DeleteObject(hrgn)
    return r


def apply_all_regions(pad=1):
    """
    Applies exact capsule clipping region to:
    1. The top-level Form HWND.
    2. All WinForms child controls in form.Controls.
    3. All native child HWNDs (WebView2, Chrome_WidgetWin_0, Intermediate D3D Window, etc.).
    Preserves 100% of the anti-aliased border with pad pixels while permanently cutting off corners.
    """
    global phys_w, phys_h
    cur_hwnd = get_hwnd()
    if not cur_hwnd or phys_w <= 0 or phys_h <= 0:
        return
    if form and not form.Visible:
        return

    pad_int = int(pad)

    # 1. Main window HWND
    try:
        hrgn = gdi32.CreateRoundRectRgn(
            -pad_int, -pad_int,
            phys_w + pad_int + 1, phys_h + pad_int + 1,
            phys_h + 2 * pad_int, phys_h + 2 * pad_int
        )
        user32.SetWindowRgn(cur_hwnd, hrgn, True)
    except Exception as e:
        log(f"Error setting main window region: {e}")

    # 2. Managed WinForms controls and Form.Region
    if form:
        def _apply_managed():
            try:
                form.Region = create_capsule_region(phys_w, phys_h, pad_int)
            except Exception:
                pass
            try:
                for c in form.Controls:
                    try:
                        c.Region = create_capsule_region(phys_w, phys_h, pad_int)
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            if form.InvokeRequired:
                form.BeginInvoke(System.Action(_apply_managed))
            else:
                _apply_managed()
        except Exception:
            pass

    # 3. Native child HWNDs (DirectComposition / Chromium / WebView2 render surfaces)
    try:
        def _enum_child(chwnd, _):
            try:
                hrgn_c = gdi32.CreateRoundRectRgn(
                    -pad_int, -pad_int,
                    phys_w + pad_int + 1, phys_h + pad_int + 1,
                    phys_h + 2 * pad_int, phys_h + 2 * pad_int
                )
                user32.SetWindowRgn(chwnd, hrgn_c, True)
            except Exception:
                pass
            return True

        cb = WNDENUMPROC(_enum_child)
        user32.EnumChildWindows(cur_hwnd, cb, 0)
    except Exception as e:
        log(f"Error applying regions to child windows: {e}")


def apply_dwm_borderless():
    """Completely disables Windows 11 DWM window border, Mica backdrop, and default corner rounding."""
    cur_hwnd = get_hwnd()
    if not cur_hwnd:
        return
    try:
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1
        c_pref = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(cur_hwnd, 33, ctypes.byref(c_pref), 4)

        # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE (removes border in Win11)
        c_none = ctypes.c_uint(0xFFFFFFFE)
        dwmapi.DwmSetWindowAttribute(cur_hwnd, 34, ctypes.byref(c_none), 4)

        # DWMWA_SYSTEMBACKDROP_TYPE = 38, DWMSBT_NONE = 1 (removes dark Mica backdrop)
        c_backdrop = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(cur_hwnd, 38, ctypes.byref(c_backdrop), 4)

        # Strip Win32 border/caption styles from GWL_STYLE
        GWL_STYLE = -16
        style = user32.GetWindowLongW(cur_hwnd, GWL_STYLE)
        user32.SetWindowLongW(cur_hwnd, GWL_STYLE, style & ~(0x00800000 | 0x00400000 | 0x00040000 | 0x00C00000))
    except Exception as e:
        log(f"Error applying DWM borderless attributes: {e}")


def apply_overlay_styles():
    global form, pos_x, pos_y, phys_w, phys_h

    if win:
        try:
            win.events.loaded.wait(timeout=5)
        except Exception:
            pass

    for _ in range(50):
        if win and win.uid in BrowserView.instances:
            form = BrowserView.instances[win.uid]
            break
        time.sleep(0.05)

    if form:
        update_placement()

        # 1. Configure Form & WebView2 background on UI thread FIRST
        def setup_form():
            form.ShowInTaskbar = False
            form.TopMost = True
            form.SetStyle(WinForms.ControlStyles.SupportsTransparentBackColor, True)
            form.BackColor = Color.Transparent
            for c in form.Controls:
                try:
                    c.DefaultBackgroundColor = Color.Transparent
                except Exception:
                    pass
            form.Location = Point(pos_x, pos_y)
            form.Size = Size(phys_w, phys_h)

            form.HandleCreated += lambda s, e: (get_hwnd(), apply_dwm_borderless())
            form.Resize += lambda s, e: apply_all_regions(pad=1)
            form.Move += lambda s, e: apply_all_regions(pad=1)
            form.ResizeEnd += lambda s, e: (save_current_position(), apply_all_regions(pad=1))
            form.LocationChanged += lambda s, e: (save_current_position() if (form and form.Visible) else None)

        form.Invoke(System.Action(setup_form))

        # 2. Grab LIVE HWND now that setup_form has finalized the Form properties
        cur_hwnd = get_hwnd()

        # 3. Eliminate Windows 11 DWM borders, dark Mica, and corner frames
        apply_dwm_borderless()

        # 4. Extend DWM frame for true per-pixel glass transparency
        m = _MARGINS(-1, -1, -1, -1)
        dwmapi.DwmExtendFrameIntoClientArea(cur_hwnd, ctypes.byref(m))

        # 5. Apply Win32 Extended Styles: ToolWindow, TopMost, NoActivate
        ex = user32.GetWindowLongW(cur_hwnd, GWL_EXSTYLE)
        new_ex = (ex & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        user32.SetWindowLongW(cur_hwnd, GWL_EXSTYLE, new_ex)

        # 6. OS-level capsule region with pad pixels (zero rectangular box, full colored border)
        apply_all_regions(pad=1)
        user32.ShowWindow(cur_hwnd, SW_HIDE)
        log(f"True transparent styles applied with padded capsule clipping. HWND={cur_hwnd}, pos=({pos_x},{pos_y}), size=({phys_w},{phys_h})")


def do_show(state="listening"):
    global win, form, pos_x, pos_y, phys_w, phys_h
    cur_hwnd = get_hwnd()
    if not win or not cur_hwnd:
        return
    update_placement()
    log(f"Showing pill (state={state}) at ({pos_x},{pos_y})")

    def _show_form():
        if form:
            form.TopMost = True
            form.Location = Point(pos_x, pos_y)
            form.Size = Size(phys_w, phys_h)
            form.Show()
            form.BringToFront()
            apply_dwm_borderless()
            apply_all_regions(pad=1)

    if form:
        try:
            form.Invoke(System.Action(_show_form))
        except Exception as e:
            log(f"Error in _show_form: {e}")

    user32.SetWindowPos(
        cur_hwnd, HWND_TOPMOST, pos_x, pos_y, phys_w, phys_h,
        SWP_NOACTIVATE | SWP_SHOWWINDOW
    )
    user32.ShowWindow(cur_hwnd, SW_SHOWNOACTIVATE)
    apply_all_regions(pad=1)

    # Deferred re-assertion on UI thread to ensure region survives initial paint/layout passes
    if form:
        def _deferred_reapply():
            time.sleep(0.04)
            try:
                form.BeginInvoke(System.Action(lambda: apply_all_regions(pad=1)))
            except Exception:
                pass
        threading.Thread(target=_deferred_reapply, daemon=True).start()

    try:
        win.evaluate_js(f"window.setState('{state}')")
    except Exception:
        pass


def do_hide():
    global win, form
    log("Hiding pill")

    cur_hwnd = get_hwnd()
    if cur_hwnd:
        user32.ShowWindow(cur_hwnd, SW_HIDE)

    def _hide_form():
        if form:
            form.Hide()

    if form:
        try:
            form.Invoke(System.Action(_hide_form))
        except Exception:
            pass


def stdin_listener():
    """Reads commands from parent process via stdin."""
    global win
    ready_event.wait()

    for line in sys.stdin:
        line = line.strip()
        if not line or not win:
            continue

        parts = line.split(" ", 1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if cmd == "show":
                state = arg or "listening"
                do_show(state)
            elif cmd == "state":
                win.evaluate_js(f"window.setState('{arg}')")
            elif cmd == "level":
                try:
                    lvl = float(arg)
                    win.evaluate_js(f"window.setLevel({lvl})")
                except ValueError:
                    pass
            elif cmd == "hide":
                do_hide()
            elif cmd == "quit":
                log("Quitting host")
                win.destroy()
                break
        except Exception as e:
            log(f"Error handling command {cmd}: {e}")


def on_ready():
    """Called when webview window is loaded."""
    apply_overlay_styles()
    ready_event.set()


def main():
    global win
    log("web_pill_host starting with per-pixel DWM transparency...")

    win = webview.create_window(
        "Quotal Fluid Orb",
        url=HTML_PATH,
        js_api=api,
        width=pill_css_w,
        height=pill_css_h,
        min_size=(296, 56),
        frameless=True,
        transparent=True,
        on_top=True,
        hidden=True
    )

    t = threading.Thread(target=stdin_listener, daemon=True)
    t.start()

    webview.start(on_ready, gui="edgechromium")
    os._exit(0)


if __name__ == "__main__":
    main()
