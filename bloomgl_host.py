"""
bloomgl_host.py - Exact Orbloom GPU orb overlay in pure Python (no exe, no WebView).

Runs the verbatim Orbloom fragment shader (same source as bloomcs_host.cs /
pill.html WebGL Bloom) through desktop OpenGL loaded with ctypes, presenting
into a per-pixel-alpha layered window. Because this runs inside the (signed,
trusted) Python interpreter instead of a fresh unsigned .exe, Windows Smart
App Control cannot block it the way it blocks bloomcs_host.exe.

Stdin protocol (mirrors web_pill_host.py):
    show [state]   show the orb (listening/transcribing/done/error)
    theme [style]  accepted for parity (only bloomcs is routed here)
    state [state]  switch semantic state
    level [0..1]   microphone energy
    hide           conceal the orb
    quit           exit

Plus:  --snapshot <png> [frames]   render headless-ish, save PNG, exit
       --selftest                  threaded render check, exit 0/1
"""

import ctypes
import math
import os
import random
import struct
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import settings_manager

# NOTE: numpy/Pillow MUST be imported here at top level (main thread).
# Importing them lazily on the render thread deadlocks inside the import
# machinery while the main thread pumps the Win32 message loop.
import numpy as np
from PIL import Image

WIN = 72
RENDER = 144
FPS = 45
LOG_PATH = os.path.join(BASE_DIR, "bloomgl.log")

# --------------------------------------------------------------------------
# Win32
# --------------------------------------------------------------------------

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
opengl32 = ctypes.windll.opengl32
kernel32 = ctypes.windll.kernel32

_VP = ctypes.c_void_p
_SP = ctypes.c_ssize_t  # signed pointer width: big handles stay passable
_I = ctypes.c_int
_U = ctypes.c_uint
_B = ctypes.c_int  # BOOL


def _sig(fn, restype, *argtypes):
    if len(argtypes) == 1 and isinstance(argtypes[0], (list, tuple)):
        argtypes = argtypes[0]
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


# user32
_sig(user32.GetDC, _SP, [_VP])
_sig(user32.ReleaseDC, _I, [_VP, _VP])
_sig(user32.CreateWindowExW, _SP, [_U, ctypes.c_wchar_p, ctypes.c_wchar_p, _U,
                                  _I, _I, _I, _I, _VP, _VP, _VP, _VP])
_sig(user32.RegisterClassExW, _U, [ctypes.c_void_p])
_sig(user32.ShowWindow, _B, [_VP, _I])
_sig(user32.SetWindowPos, _B, [_VP, _VP, _I, _I, _I, _I, _U])
_sig(user32.GetWindowRect, _B, [_VP, _VP])
_sig(user32.SystemParametersInfoW, _B, [_U, _U, _VP, _U])
_sig(user32.GetCursorPos, _B, [_VP])
_sig(user32.SetCapture, _SP, [_VP])
_sig(user32.ReleaseCapture, _B, [])
# user32 (POINT-dependent entries are configured after the structs below)
# NOTE: handle-returning functions use SIGNED restype: some HDC/HWND values
# have the high bit set, and huge unsigned ints cannot be passed back into
# ctypes calls (OverflowError). As negatives they round-trip fine.
_sig(user32.GetMessageW, _I, [_VP, _VP, _U, _U])
_sig(user32.TranslateMessage, _B, [_VP])
_sig(user32.DispatchMessageW, _VP, [_VP])
_sig(user32.PostMessageW, _B, [_VP, _U, _VP, _VP])
_sig(user32.PostQuitMessage, None, [_I])
_sig(user32.DefWindowProcW, ctypes.c_ssize_t, [_VP, _U, _VP, _VP])
_sig(user32.DestroyWindow, _B, [_VP])
_sig(user32.GetSystemMetrics, _I, [_I])
# gdi32
_sig(gdi32.ChoosePixelFormat, _I, [_VP, _VP])
_sig(gdi32.SetPixelFormat, _B, [_VP, _I, _VP])
_sig(gdi32.SwapBuffers, _B, [_VP])
_sig(gdi32.CreateCompatibleDC, _SP, [_VP])
_sig(gdi32.CreateDIBSection, _SP, [_VP, _VP, _U, _VP, _VP, _U])
_sig(gdi32.SelectObject, _SP, [_VP, _VP])
_sig(gdi32.DeleteObject, _B, [_VP])
_sig(gdi32.DeleteDC, _B, [_VP])
# opengl32 wgl
_sig(opengl32.wglCreateContext, _SP, [_VP])
_sig(opengl32.wglMakeCurrent, _B, [_VP, _VP])
_sig(opengl32.wglDeleteContext, _B, [_VP])
_sig(opengl32.wglGetProcAddress, _SP, [ctypes.c_char_p])
# kernel32
_sig(kernel32.GetProcAddress, _SP, [_VP, ctypes.c_char_p])
_sig(kernel32.GetModuleHandleW, _SP, [ctypes.c_wchar_p])

# MonitorFromPoint takes POINT by value; declare separately
user32.MonitorFromPoint.restype = _VP

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App")
except Exception:
    pass

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
ULW_ALPHA = 2
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEMOVE = 0x0200
WM_DESTROY = 0x0002


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class PFD(ctypes.Structure):
    _fields_ = [("nSize", ctypes.c_short), ("nVersion", ctypes.c_short),
                ("dwFlags", ctypes.c_int), ("iPixelType", ctypes.c_ubyte),
                ("cColorBits", ctypes.c_ubyte), ("cRedBits", ctypes.c_ubyte),
                ("cRedShift", ctypes.c_ubyte), ("cGreenBits", ctypes.c_ubyte),
                ("cGreenShift", ctypes.c_ubyte), ("cBlueBits", ctypes.c_ubyte),
                ("cBlueShift", ctypes.c_ubyte), ("cAlphaBits", ctypes.c_ubyte),
                ("cAlphaShift", ctypes.c_ubyte), ("cAccumBits", ctypes.c_ubyte),
                ("cAccumRedBits", ctypes.c_ubyte), ("cAccumGreenBits", ctypes.c_ubyte),
                ("cAccumBlueBits", ctypes.c_ubyte), ("cAccumAlphaBits", ctypes.c_ubyte),
                ("cDepthBits", ctypes.c_ubyte), ("cStencilBits", ctypes.c_ubyte),
                ("cAuxBuffers", ctypes.c_ubyte), ("iLayerType", ctypes.c_ubyte),
                ("bReserved", ctypes.c_ubyte), ("dwLayerMask", ctypes.c_int),
                ("dwVisibleMask", ctypes.c_int), ("dwDamageMask", ctypes.c_int)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_int), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_short),
                ("biBitCount", ctypes.c_short), ("biCompression", ctypes.c_int),
                ("biSizeImage", ctypes.c_int), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_int),
                ("biClrImportant", ctypes.c_int)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_int)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_void_p),
                ("lpszClassName", ctypes.c_void_p), ("hIconSm", ctypes.c_void_p)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_int), ("pt", POINT)]


_sig(user32.MonitorFromPoint, _SP, [POINT, _U])
_sig(user32.GetMonitorInfoW, _B, [_VP, _VP])


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


# --------------------------------------------------------------------------
# Shaders — verbatim Orbloom (src/shaders.js); precision line dropped
# (desktop GLSL is high-precision by default). Variant: core-teal-01.
# --------------------------------------------------------------------------

VERT = b"""
attribute vec2 aPosition;
attribute vec2 aTextureCoord;
varying vec2 vUv;
void main() {
  vUv = aTextureCoord;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
"""

FRAG_PATH = os.path.join(BASE_DIR, "bloomcs", "orb.frag")
FRAG = None


def load_frag():
    global FRAG
    if FRAG is None:
        with open(FRAG_PATH, "rb") as f:
            FRAG = f.read()
    return FRAG


# Variant core-teal-01
PHASE = 4.6
ARCHETYPE = 2.0
GLASS = 0.4


def _hex(h):
    n = int(h[1:], 16)
    return ((n >> 16) & 255) / 255.0, ((n >> 8) & 255) / 255.0, (n & 255) / 255.0


BASE_C = _hex("#07262B")
ACC0 = _hex("#00C2A8")
ACC1 = _hex("#38E1FF")
ACC2 = _hex("#FFC65C")

# GL constants
VERTEX_SHADER = 0x8B31
FRAGMENT_SHADER = 0x8B30
COMPILE_STATUS = 0x8B81
LINK_STATUS = 0x8B82
ARRAY_BUFFER = 0x8892
STATIC_DRAW = 0x88E4
TRIANGLE_STRIP = 0x0005
COLOR_BUFFER_BIT = 0x4000
BLEND = 0x0BE2
ONE = 1
ONE_MINUS_SRC_ALPHA = 0x0303
RGBA = 0x1908
UNSIGNED_BYTE = 0x1401
FLOAT = 0x1406


# --------------------------------------------------------------------------
# GL function loader
# --------------------------------------------------------------------------

_gl_lib = opengl32
_wgl = {}


def _load(name, restype, argtypes, from_gl=True):
    addr = None
    try:
        addr = opengl32.wglGetProcAddress(name.encode("ascii"))
    except Exception:
        addr = None
    if not addr:
        try:
            hmod = kernel32.GetModuleHandleW("opengl32.dll")
            if hmod:
                addr = kernel32.GetProcAddress(hmod, name.encode("ascii"))
        except Exception:
            addr = None
    if not addr:
        return None
    proto = ctypes.WINFUNCTYPE(restype, *argtypes)
    return proto(addr)


class GLProc:
    pass


def load_gl():
    g = GLProc()
    c_void_p = ctypes.c_void_p
    c_uint = ctypes.c_uint
    c_int = ctypes.c_int
    c_float = ctypes.c_float
    g.Viewport = _load("glViewport", None, [c_int, c_int, c_int, c_int])
    g.ClearColor = _load("glClearColor", None, [c_float] * 4)
    g.Clear = _load("glClear", None, [c_uint])
    g.DrawArrays = _load("glDrawArrays", None, [c_uint, c_int, c_int])
    g.ReadPixels = _load("glReadPixels", None, [c_int, c_int, c_int, c_int, c_uint, c_uint, c_void_p])
    g.BlendFunc = _load("glBlendFunc", None, [c_uint, c_uint])
    g.Enable = _load("glEnable", None, [c_uint])
    g.Disable = _load("glDisable", None, [c_uint])
    g.CreateShader = _load("glCreateShader", c_uint, [c_uint])
    g.ShaderSource = _load("glShaderSource", None, [c_uint, c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(c_int)])
    g.CompileShader = _load("glCompileShader", None, [c_uint])
    g.GetShaderiv = _load("glGetShaderiv", None, [c_uint, c_uint, ctypes.POINTER(c_int)])
    g.GetShaderInfoLog = _load("glGetShaderInfoLog", None, [c_uint, c_int, ctypes.POINTER(c_int), ctypes.c_char_p])
    g.DeleteShader = _load("glDeleteShader", None, [c_uint])
    g.CreateProgram = _load("glCreateProgram", c_uint, [])
    g.AttachShader = _load("glAttachShader", None, [c_uint, c_uint])
    g.LinkProgram = _load("glLinkProgram", None, [c_uint])
    g.GetProgramiv = _load("glGetProgramiv", None, [c_uint, c_uint, ctypes.POINTER(c_int)])
    g.GetProgramInfoLog = _load("glGetProgramInfoLog", None, [c_uint, c_int, ctypes.POINTER(c_int), ctypes.c_char_p])
    g.UseProgram = _load("glUseProgram", None, [c_uint])
    g.DeleteProgram = _load("glDeleteProgram", None, [c_uint])
    g.GetAttribLocation = _load("glGetAttribLocation", c_int, [c_uint, ctypes.c_char_p])
    g.GetUniformLocation = _load("glGetUniformLocation", c_int, [c_uint, ctypes.c_char_p])
    g.Uniform1f = _load("glUniform1f", None, [c_int, c_float])
    g.Uniform2f = _load("glUniform2f", None, [c_int, c_float, c_float])
    g.Uniform3f = _load("glUniform3f", None, [c_int, c_float, c_float, c_float])
    g.GenBuffers = _load("glGenBuffers", None, [c_int, ctypes.POINTER(c_uint)])
    g.BindBuffer = _load("glBindBuffer", None, [c_uint, c_uint])
    g.BufferData = _load("glBufferData", None, [c_uint, ctypes.c_ssize_t, c_void_p, c_uint])
    g.EnableVertexAttribArray = _load("glEnableVertexAttribArray", None, [c_int])
    g.VertexAttribPointer = _load("glVertexAttribPointer", None, [c_int, c_int, c_uint, ctypes.c_bool, c_int, c_void_p])
    ok = all([g.Viewport, g.Clear, g.DrawArrays, g.ReadPixels, g.CreateShader,
              g.CreateProgram, g.Uniform1f, g.GetUniformLocation])
    return g if ok else None


# --------------------------------------------------------------------------
# Motion (port of motion.js)
# --------------------------------------------------------------------------

class Motion:
    def __init__(self, phase):
        self.phase = phase
        self.audio_smooth = 0.0
        self.audio_fast = 0.0
        self.spin_dir = 1
        self.spin_vel = 0.0
        self.prev_a = 0.0
        self.flip_queued = False
        self.osc_sign = 1
        self.spin = phase * 3.7
        self.last_t = None

    def advance(self, t, audio):
        dt = 0.0 if self.last_t is None else min(0.1, max(0.0, t - self.last_t))
        self.last_t = t
        x = min(1.0, max(0.0, audio))
        sc = 0.11 if x > self.audio_smooth else 0.30
        if dt > 0:
            self.audio_smooth += (x - self.audio_smooth) * (1 - math.exp(-dt / sc))
        fc = 0.04 if x > self.audio_fast else 0.18
        if dt > 0:
            self.audio_fast += (x - self.audio_fast) * (1 - math.exp(-dt / fc))
        pv = (6.31 * self.phase) % 1.0
        drift = 0.35 * math.sin(t * (0.11 + 0.08 * ((2.17 * self.phase) % 1)) + self.phase)
        osc = math.sin(t * (0.45 + 0.2 * pv) + self.phase)
        s = 1 if osc >= 0 else -1
        if s != self.osc_sign:
            self.osc_sign = s
            self.flip_queued = True
        if self.flip_queued and self.audio_fast < 0.18:
            self.spin_dir = -self.spin_dir
            self.flip_queued = False
        target = 0.65 * (0.65 + 0.7 * pv) * (1 + drift) + self.spin_dir * self.audio_fast * 2.2
        if dt > 0:
            self.spin_vel += (target - self.spin_vel) * (1 - math.exp(-dt / 0.35))
        attack = max(0.0, self.audio_fast - self.prev_a)
        self.prev_a = self.audio_fast
        self.spin_vel += self.spin_dir * min(6 * attack, 1.4) * dt * 14.0
        self.spin += self.spin_vel * dt


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

class Renderer:
    def __init__(self):
        self.gl = None
        self.prog = 0
        self.loc = {}
        self.hwnd = None
        self.hdc = None
        self.hgl = None
        self.last_error = ""
        self.ready = False

    def _compile(self, stype, src):
        g = self.gl
        sh = g.CreateShader(stype)
        arr = (ctypes.c_char_p * 1)(src)
        g.ShaderSource(sh, 1, arr, None)
        g.CompileShader(sh)
        ok = ctypes.c_int(0)
        g.GetShaderiv(sh, COMPILE_STATUS, ctypes.byref(ok))
        if not ok.value:
            buf = ctypes.create_string_buffer(4096)
            ln = ctypes.c_int(0)
            g.GetShaderInfoLog(sh, 4096, ctypes.byref(ln), buf)
            self.last_error = "compile: " + buf.value.decode("utf-8", "replace")
            return 0
        return sh

    def init(self, hwnd):
        self.hwnd = hwnd
        self.hdc = user32.GetDC(hwnd)
        pfd = PFD()
        pfd.nSize = ctypes.sizeof(PFD)
        pfd.nVersion = 1
        pfd.dwFlags = 0x4 | 0x20 | 0x1  # DRAW_TO_WINDOW | SUPPORT_OPENGL | DOUBLEBUFFER
        pfd.iPixelType = 0
        pfd.cColorBits = 32
        pfd.cAlphaBits = 8
        pfd.cDepthBits = 24
        pfd.iLayerType = 0
        pf = gdi32.ChoosePixelFormat(self.hdc, ctypes.byref(pfd))
        if not pf:
            self.last_error = "ChoosePixelFormat failed"
            return False
        if not gdi32.SetPixelFormat(self.hdc, pf, ctypes.byref(pfd)):
            self.last_error = "SetPixelFormat failed"
            return False
        self.hgl = opengl32.wglCreateContext(self.hdc)
        if not self.hgl:
            self.last_error = "wglCreateContext failed"
            return False
        if not opengl32.wglMakeCurrent(self.hdc, self.hgl):
            self.last_error = "wglMakeCurrent failed"
            return False
        self.gl = load_gl()
        if self.gl is None:
            self.last_error = "GL extension load failed"
            return False
        g = self.gl
        vs = self._compile(VERTEX_SHADER, VERT)
        if not vs:
            return False
        try:
            frag = load_frag()
        except Exception as ex:
            self.last_error = "frag load: %s" % ex
            return False
        fs = self._compile(FRAGMENT_SHADER, frag)
        if not fs:
            return False
        prog = g.CreateProgram()
        g.AttachShader(prog, vs)
        g.AttachShader(prog, fs)
        g.LinkProgram(prog)
        g.DeleteShader(vs)
        g.DeleteShader(fs)
        ok = ctypes.c_int(0)
        g.GetProgramiv(prog, LINK_STATUS, ctypes.byref(ok))
        if not ok.value:
            buf = ctypes.create_string_buffer(2048)
            ln = ctypes.c_int(0)
            g.GetProgramInfoLog(prog, 2048, ctypes.byref(ln), buf)
            self.last_error = "link: " + buf.value.decode("utf-8", "replace")
            return False
        self.prog = prog

        def U(n):
            return g.GetUniformLocation(prog, n.encode("ascii"))

        for n in ("uResolution", "uInteriorColor", "uBaseColor", "uAccentPrimary",
                  "uAccentSecondary", "uAccentHighlight", "uTime", "uSeed",
                  "uAudioBrightness", "uAudioPulse", "uSpin", "uArchetype",
                  "uGlass", "uVisualIntensity", "uDetail", "uGlow",
                  "uState", "uStateBlend"):
            self.loc[n] = U(n)
        a_pos = g.GetAttribLocation(prog, b"aPosition")
        a_tex = g.GetAttribLocation(prog, b"aTextureCoord")

        vbo = ctypes.c_uint(0)
        g.GenBuffers(1, ctypes.byref(vbo))
        g.BindBuffer(ARRAY_BUFFER, vbo.value)
        quad = (ctypes.c_float * 16)(-1, -1, 0, 1, 1, -1, 1, 1, -1, 1, 0, 0, 1, 1, 1, 0)
        g.BufferData(ARRAY_BUFFER, ctypes.sizeof(quad), quad, STATIC_DRAW)
        g.EnableVertexAttribArray(a_pos)
        g.VertexAttribPointer(a_pos, 2, FLOAT, False, 16, None)
        g.EnableVertexAttribArray(a_tex)
        g.VertexAttribPointer(a_tex, 2, FLOAT, False, 16, ctypes.c_void_p(8))

        g.Disable(0x0B71)
        g.Enable(BLEND)
        g.BlendFunc(ONE, ONE_MINUS_SRC_ALPHA)
        g.UseProgram(prog)
        g.Uniform3f(self.loc["uInteriorColor"], 0.0, 0.0, 0.0)
        g.Uniform3f(self.loc["uBaseColor"], *BASE_C)
        g.Uniform3f(self.loc["uAccentPrimary"], *ACC0)
        g.Uniform3f(self.loc["uAccentSecondary"], *ACC1)
        g.Uniform3f(self.loc["uAccentHighlight"], *ACC2)
        g.Uniform1f(self.loc["uSeed"], PHASE)
        g.Uniform1f(self.loc["uArchetype"], ARCHETYPE)
        g.Uniform1f(self.loc["uGlass"], GLASS)
        g.Uniform1f(self.loc["uVisualIntensity"], 1.0)
        g.Uniform1f(self.loc["uDetail"], 1.0)
        g.Uniform1f(self.loc["uGlow"], 1.0)
        g.Viewport(0, 0, RENDER, RENDER)
        # Release from init thread; the render thread acquires it.
        opengl32.wglMakeCurrent(None, None)
        self.ready = True
        return True

    def make_current(self):
        try:
            return bool(opengl32.wglMakeCurrent(self.hdc, self.hgl))
        except Exception:
            return False

    def draw(self, t, brightness, pulse, spin, state_idx, state_blend):
        g = self.gl
        n = RENDER
        g.Viewport(0, 0, n, n)
        g.ClearColor(0.0, 0.0, 0.0, 0.0)
        g.Clear(COLOR_BUFFER_BIT)
        g.UseProgram(self.prog)
        g.Uniform2f(self.loc["uResolution"], float(n), float(n))
        g.Uniform1f(self.loc["uTime"], t)
        g.Uniform1f(self.loc["uAudioBrightness"], brightness)
        g.Uniform1f(self.loc["uAudioPulse"], pulse)
        g.Uniform1f(self.loc["uSpin"], spin)
        g.Uniform1f(self.loc["uState"], float(state_idx))
        g.Uniform1f(self.loc["uStateBlend"], state_blend)
        g.DrawArrays(TRIANGLE_STRIP, 0, 4)
        # NOTE: read BEFORE any swap. The window is hidden (nothing is ever
        # displayed from it), and reading after SwapBuffers would return the
        # stale front buffer (a permanent 1-frame lag / blank first frame).
        buf = ctypes.create_string_buffer(n * n * 4)
        g.ReadPixels(0, 0, n, n, RGBA, UNSIGNED_BYTE, buf)
        return buf.raw

    def dispose(self):
        try:
            if self.hgl:
                opengl32.wglMakeCurrent(None, None)
                opengl32.wglDeleteContext(self.hgl)
                self.hgl = None
            if self.hwnd and self.hdc:
                user32.ReleaseDC(self.hwnd, self.hdc)
                self.hdc = None
        except Exception:
            pass


# --------------------------------------------------------------------------
# Layered overlay window
# --------------------------------------------------------------------------

_WNDPROC = None
HOST = None

# LRESULT WndProc(HWND, UINT, WPARAM, LPARAM)
WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p)


def _wndproc(hwnd, msg, wp, lp):
    try:
        if HOST is not None:
            return HOST.on_message(hwnd, msg, wp, lp) or 0
    except Exception:
        pass
    try:
        r = user32.DefWindowProcW(hwnd, msg, wp, lp)
        return r if r is not None else 0
    except Exception:
        return 0


def work_area():
    r = RECT()
    try:
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return (0, 0, 1920, 1040)


def primary_rect():
    try:
        import ctypes as _c

        class _R(_c.Structure):
            _fields_ = [("left", _c.c_long), ("top", _c.c_long),
                        ("right", _c.c_long), ("bottom", _c.c_long)]
        # SM_XVIRTUALSCREEN etc. avoided; use primary screen metrics
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        return (0, 0, w or 1920, h or 1040)
    except Exception:
        return (0, 0, 1920, 1040)


class OrbWindow:
    CLASS = "BloomGLOrb"

    def __init__(self):
        global _WNDPROC
        _WNDPROC = WNDPROCTYPE(_wndproc)
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = 0x0008  # CS_DBLCLKS
        wc.lpfnWndProc = ctypes.cast(_WNDPROC, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = ctypes.cast(ctypes.create_unicode_buffer(self.CLASS),
                                       ctypes.c_void_p).value
        # keep buffers alive
        self._class_name_buf = ctypes.create_unicode_buffer(self.CLASS)
        wc.lpszClassName = ctypes.cast(self._class_name_buf, ctypes.c_void_p).value
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            # already registered (re-spawn in same session) is fine
            pass
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(
            ex, self._class_name_buf, None, WS_POPUP,
            0, 0, WIN, WIN, None, None, hinst, None)
        if not self.hwnd:
            raise RuntimeError("CreateWindowEx failed")
        self.dragging = False
        self.drag_dx = 0
        self.drag_dy = 0
        self.place_initial()

    def place_initial(self):
        l, t, r, b = work_area()
        try:
            s = settings_manager.load_settings()
            cx, cy = s.get("pill_x"), s.get("pill_y")
        except Exception:
            cx, cy = None, None
        if cx is not None and cy is not None:
            x = max(l + 4, min(r - WIN - 4, int(cx)))
            y = max(t + 4, min(b - WIN - 4, int(cy)))
        else:
            x = (l + r - WIN) // 2
            y = b - WIN - 24
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)

    def ensure_visible(self):
        rect = RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        want = (rect.left, rect.top, rect.left + WIN, rect.top + WIN)
        # nearest monitor work area
        try:
            import ctypes as _c
            pt = POINT(rect.left + WIN // 2, rect.top + WIN // 2)
            mon = user32.MonitorFromPoint(pt, 2)
            mi_size = 72  # MONITORINFOEX size approx; use MONITORINFO instead

            class MI(_c.Structure):
                _fields_ = [("cbSize", _c.c_int), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", _c.c_int)]
            mi = MI()
            mi.cbSize = _c.sizeof(MI)
            if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                wa = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
            else:
                wa = work_area()
        except Exception:
            wa = work_area()
        x, y = want[0], want[1]
        moved = False
        if want[2] > wa[2]:
            x = wa[2] - WIN
            moved = True
        if want[3] > wa[3]:
            y = wa[3] - WIN
            moved = True
        if x < wa[0]:
            x = wa[0]
            moved = True
        if y < wa[1]:
            y = wa[1]
            moved = True
        if moved:
            user32.SetWindowPos(self.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
            try:
                s = settings_manager.load_settings()
                s["pill_x"] = x
                s["pill_y"] = y
                settings_manager.save_settings(s)
            except Exception:
                pass
            log("clamped to %s -> (%d,%d)" % (wa, x, y))
        if not (wa[0] <= x and x + WIN <= wa[2] and wa[1] <= y and y + WIN <= wa[3]):
            l, t, r, b = work_area()
            x = (l + r - WIN) // 2
            y = b - WIN - 24
            user32.SetWindowPos(self.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
            log("reset to bottom-center")

    def show(self):
        self.place_initial()
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self.ensure_visible()
        rect = RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        log("show at (%d,%d)" % (rect.left, rect.top))

    def hide(self):
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def present(self, rgba_bottom_up):
        n = RENDER
        a = np.frombuffer(rgba_bottom_up, dtype=np.uint8).reshape(n, n, 4)
        # bottom-up RGBA -> top-down BGRA premultiplied (GL already premultiplied)
        a = a[::-1, :, [2, 1, 0, 3]]
        raw = a.tobytes()
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = n
        bmi.bmiHeader.biHeight = -n  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            user32.ReleaseDC(None, screen_dc)
            return
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0,
                                      ctypes.byref(bits), None, 0)
        if not hbmp:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            return
        ctypes.memmove(bits, raw, len(raw))
        old = gdi32.SelectObject(mem_dc, hbmp)
        try:
            rect = RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            dst = POINT(rect.left, rect.top)
            size = SIZE(WIN, WIN)
            src = POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, 255, 1)
            ok = user32.UpdateLayeredWindow(self.hwnd, screen_dc, ctypes.byref(dst),
                                            ctypes.byref(size), mem_dc, ctypes.byref(src),
                                            0, ctypes.byref(blend), ULW_ALPHA)
            if os.environ.get("BLOOMGL_DEBUG_ULW"):
                _a = np.frombuffer(rgba_bottom_up, dtype=np.uint8).reshape(RENDER, RENDER, 4)
                log("ulw=%s alpha_sum=%d rgb_sum=%d" % (bool(ok), int(_a[:, :, 3].sum()), int(_a[:, :, :3].sum())))
                try:
                    _dib = _a[::-1][:, :, [2, 1, 0, 3]]
                    Image.fromarray(_dib[:, :, :3], "RGB").save(
                        "C:/Users/Lenovo/AppData/Local/Temp/opencode/dib_frame.png")
                except Exception as _ex2:
                    log("dib dump err: %s" % _ex2)
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)


# --------------------------------------------------------------------------
# Host: state, protocol, render loop
# --------------------------------------------------------------------------

def pill_to_orb_state(s):
    return {"listening": 3, "transcribing": 2, "done": 4, "error": 5}.get(s, 3)


class Host:
    def __init__(self):
        self.window = OrbWindow()
        self.renderer = Renderer()
        self.motion = Motion(PHASE)
        self.lock = threading.Lock()
        self.visible = False
        self.running = True
        self.state_idx = 3
        self.state_blend = 1.0
        self.last_state_at = None
        self.target_level = 0.08
        self.audio_smooth = 0.0
        self.audio_fast = 0.0
        self.time_offset = random.random() * 4000.0
        self.t0 = time.perf_counter()
        self.use_gl = False

    def set_state(self, s):
        with self.lock:
            idx = pill_to_orb_state(s)
            if idx != self.state_idx:
                self.state_idx = idx
                self.state_blend = 0.0
                self.last_state_at = None
            if s == "transcribing" and self.target_level < 0.35:
                self.target_level = 0.35

    def show(self):
        self.window.show()
        self.visible = True

    def hide(self):
        self.window.hide()
        self.visible = False

    def on_message(self, hwnd, msg, wp, lp):
        if msg == WM_LBUTTONDOWN:
            try:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                self.window.drag_dx = pt.x - rect.left
                self.window.drag_dy = pt.y - rect.top
                self.window.dragging = True
                user32.SetCapture(hwnd)
                log("drag-start (%d,%d)" % (rect.left, rect.top))
            except Exception:
                pass
            return 0
        if msg == WM_MOUSEMOVE and self.window.dragging:
            try:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetWindowPos(hwnd, None, pt.x - self.window.drag_dx,
                                    pt.y - self.window.drag_dy, 0, 0,
                                    SWP_NOSIZE | SWP_NOACTIVATE)
            except Exception:
                pass
            return 0
        if msg == WM_LBUTTONUP and self.window.dragging:
            self.window.dragging = False
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
            try:
                self.window.ensure_visible()
                rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                s = settings_manager.load_settings()
                s["pill_x"] = rect.left
                s["pill_y"] = rect.top
                settings_manager.save_settings(s)
                log("drag-end (%d,%d)" % (rect.left, rect.top))
            except Exception:
                pass
            return 0
        if msg == WM_LBUTTONDBLCLK:
            try:
                s = settings_manager.load_settings()
                s["pill_x"] = None
                s["pill_y"] = None
                settings_manager.save_settings(s)
            except Exception:
                pass
            self.window.place_initial()
            self.window.ensure_visible()
            return 0
        if msg == WM_DESTROY:
            self.running = False
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def stdin_loop(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            try:
                if cmd == "show":
                    self.set_state(arg or "listening")
                    self.show()
                elif cmd == "state":
                    self.set_state(arg or "listening")
                elif cmd == "level":
                    try:
                        with self.lock:
                            self.target_level = max(0.0, min(1.0, float(arg)))
                    except ValueError:
                        pass
                elif cmd == "theme":
                    pass
                elif cmd == "hide":
                    self.hide()
                elif cmd == "quit":
                    break
            except Exception as ex:
                log("cmd err: %s" % ex)
        self.running = False
        try:
            user32.PostMessageW(self.window.hwnd, WM_DESTROY, 0, 0)
        except Exception:
            pass
        try:
            os._exit(0)
        except Exception:
            pass

    def render_loop(self):
        if self.use_gl and not self.renderer.make_current():
            log("wglMakeCurrent failed on render thread")
        frame = 1.0 / FPS
        dbg = os.environ.get("BLOOMGL_DEBUG_ULW")
        while self.running:
            start = time.perf_counter()
            if self.visible:
                now = time.perf_counter() - self.t0
                with self.lock:
                    tlvl = self.target_level
                    idx = self.state_idx
                    dt = 0.0 if self.last_state_at is None else min(0.1, max(0.0, now - self.last_state_at))
                    self.last_state_at = now
                    if dt > 0:
                        self.state_blend += (1.0 - self.state_blend) * (1 - math.exp(-dt / 0.18))
                    blend = self.state_blend
                tc = 0.11 if tlvl > self.audio_smooth else 0.30
                self.audio_smooth += (tlvl - self.audio_smooth) * (1 - math.exp(-frame / tc))
                fc = 0.04 if tlvl > self.audio_fast else 0.18
                self.audio_fast += (tlvl - self.audio_fast) * (1 - math.exp(-frame / fc))
                t = now + self.time_offset
                try:
                    if self.use_gl:
                        self.motion.advance(t, min(1.0, tlvl))
                        b = min(1.0, self.motion.audio_smooth)
                        px = self.renderer.draw(t, b, b, self.motion.spin, idx, blend)
                    else:
                        px = gdi_circle(min(1.0, self.audio_smooth), now)
                    if os.environ.get("BLOOMGL_DUMP"):
                        try:
                            import numpy as _np
                            from PIL import Image as _Image
                            _a = _np.frombuffer(px, dtype=_np.uint8).reshape(RENDER, RENDER, 4)
                            _Image.fromarray(_a[::-1][:, :, :3], "RGB").save(
                                os.environ["BLOOMGL_DUMP"])
                            log("dumped frame b=%.2f blend=%.2f" % (b, blend))
                        except Exception as _ex:
                            log("dump err: %s" % _ex)
                        del os.environ["BLOOMGL_DUMP"]
                    self.window.present(px)
                except Exception as ex:
                    log("frame err: %s" % ex)
            spent = time.perf_counter() - start
            sleep = frame - spent
            if sleep > 0:
                time.sleep(sleep)

    def msg_loop(self):
        msg = MSG()
        while self.running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def run(self):
        global HOST
        HOST = self
        # Hidden GL init window. It MUST stay alive for the whole process:
        # destroying it invalidates the DC the GL context draws into.
        hinst = kernel32.GetModuleHandleW(None)
        self._gl_hwnd = user32.CreateWindowExW(0, "STATIC", None, WS_POPUP,
                                               0, 0, RENDER, RENDER, None, None, hinst, None)
        ok = self.renderer.init(self._gl_hwnd) if self._gl_hwnd else False
        if ok:
            self.use_gl = True
            log("GL renderer ready")
        else:
            self.use_gl = False
            log("GL failed (%s), GDI fallback" % self.renderer.last_error)
        threading.Thread(target=self.stdin_loop, daemon=True).start()
        threading.Thread(target=self.render_loop, daemon=True).start()
        self.msg_loop()
        self.renderer.dispose()
        try:
            if getattr(self, "_gl_hwnd", None):
                user32.DestroyWindow(self._gl_hwnd)
        except Exception:
            pass


def gdi_circle(voice, t):
    # Minimal fallback: smooth radial orb via PIL (only if GL is unavailable).
    from PIL import Image, ImageDraw
    import numpy as np
    n = RENDER
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    xx = (xx / (n - 1)) * 2 - 1
    yy = (yy / (n - 1)) * 2 - 1
    r = np.sqrt(xx ** 2 + yy ** 2)
    disc = r <= 1.0
    glow = np.exp(-r ** 2 * 5.0) * (0.35 + 0.65 * voice)
    col = np.zeros((n, n, 3))
    col[:, :, 0] = 7 + glow * 60
    col[:, :, 1] = 38 + glow * 190
    col[:, :, 2] = 43 + glow * 215
    rim = np.clip((r - 0.85) / 0.15, 0, 1)
    col += np.stack([rim * 40, rim * 150, rim * 170], axis=2)
    img = Image.fromarray(np.clip(col, 0, 255).astype("uint8"), "RGB")
    import io as _io
    # bottom-up RGBA bytes like glReadPixels
    a = np.zeros((n, n, 4), dtype="uint8")
    a[:, :, :3] = np.asarray(img)
    a[:, :, 3] = (disc * 255).astype("uint8")
    return a[::-1].tobytes()


def do_snapshot(path, frames):
    host = Host.__new__(Host)
    host.renderer = Renderer()
    hinst = kernel32.GetModuleHandleW(None)
    hwnd = user32.CreateWindowExW(0, "STATIC", None, WS_POPUP,
                                  0, 0, RENDER, RENDER, None, None, hinst, None)
    if not host.renderer.init(hwnd):
        print("GL init failed: " + host.renderer.last_error)
        return 1
    host.renderer.make_current()
    m = Motion(PHASE)
    last = None
    for i in range(frames):
        t = 1234.0 + i * (1.0 / FPS) + 777.0
        lvl = 0.15 + 0.65 * abs(math.sin(i * 0.35))
        m.advance(t, lvl)
        b = min(1.0, m.audio_smooth)
        last = host.renderer.draw(t, b, b, m.spin, 3, 1.0)
    import numpy as np
    from PIL import Image
    a = np.frombuffer(last, dtype=np.uint8).reshape(RENDER, RENDER, 4)
    img = Image.fromarray(a[::-1][:, :, :3], "RGB")
    img.save(path)
    print("SNAPSHOT-OK gl")
    return 0


def do_selftest():
    rend = Renderer()
    hinst = kernel32.GetModuleHandleW(None)
    hwnd = user32.CreateWindowExW(0, "STATIC", None, WS_POPUP,
                                  0, 0, RENDER, RENDER, None, None, hinst, None)
    if not rend.init(hwnd):
        print("SELFTEST-FAIL gl-init: " + rend.last_error)
        return 1
    out = [None] * 3
    err = []

    def worker():
        try:
            if not rend.make_current():
                raise RuntimeError("MakeCurrent false")
            m = Motion(PHASE)
            for i in range(3):
                t = 500.0 + i * 0.25 + 321.0
                m.advance(t, 0.6)
                out[i] = rend.draw(t, 0.6, 0.6, m.spin, 3, 1.0)
        except Exception as ex:
            err.append(ex)

    th = threading.Thread(target=worker)
    th.start()
    th.join(15)
    if th.is_alive():
        print("SELFTEST-FAIL worker-timeout")
        return 1
    rend.dispose()
    user32.DestroyWindow(hwnd)
    if err:
        print("SELFTEST-FAIL worker: %s" % err[0])
        return 1
    import numpy as np
    f2 = np.frombuffer(out[2], dtype=np.uint8).reshape(-1, 4).astype(int)
    alpha = int(f2[:, 3].sum())
    lum = int(f2[:, :3].sum())
    f0 = np.frombuffer(out[0], dtype=np.uint8).reshape(-1, 4).astype(int)
    diff = int(abs(f0 - f2).sum())
    print("alpha=%d lum=%d framediff=%d" % (alpha, lum, diff))
    if alpha < 100000 or lum < 100000 or diff < 10000:
        print("SELFTEST-FAIL dark-or-frozen")
        return 1
    print("SELFTEST-OK gl-threaded")
    return 0


def main():
    if os.environ.get("BLOOMGL_FAULT"):
        import faulthandler
        faulthandler.dump_traceback_later(6, exit=True)
    if len(sys.argv) >= 3 and sys.argv[1] == "--snapshot":
        try:
            frames = int(sys.argv[3]) if len(sys.argv) >= 4 else 24
        except ValueError:
            frames = 24
        sys.exit(do_snapshot(sys.argv[2], max(1, frames)))
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        sys.exit(do_selftest())
    host = Host()
    try:
        host.run()
    finally:
        try:
            host.renderer.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    main()

