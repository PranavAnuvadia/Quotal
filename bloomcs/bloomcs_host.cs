using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace BloomCs
{
    // ======================================================================
    // Native interop (user32 / gdi32 / opengl32 / kernel32 only, no NuGet)
    // ======================================================================
    internal static class Native
    {
        public const int GWL_EXSTYLE = -20;
        public const int WS_EX_LAYERED = 0x80000;
        public const int WS_EX_TOOLWINDOW = 0x80;
        public const int WS_EX_NOACTIVATE = 0x08000000;
        public const int WS_EX_TOPMOST = 0x00000008;
        public const int WS_EX_APPWINDOW = 0x00040000;
        public const int ULW_ALPHA = 2;
        public const byte AC_SRC_OVER = 0;
        public const byte AC_SRC_ALPHA = 1;
        public const int SW_SHOWNOACTIVATE = 4;
        public const int SW_HIDE = 0;
        public const int WM_LBUTTONDOWN = 0x0201;
        public const int WM_LBUTTONUP = 0x0202;
        public const int WM_LBUTTONDBLCLK = 0x0203;
        public const int WM_NCLBUTTONDOWN = 0x00A1;
        public const int HTCAPTION = 2;
        public const uint SPI_GETWORKAREA = 0x0030;

        [StructLayout(LayoutKind.Sequential)]
        public struct BLENDFUNCTION
        {
            public byte BlendOp;
            public byte BlendFlags;
            public byte SourceConstantAlpha;
            public byte AlphaFormat;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT
        {
            public int Left; public int Top; public int Right; public int Bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct PIXELFORMATDESCRIPTOR
        {
            public short nSize;
            public short nVersion;
            public int dwFlags;
            public byte iPixelType;
            public byte cColorBits;
            public byte cRedBits; public byte cRedShift;
            public byte cGreenBits; public byte cGreenShift;
            public byte cBlueBits; public byte cBlueShift;
            public byte cAlphaBits; public byte cAlphaShift;
            public byte cAccumBits;
            public byte cAccumRedBits; public byte cAccumGreenBits;
            public byte cAccumBlueBits; public byte cAccumAlphaBits;
            public byte cDepthBits; public byte cStencilBits;
            public byte cAuxBuffers; public byte iLayerType;
            public byte bReserved;
            public int dwLayerMask; public int dwVisibleMask; public int dwDamageMask;
        }

        public const int PFD_DRAW_TO_WINDOW = 0x00000004;
        public const int PFD_SUPPORT_OPENGL = 0x00000020;
        public const int PFD_DOUBLEBUFFER = 0x00000001;
        public const byte PFD_TYPE_RGBA = 0;
        public const byte PFD_MAIN_PLANE = 0;

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool UpdateLayeredWindow(IntPtr hwnd, IntPtr hdcDst,
            ref Point pptDst, ref Size psize, IntPtr hdcSrc, ref Point pptSrc,
            int crKey, ref BLENDFUNCTION pblend, int dwFlags);

        [DllImport("user32.dll")]
        public static extern IntPtr GetDC(IntPtr hWnd);
        [DllImport("user32.dll")]
        public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
        [DllImport("gdi32.dll")]
        public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
        [DllImport("gdi32.dll")]
        public static extern bool DeleteDC(IntPtr hdc);
        [DllImport("gdi32.dll")]
        public static extern IntPtr SelectObject(IntPtr hdc, IntPtr hgdiobj);
        [DllImport("gdi32.dll")]
        public static extern bool DeleteObject(IntPtr hObject);
        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")]
        public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
            int X, int Y, int cx, int cy, uint uFlags);
        public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
        public const uint SWP_NOSIZE = 0x0001;
        public const uint SWP_NOMOVE = 0x0002;
        public const uint SWP_NOACTIVATE = 0x0010;
        public const uint SWP_SHOWWINDOW = 0x0040;
        [DllImport("user32.dll")]
        public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
        [DllImport("user32.dll")]
        public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
        [DllImport("user32.dll")]
        public static extern void ReleaseCapture();
        [DllImport("user32.dll")]
        public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")]
        public static extern bool SystemParametersInfoW(uint uiAction, uint uiParam, ref RECT pvParam, uint fWinIni);

        [DllImport("gdi32.dll")]
        public static extern int ChoosePixelFormat(IntPtr hdc, ref PIXELFORMATDESCRIPTOR ppfd);
        [DllImport("gdi32.dll")]
        public static extern bool SetPixelFormat(IntPtr hdc, int iPixelFormat, ref PIXELFORMATDESCRIPTOR ppfd);
        [DllImport("gdi32.dll")]
        public static extern bool SwapBuffers(IntPtr hdc);

        [DllImport("opengl32.dll")]
        public static extern IntPtr wglCreateContext(IntPtr hdc);
        [DllImport("opengl32.dll")]
        public static extern bool wglMakeCurrent(IntPtr hdc, IntPtr hglrc);
        [DllImport("opengl32.dll")]
        public static extern bool wglDeleteContext(IntPtr hglrc);
        [DllImport("opengl32.dll")]
        public static extern IntPtr wglGetProcAddress(string name);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi)]
        public static extern IntPtr GetProcAddress(IntPtr hModule, string name);
        [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
        public static extern IntPtr GetModuleHandle(string name);

        [DllImport("shcore.dll")]
        public static extern int SetProcessDpiAwareness(int value);
        [DllImport("user32.dll")]
        public static extern bool SetProcessDPIAware();
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        public static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    }

    // ======================================================================
    // Minimal OpenGL loader (core 1.1 via opengl32, shaders via extension)
    // ======================================================================
    internal static class GL
    {
        public const uint VERTEX_SHADER = 0x8B31;
        public const uint FRAGMENT_SHADER = 0x8B30;
        public const uint COMPILE_STATUS = 0x8B81;
        public const uint LINK_STATUS = 0x8B82;
        public const uint ARRAY_BUFFER = 0x8892;
        public const uint STATIC_DRAW = 0x88E4;
        public const uint TRIANGLE_STRIP = 0x0005;
        public const uint COLOR_BUFFER_BIT = 0x00004000;
        public const uint BLEND = 0x0BE2;
        public const uint ONE = 1;
        public const uint ONE_MINUS_SRC_ALPHA = 0x0303;
        public const uint RGBA = 0x1908;
        public const uint UNSIGNED_BYTE = 0x1401;
        public const uint FLOAT = 0x1406;

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void ViewportDelegate(int x, int y, int w, int h);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void ClearColorDelegate(float r, float g, float b, float a);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void ClearDelegate(uint mask);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void DrawArraysDelegate(uint mode, int first, int count);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void ReadPixelsDelegate(int x, int y, int w, int h, uint fmt, uint type, IntPtr pixels);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void BlendFuncDelegate(uint s, uint d);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void EnableDelegate(uint cap);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void DisableDelegate(uint cap);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate uint CreateShaderDelegate(uint type);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate void ShaderSourceDelegate(uint shader, int count, string[] src, int[] len);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void CompileShaderDelegate(uint shader);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void GetShaderivDelegate(uint shader, uint pname, out int param);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate void GetShaderInfoLogDelegate(uint shader, int maxLen, out int len, StringBuilder log);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void DeleteShaderDelegate(uint shader);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate uint CreateProgramDelegate();
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void AttachShaderDelegate(uint prog, uint shader);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void LinkProgramDelegate(uint prog);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void GetProgramivDelegate(uint prog, uint pname, out int param);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate void GetProgramInfoLogDelegate(uint prog, int maxLen, out int len, StringBuilder log);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void UseProgramDelegate(uint prog);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void DeleteProgramDelegate(uint prog);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate int GetAttribLocationDelegate(uint prog, string name);
        [UnmanagedFunctionPointer(CallingConvention.StdCall, CharSet = CharSet.Ansi)]
        public delegate int GetUniformLocationDelegate(uint prog, string name);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void Uniform1fDelegate(int loc, float v);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void Uniform2fDelegate(int loc, float x, float y);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void Uniform3fDelegate(int loc, float x, float y, float z);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void GenBuffersDelegate(int n, out uint buffers);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void BindBufferDelegate(uint target, uint buffer);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void BufferDataDelegate(uint target, IntPtr size, float[] data, uint usage);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void EnableVertexAttribArrayDelegate(int index);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void VertexAttribPointerDelegate(int index, int size, uint type, bool norm, int stride, IntPtr ptr);

        public static ViewportDelegate Viewport;
        public static ClearColorDelegate ClearColor;
        public static ClearDelegate Clear;
        public static DrawArraysDelegate DrawArrays;
        public static ReadPixelsDelegate ReadPixels;
        public static BlendFuncDelegate BlendFunc;
        public static EnableDelegate Enable;
        public static DisableDelegate Disable;
        public static CreateShaderDelegate CreateShader;
        public static ShaderSourceDelegate ShaderSource;
        public static CompileShaderDelegate CompileShader;
        public static GetShaderivDelegate GetShaderiv;
        public static GetShaderInfoLogDelegate GetShaderInfoLog;
        public static DeleteShaderDelegate DeleteShader;
        public static CreateProgramDelegate CreateProgram;
        public static AttachShaderDelegate AttachShader;
        public static LinkProgramDelegate LinkProgram;
        public static GetProgramivDelegate GetProgramiv;
        public static GetProgramInfoLogDelegate GetProgramInfoLog;
        public static UseProgramDelegate UseProgram;
        public static DeleteProgramDelegate DeleteProgram;
        public static GetAttribLocationDelegate GetAttribLocation;
        public static GetUniformLocationDelegate GetUniformLocation;
        public static Uniform1fDelegate Uniform1f;
        public static Uniform2fDelegate Uniform2f;
        public static Uniform3fDelegate Uniform3f;
        public static GenBuffersDelegate GenBuffers;
        public static BindBufferDelegate BindBuffer;
        public static BufferDataDelegate BufferData;
        public static EnableVertexAttribArrayDelegate EnableVertexAttribArray;
        public static VertexAttribPointerDelegate VertexAttribPointer;

        private static T Load<T>(string name) where T : class
        {
            IntPtr p = Native.wglGetProcAddress(name);
            if (p == IntPtr.Zero)
            {
                IntPtr mod = Native.GetModuleHandle("opengl32.dll");
                if (mod != IntPtr.Zero) p = Native.GetProcAddress(mod, name);
            }
            if (p == IntPtr.Zero) return null;
            return Marshal.GetDelegateForFunctionPointer(p, typeof(T)) as T;
        }

        public static bool LoadAll()
        {
            Viewport = Load<ViewportDelegate>("glViewport");
            ClearColor = Load<ClearColorDelegate>("glClearColor");
            Clear = Load<ClearDelegate>("glClear");
            DrawArrays = Load<DrawArraysDelegate>("glDrawArrays");
            ReadPixels = Load<ReadPixelsDelegate>("glReadPixels");
            BlendFunc = Load<BlendFuncDelegate>("glBlendFunc");
            Enable = Load<EnableDelegate>("glEnable");
            Disable = Load<DisableDelegate>("glDisable");
            CreateShader = Load<CreateShaderDelegate>("glCreateShader");
            ShaderSource = Load<ShaderSourceDelegate>("glShaderSource");
            CompileShader = Load<CompileShaderDelegate>("glCompileShader");
            GetShaderiv = Load<GetShaderivDelegate>("glGetShaderiv");
            GetShaderInfoLog = Load<GetShaderInfoLogDelegate>("glGetShaderInfoLog");
            DeleteShader = Load<DeleteShaderDelegate>("glDeleteShader");
            CreateProgram = Load<CreateProgramDelegate>("glCreateProgram");
            AttachShader = Load<AttachShaderDelegate>("glAttachShader");
            LinkProgram = Load<LinkProgramDelegate>("glLinkProgram");
            GetProgramiv = Load<GetProgramivDelegate>("glGetProgramiv");
            GetProgramInfoLog = Load<GetProgramInfoLogDelegate>("glGetProgramInfoLog");
            UseProgram = Load<UseProgramDelegate>("glUseProgram");
            DeleteProgram = Load<DeleteProgramDelegate>("glDeleteProgram");
            GetAttribLocation = Load<GetAttribLocationDelegate>("glGetAttribLocation");
            GetUniformLocation = Load<GetUniformLocationDelegate>("glGetUniformLocation");
            Uniform1f = Load<Uniform1fDelegate>("glUniform1f");
            Uniform2f = Load<Uniform2fDelegate>("glUniform2f");
            Uniform3f = Load<Uniform3fDelegate>("glUniform3f");
            GenBuffers = Load<GenBuffersDelegate>("glGenBuffers");
            BindBuffer = Load<BindBufferDelegate>("glBindBuffer");
            BufferData = Load<BufferDataDelegate>("glBufferData");
            EnableVertexAttribArray = Load<EnableVertexAttribArrayDelegate>("glEnableVertexAttribArray");
            VertexAttribPointer = Load<VertexAttribPointerDelegate>("glVertexAttribPointer");
            return Viewport != null && Clear != null && DrawArrays != null && ReadPixels != null
                && CreateShader != null && CreateProgram != null && Uniform1f != null;
        }
    }

    // ======================================================================
    // Orbloom shaders — verbatim from https://github.com/rickybharti/orbloom
    // (src/shaders.js). Only change: the `precision highp float;` line is
    // dropped because desktop GLSL is high-precision by default.
    // Variant baked: core-teal-01.
    // ======================================================================
    internal static class Shaders
    {
        public const string Vert = @"
attribute vec2 aPosition;
attribute vec2 aTextureCoord;
varying vec2 vUv;
void main() {
  vUv = aTextureCoord;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
";

        public const string Frag = @"
varying vec2 vUv;
uniform vec2 uResolution;
uniform vec3 uInteriorColor;
uniform vec3 uBaseColor;
uniform vec3 uAccentPrimary;
uniform vec3 uAccentSecondary;
uniform vec3 uAccentHighlight;
uniform float uTime;
uniform float uSeed;
uniform float uAudioBrightness;
uniform float uAudioPulse;
uniform float uSpin;
uniform float uArchetype;
uniform float uGlass;
uniform float uVisualIntensity;
uniform float uDetail;
uniform float uGlow;
uniform float uState;
uniform float uStateBlend;

float scalarHash(float value) {
  return fract(sin(value * 127.1) * 43758.5453);
}

vec4 sampleSky(vec3 direction, float time) {
  float longitude = atan(direction.z, direction.x);
  float latitude = asin(clamp(direction.y, -1.0, 1.0));
  float varianceA = fract(uSeed * 7.13);
  float varianceB = fract(uSeed * 3.71);
  float varianceC = fract(uSeed * 5.37);

  float type = uArchetype >= 0.0 ? uArchetype : floor(fract(uSeed * 9.73) * 4.0);
  float nebulaType = step(0.5, type) * (1.0 - step(1.5, type));
  float coreType = step(1.5, type) * (1.0 - step(2.5, type));
  float deepType = step(2.5, type);

  float planeOffset = latitude
    + (0.15 + 0.4 * varianceA) * sin(longitude * (1.0 + floor(varianceB * 2.0)) + 1.3)
    + 0.12 * sin(longitude * 3.0 + time * 0.1);
  float band = exp(-planeOffset * planeOffset * (5.0 + 10.0 * varianceC));
  band = mix(band, max(band, 0.8), nebulaType);
  band *= 1.0 - 0.85 * deepType;

  float waveA = sin(longitude * 2.0 + sin(latitude * 3.0 + time * 0.25) * 1.6 + time * 0.15);
  float waveB = sin(longitude * 5.0 - sin(latitude * 4.0 - time * 0.2) * 1.2 - time * 0.22 + 2.4);
  float cloud = pow(0.5 + 0.5 * waveA, 2.0) * (0.45 + 0.55 * pow(0.5 + 0.5 * waveB, 2.0));
  float dustLane = pow(0.5 + 0.5 * sin(longitude * 4.0 + latitude * 7.0 + sin(longitude * 2.0) * 2.0), 3.0);
  float galaxy = clamp(band * cloud * (1.0 - dustLane * (0.55 + 0.35 * varianceB)), 0.0, 1.0);

  vec3 paletteHue = mix(
    mix(uAccentPrimary, uAccentSecondary, varianceA),
    mix(uAccentSecondary, uAccentHighlight, varianceC),
    0.5 + 0.5 * sin(longitude + latitude * 2.0 - time * 0.2)
  );
  vec3 greyHue = vec3(dot(paletteHue, vec3(0.299, 0.587, 0.114)));
  paletteHue = clamp(greyHue + (paletteHue - greyHue) * 1.45, 0.0, 1.0);
  vec3 dustColor = mix(vec3(0.72, 0.78, 0.92), paletteHue, 0.45 + 0.3 * varianceA + 0.45 * nebulaType);
  vec3 color = dustColor * galaxy * (0.6 + 0.9 * nebulaType);

  float shear = sin(longitude * 13.0 + latitude * 4.0 - time * 0.35)
    * sin(longitude * 5.0 + time * 0.2);
  color += dustColor * band * cloud * max(shear, 0.0) * 0.14;

  float secondPlane = latitude - (0.35 + 0.25 * varianceB) * sin(longitude * 2.0 - 1.1) + 0.4;
  float secondArm = exp(-secondPlane * secondPlane * 7.0) * cloud;
  color += mix(dustColor, uAccentSecondary, 0.35) * secondArm * 0.2;

  vec3 ambientColor = mix(
    vec3(0.04, 0.03, 0.1),
    mix(uAccentPrimary, mix(uAccentSecondary, uAccentHighlight, varianceC), varianceA) * 0.22,
    0.75
  );
  color += ambientColor * (0.5 + 0.22 * sin(time * 0.4 + longitude)) * (0.4 + 0.6 * band);
  color += vec3(1.0, 0.88, 0.68) * pow(band, 4.0) * pow(cloud, 2.0) * 0.4;

  float coreAngle = varianceB * 6.28318;
  vec3 coreDirection = normalize(vec3(cos(coreAngle) * 0.85, 0.6 * (varianceC - 0.5), sin(coreAngle) * 0.85));
  float bulge = max(dot(direction, coreDirection), 0.0);
  color += mix(vec3(1.0, 0.85, 0.6), uAccentHighlight, 0.25)
    * (pow(bulge, 14.0) * 1.6 + pow(bulge, 4.0) * 0.5) * coreType;

  float pocketA = pow(cloud, 5.0) * band * (0.7 + 0.3 * sin(time * 0.6 + longitude * 3.0));
  color += mix(uAccentHighlight, uAccentPrimary, fract(varianceA + 0.5 * sin(longitude * 2.0) + 0.5))
    * pocketA * (0.5 + 0.4 * varianceB + 0.8 * nebulaType);
  float pocketB = pow(0.5 + 0.5 * sin(longitude * 3.0 + latitude * 4.0 - time * 0.18 + 2.0), 6.0) * band;
  color += mix(uAccentSecondary, uAccentHighlight, varianceC) * pocketB * (0.25 + 0.3 * varianceA + 0.5 * nebulaType);

  float detail = smoothstep(90.0, 200.0, uResolution.y) * uDetail;
  vec2 grainGrid = vec2(longitude, latitude) * 34.0;
  vec2 grainCell = floor(grainGrid);
  vec2 grainLocal = fract(grainGrid);
  float grainHash = scalarHash(grainCell.x * 3.7 + grainCell.y * 11.3);
  vec2 grainPoint = vec2(
    0.2 + 0.6 * scalarHash(grainHash * 91.0),
    0.2 + 0.6 * scalarHash(grainHash * 47.0)
  );
  float grainDistance = length((grainLocal - grainPoint) * vec2(cos(latitude), 1.0));
  float resolutionFactor = clamp(uResolution.y / 420.0, 0.22, 1.0);
  float grain = exp(-grainDistance * grainDistance * 700.0 * resolutionFactor)
    * step(0.3, grainHash) * (0.15 + 0.85 * band);
  color += vec3(0.88, 0.9, 1.0) * grain * 0.4 * detail;
  float coverage = clamp(galaxy * 0.7 + pow(band, 4.0) * 0.25, 0.0, 1.0);

  for (int scaleIndex = 0; scaleIndex < 3; scaleIndex++) {
    float scale = scaleIndex == 0 ? 6.0 : (scaleIndex == 1 ? 11.0 : 19.0);
    vec2 grid = vec2(longitude, latitude) * scale;
    vec2 cell = floor(grid);
    vec2 local = fract(grid);
    float hashX = scalarHash(cell.x * 13.7 + cell.y * 7.3 + float(scaleIndex) * 91.0);
    float hashY = scalarHash(cell.x * 5.1 + cell.y * 17.9 + float(scaleIndex) * 37.0);
    vec2 starPoint = vec2(0.15 + 0.7 * hashX, 0.15 + 0.7 * hashY);
    float distanceToStar = length((local - starPoint) * vec2(cos(latitude), 1.0));
    float census = (varianceB - 0.5) * 0.2 + 0.35 * nebulaType - 0.2 * coreType + 0.3 * deepType;
    float threshold = scaleIndex == 2 ? 0.3 : 0.55;
    float keep = step(threshold + census, scalarHash(hashX * 89.0 + hashY * 31.0) + band * 0.25);
    float twinkle = mix(
      0.92,
      0.6 + 0.4 * sin(time * (1.5 + 3.0 * hashX) + hashX * 40.0),
      resolutionFactor
    );
    float sizeHash = scalarHash(hashX * 53.0 + hashY * 71.0 + cell.x);
    float magnitude = 0.35 + 1.8 * sizeHash * sizeHash;
    float sharpness = (scaleIndex == 0 ? 260.0 : (scaleIndex == 1 ? 700.0 : 1600.0))
      / magnitude * resolutionFactor;
    float star = exp(-distanceToStar * distanceToStar * sharpness) * keep * twinkle;
    vec3 temperature = hashX < 0.33
      ? vec3(0.85, 0.9, 1.0)
      : (hashX < 0.66 ? vec3(1.0, 0.95, 0.85) : mix(vec3(1.0), uAccentSecondary, 0.3));
    vec3 tint = mix(vec3(1.0), temperature, 0.6);
    float brightness = (scaleIndex == 0 ? 1.7 : (scaleIndex == 1 ? 0.9 : 0.5))
      * (0.55 + 0.7 * magnitude);
    float scaleFade = mix(scaleIndex == 2 ? 0.14 : 0.45, 1.0, detail);
    color += tint * star * brightness * scaleFade;

    if (scaleIndex == 0) {
      float largeStar = smoothstep(1.2, 2.0, magnitude);
      color += tint * exp(-distanceToStar * distanceToStar * 60.0) * 0.18 * largeStar * twinkle * scaleFade;
      vec2 offset = (local - starPoint) * vec2(cos(latitude), 1.0);
      float spike = exp(-offset.x * offset.x * 1200.0) * exp(-offset.y * offset.y * 26.0)
        + exp(-offset.y * offset.y * 1200.0) * exp(-offset.x * offset.x * 26.0);
      color += tint * spike * 0.3 * largeStar * twinkle * scaleFade;
      coverage = max(coverage, spike * 0.3 * largeStar * scaleFade);
    }
    coverage = max(coverage, star * min(brightness, 1.5) * scaleFade);
  }

  float pulsarAngle = varianceA * 6.28318;
  vec3 pulsarDirection = normalize(vec3(
    sin(pulsarAngle) * 0.9,
    1.4 * (varianceB - 0.5),
    cos(pulsarAngle) * 0.9
  ));
  float pulsarAlignment = max(dot(direction, pulsarDirection), 0.0);
  float pulse = pow(0.5 + 0.5 * sin(time * (1.2 + varianceC + 1.5 * uAudioPulse) + varianceC * 6.28), 8.0);
  pulse = min(1.0, pulse + 0.6 * uAudioPulse);
  float pulsarFade = mix(0.45, 1.0, detail);
  color += vec3(0.9, 0.95, 1.0)
    * (pow(pulsarAlignment, 900.0) * (0.6 + 1.2 * pulse) + pow(pulsarAlignment, 110.0) * 0.5 * pulse)
    * pulsarFade;
  coverage = max(coverage, pow(pulsarAlignment, 900.0) * (0.5 + 0.5 * pulse) * pulsarFade);
  return vec4(min(color, vec3(1.0)), min(coverage, 1.0));
}

vec4 sampleRotatedSphere(vec3 direction, float spin, float time) {
  float roll = time * 0.13;
  float rollCos = cos(roll);
  float rollSin = sin(roll);
  direction = vec3(
    rollCos * direction.x - rollSin * direction.y,
    rollSin * direction.x + rollCos * direction.y,
    direction.z
  );
  float tilt = 0.45 + 0.35 * sin(time * 0.24);
  float tiltCos = cos(tilt);
  float tiltSin = sin(tilt);
  direction = vec3(
    direction.x,
    tiltCos * direction.y - tiltSin * direction.z,
    tiltSin * direction.y + tiltCos * direction.z
  );
  float spinCos = cos(spin);
  float spinSin = sin(spin);
  direction = vec3(
    spinCos * direction.x + spinSin * direction.z,
    direction.y,
    -spinSin * direction.x + spinCos * direction.z
  );
  return sampleSky(direction, time);
}

vec3 shadeOrb(vec2 point) {
  float radius = length(point);
  float clampedRadius = min(radius, 0.9995);
  float depth = sqrt(max(0.0, 1.0 - clampedRadius * clampedRadius));
  vec3 normal = vec3(point.x, point.y, depth);
  float rim = pow(1.0 - depth, 2.4);

  vec3 refracted = refract(vec3(0.0, 0.0, -1.0), normal, 0.75);
  float backDistance = -2.0 * dot(normal, refracted);
  vec3 backDirection = normalize(normal + refracted * backDistance);

  float time = uTime * 0.8 + uSeed;
  float varianceA = fract(uSeed * 6.31);
  float varianceB = fract(uSeed * 2.17);
  float warpedTime = time
    + (0.9 + 1.3 * varianceA) * sin(time * (0.09 + 0.07 * varianceB))
    + (0.5 + 0.8 * varianceB) * sin(time * (0.21 + 0.09 * varianceA) + 2.6);
  vec4 front = sampleRotatedSphere(normal, uSpin, warpedTime);
  vec4 back = sampleRotatedSphere(backDirection, uSpin, warpedTime * 0.8 + 2.7);

  vec3 voidColor = mix(uBaseColor * 0.04, uBaseColor * 0.35, rim);
  vec3 color = mix(uInteriorColor, voidColor, 0.97 - 0.04 * rim);
  float frontAlpha = clamp(front.a, 0.0, 1.0);
  float backAlpha = clamp(back.a, 0.0, 1.0);
  color = mix(color, back.rgb, backAlpha * 0.16);
  color = mix(color, front.rgb, frontAlpha * 0.85);

  float auroraLongitude = atan(normal.x, normal.z);
  float speechWave = pow(
    0.5 + 0.5 * sin(auroraLongitude * 3.0 + sin(auroraLongitude * 7.0 + time * 1.1) * 0.7 + time * 0.5),
    3.0
  ) * (0.55 + 0.45 * sin(auroraLongitude * 5.0 - time * 0.65 + 1.7));
  float visibleSky = -normal.y;
  float hangingMask = smoothstep(-0.15, 0.5, visibleSky);
  float rayPattern = 0.7 + 0.3 * sin(
    auroraLongitude * 24.0 + sin(auroraLongitude * 9.0 - time * 0.8) * 2.0 + time * 1.6
  );
  float aurora = clamp(speechWave, 0.0, 1.0) * hangingMask * rayPattern * (1.0 + 2.2 * uAudioPulse);
  float auroraVariance = fract(uSeed * 2.93);
  vec3 auroraColor = mix(
    vec3(0.12, 0.95, 0.55),
    vec3(0.45, 0.35, 1.0),
    smoothstep(0.0, 0.95, visibleSky + 0.35 * speechWave)
  );
  auroraColor = mix(auroraColor, mix(uAccentPrimary, uAccentHighlight, auroraVariance), 0.15 + 0.4 * auroraVariance);
  color += auroraColor * aurora * 0.8;

  float meteorPeriod = 4.5 + 3.5 * fract(uSeed * 4.91);
  float meteorEpoch = floor(time / meteorPeriod);
  float meteorPhase = fract(time / meteorPeriod);
  vec2 meteorStart = vec2(
    -1.1 + 2.2 * scalarHash(meteorEpoch * 1.3),
    0.85 - 1.4 * scalarHash(meteorEpoch * 2.9)
  );
  vec2 meteorDirection = normalize(vec2(
    0.7 + 0.5 * scalarHash(meteorEpoch * 4.1),
    -0.35 - 0.4 * scalarHash(meteorEpoch * 5.3)
  ));
  vec2 meteorHead = meteorStart + meteorDirection * meteorPhase * 2.8;
  vec2 meteorRelative = point - meteorHead;
  float meteorAlong = dot(meteorRelative, meteorDirection);
  float meteorPerpendicular = dot(meteorRelative, vec2(-meteorDirection.y, meteorDirection.x));
  float meteorVisible = smoothstep(0.0, 0.06, meteorPhase) * smoothstep(0.5, 0.32, meteorPhase);
  float meteorTail = exp(-meteorPerpendicular * meteorPerpendicular * 1600.0)
    * exp(meteorAlong * 9.0) * step(meteorAlong, 0.0) * smoothstep(-0.5, -0.02, meteorAlong);
  float meteorGlow = exp(-dot(meteorRelative, meteorRelative) * 900.0);
  color += (vec3(1.0) * meteorGlow * 1.2 + mix(vec3(1.0), uAccentSecondary, 0.3) * meteorTail * 0.85)
    * meteorVisible;

  vec3 movingLight = normalize(vec3(
    0.85 * sin(time * 0.42),
    0.45 * sin(time * 0.26 + 1.2),
    0.5
  ));
  float diffuse = (0.62 + 0.65 * max(dot(normal, movingLight), 0.0)) * (1.0 + 0.35 * uAudioBrightness);
  color *= diffuse;
  vec3 voiceColor = mix(uAccentSecondary, vec3(1.0, 0.97, 0.9), 0.45);
  color += voiceColor * pow(1.0 - clampedRadius, 1.8) * uAudioBrightness * 0.5;
  color += (uAccentSecondary * 0.7 + vec3(0.12)) * rim * uAudioBrightness * 0.65 * uGlow;
  color += color * uAudioBrightness * 0.18 * sin(time * 14.0 + clampedRadius * 40.0 + uSeed * 7.0);
  float counterLight = max(dot(normal.xy, -movingLight.xy), 0.0) * rim;
  color += mix(uAccentPrimary, vec3(0.5, 0.6, 0.9), 0.5) * counterLight * 0.18 * uGlow;

  vec3 keyDirection = normalize(vec3(
    -0.45 + 0.3 * sin(time * 0.34),
    0.62 + 0.2 * sin(time * 0.27 + 1.7),
    0.64
  ));
  float keyStrength = 0.5 * (0.78 + 0.22 * sin(time * 0.45 + 2.2));
  color += vec3(1.0) * pow(max(dot(normal, keyDirection), 0.0), 150.0) * keyStrength * uGlow;
  vec3 sheenDirection = normalize(vec3(sin(time * 0.07) * 0.9, 0.35 + 0.3 * cos(time * 0.05), 0.7));
  color += vec3(1.0) * pow(max(dot(normal, sheenDirection), 0.0), 7.0) * 0.05 * uGlow;
  vec3 glintDirection = normalize(vec3(0.52, -0.5 + 0.12 * sin(time * 0.09), 0.69));
  color += vec3(1.0) * pow(max(dot(normal, glintDirection), 0.0), 140.0) * 0.25 * uGlow;
  color = mix(color, front.rgb, frontAlpha * rim * 0.3);
  float listeningState = step(0.5, uState) * (1.0 - step(1.5, uState));
  float thinkingState = step(1.5, uState) * (1.0 - step(2.5, uState));
  float successState = step(3.5, uState) * (1.0 - step(4.5, uState));
  float errorState = step(4.5, uState);
  float statePulse = 0.5 + 0.5 * sin(time * (1.1 + thinkingState * 0.7));
  float stateStrength = uStateBlend * (
    listeningState * 0.12
    + thinkingState * (0.08 + 0.1 * statePulse)
    + successState * 0.22
    + errorState * 0.18
  );
  vec3 stateColor = mix(uAccentPrimary, uAccentSecondary, thinkingState * statePulse);
  stateColor = mix(stateColor, uAccentHighlight, successState);
  stateColor = mix(stateColor, vec3(1.0, 0.08, 0.05), errorState);
  color += stateColor * (0.15 + 0.85 * rim) * stateStrength * uGlow;
  color *= uVisualIntensity;
  float limb = smoothstep(0.94, 1.0, clampedRadius);
  return mix(color, color * 0.85, limb * 0.4);
}

void main() {
  vec2 point = vUv * 2.0 - 1.0;
  if (length(point) > 1.0) discard;
  if (uGlass > 0.0) {
    float radius = length(point);
    float exponential = exp(2.0 * 1.7724539 * (radius - 0.9) / 0.1414214);
    float edgeFalloff = 0.5 + 0.5 * (exponential - 1.0) / (exponential + 1.0);
    if (edgeFalloff > 0.004) {
      float lensPulse = 1.0 + 0.16 * (
        0.6 * sin(uTime * 0.9 + uSeed)
        + 0.4 * sin(uTime * 1.7 + uSeed * 1.3)
      );
      float displacement = uGlass * edgeFalloff * lensPulse;
      float redShift = 1.4 * (1.0 + 0.06 * sin(uTime * 1.3 + uSeed));
      float greenShift = 1.2 * (1.0 + 0.06 * sin(uTime * 1.3 + uSeed + 2.1));
      float blueShift = 1.0 * (1.0 + 0.06 * sin(uTime * 1.3 + uSeed + 4.2));
      vec3 color = vec3(
        shadeOrb(point * (1.0 - displacement * redShift)).r,
        shadeOrb(point * (1.0 - displacement * greenShift)).g,
        shadeOrb(point * (1.0 - displacement * blueShift)).b
      );
      vec2 absolutePoint = min(abs(point), 1.0);
      float lobe = max(
        abs(absolutePoint.x * 0.766 + absolutePoint.y * 0.643),
        abs(absolutePoint.x * 0.766 - absolutePoint.y * 0.643)
      );
      float glow = 0.65 * pow(clamp((lobe - 0.0707) / 1.3435, 0.0, 1.0), 2.4) * edgeFalloff;
      glow += 1.02 * clamp(1.0 + (radius - 1.0) / 0.15, 0.0, 1.0)
        * step(radius, 1.0) * pow(lobe, 2.0);
      color += vec3(0.25) * min(glow, 1.0) * uGlow;
      gl_FragColor = vec4(color, 1.0);
      return;
    }
  }
  gl_FragColor = vec4(shadeOrb(point), 1.0);
}
";
    }

    // ======================================================================
    // Baked variant: core-teal-01 (phase 4.6, archetype core = 2)
    // ======================================================================
    internal static class Variant
    {
        public const float Phase = 4.6f;
        public const float Archetype = 2.0f;
        public const float Glass = 0.4f;
        public static readonly float[] Base = Hex("#07262B");
        public static readonly float[] Acc0 = Hex("#00C2A8");
        public static readonly float[] Acc1 = Hex("#38E1FF");
        public static readonly float[] Acc2 = Hex("#FFC65C");

        private static float[] Hex(string h)
        {
            int n = Convert.ToInt32(h.Substring(1), 16);
            return new float[] { ((n >> 16) & 255) / 255f, ((n >> 8) & 255) / 255f, (n & 255) / 255f };
        }
    }

    // ======================================================================
    // Motion state — port of motion.js (advanceOrbMotion)
    // ======================================================================
    internal class Motion
    {
        public float Phase;
        public float AudioSmooth;
        public float AudioFast;
        public int SpinDir = 1;
        public float SpinVel;
        public float PrevA;
        public bool FlipQueued;
        public int OscSign = 1;
        public float Spin;
        public double LastT;
        public bool HasLastT;

        public Motion(float phase)
        {
            Phase = phase;
            Spin = phase * 3.7f;
        }

        public void Advance(double time, float audioLevel)
        {
            double delta = HasLastT ? Math.Min(0.1, Math.Max(0.0, time - LastT)) : 0.0;
            LastT = time; HasLastT = true;
            float input = Math.Min(1f, Math.Max(0f, audioLevel));
            float sc = input > AudioSmooth ? 0.11f : 0.3f;
            if (delta > 0) AudioSmooth += (input - AudioSmooth) * (1 - (float)Math.Exp(-delta / sc));
            float fc = input > AudioFast ? 0.04f : 0.18f;
            if (delta > 0) AudioFast += (input - AudioFast) * (1 - (float)Math.Exp(-delta / fc));

            float pv = (6.31f * Phase) % 1f;
            float drift = 1f * 0.35f * (float)Math.Sin(time * (0.11 + 0.08 * ((2.17 * Phase) % 1)) + Phase);
            float osc = (float)Math.Sin(time * (0.45 + 0.2 * pv) + Phase);
            int s = Math.Sign(osc);
            if (s != OscSign) { OscSign = s; FlipQueued = true; }
            if (FlipQueued && AudioFast < 0.18f) { SpinDir = -SpinDir; FlipQueued = false; }

            float target = 1f * (0.65f * (0.65f + 0.7f * pv) * (1 + drift) + SpinDir * AudioFast * 2.2f);
            if (delta > 0) SpinVel += (target - SpinVel) * (1 - (float)Math.Exp(-delta / 0.35));
            float attack = Math.Max(0f, AudioFast - PrevA);
            PrevA = AudioFast;
            SpinVel += SpinDir * Math.Min(6 * attack, 1.4f) * (float)delta * 14f * 1f;
            Spin += SpinVel * (float)delta;
        }
    }

    // ======================================================================
    // settings.json helpers (regex-based, no extra assemblies)
    // ======================================================================
    internal static class Settings
    {
        public static string FilePath()
        {
            // The exe lives in bloomcs/, but settings.json lives next to the app.
            string here = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "settings.json");
            if (File.Exists(here)) return here;
            try
            {
                string parent = Path.Combine(Directory.GetParent(AppDomain.CurrentDomain.BaseDirectory).FullName, "settings.json");
                if (File.Exists(parent)) return parent;
            }
            catch { }
            return here;
        }

        private static int? ReadInt(string key)
        {
            try
            {
                string text = File.ReadAllText(FilePath());
                Match m = Regex.Match(text, "\"" + key + "\"\\s*:\\s*(-?\\d+)");
                if (m.Success) return int.Parse(m.Groups[1].Value);
            }
            catch { }
            return null;
        }

        public static int? PillX() { return ReadInt("pill_x"); }
        public static int? PillY() { return ReadInt("pill_y"); }

        public static void SavePos(int x, int y)
        {
            try
            {
                string path = FilePath();
                string text = File.Exists(path) ? File.ReadAllText(path) : "{}";
                text = Regex.Replace(text, "\"pill_x\"\\s*:\\s*(-?\\d+|null)", "\"pill_x\": " + x);
                text = Regex.Replace(text, "\"pill_y\"\\s*:\\s*(-?\\d+|null)", "\"pill_y\": " + y);
                if (!text.Contains("\"pill_x\""))
                    text = text.TrimEnd().TrimEnd('}').TrimEnd() + ",\n  \"pill_x\": " + x + ",\n  \"pill_y\": " + y + "\n}";
                File.WriteAllText(path, text);
            }
            catch { }
        }

        public static void ClearPos()
        {
            try
            {
                string path = FilePath();
                if (!File.Exists(path)) return;
                string text = File.ReadAllText(path);
                text = Regex.Replace(text, "\"pill_x\"\\s*:\\s*(-?\\d+|null)", "\"pill_x\": null");
                text = Regex.Replace(text, "\"pill_y\"\\s*:\\s*(-?\\d+|null)", "\"pill_y\": null");
                File.WriteAllText(path, text);
            }
            catch { }
        }
    }

    internal static class Log
    {
        public static void Write(string msg)
        {
            try
            {
                File.AppendAllText(
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "bloomcs.log"),
                    "[" + DateTime.Now.ToString("HH:mm:ss") + "] " + msg + "\r\n");
            }
            catch { }
        }
    }

    // ======================================================================
    // Layered overlay window (per-pixel alpha, no taskbar, no focus steal)
    // ======================================================================
    internal class OrbForm : Form
    {
        public const int WIN = 72;
        public const int RENDER = 144;

        private bool dragging;
        private Point dragStart;

        public OrbForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            TopMost = true;
            StartPosition = FormStartPosition.Manual;
            Size = new Size(WIN, WIN);
            BackColor = Color.Magenta;
            PlaceInitial();
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= Native.WS_EX_LAYERED | Native.WS_EX_TOOLWINDOW
                    | Native.WS_EX_NOACTIVATE | Native.WS_EX_TOPMOST;
                cp.ExStyle &= ~Native.WS_EX_APPWINDOW;
                return cp;
            }
        }

        public void PlaceInitial()
        {
            Native.RECT r = new Native.RECT();
            Native.SystemParametersInfoW(Native.SPI_GETWORKAREA, 0, ref r, 0);
            int? cx = Settings.PillX();
            int? cy = Settings.PillY();
            int x, y;
            if (cx.HasValue && cy.HasValue)
            {
                x = Math.Max(r.Left + 4, Math.Min(r.Right - WIN - 4, cx.Value));
                y = Math.Max(r.Top + 4, Math.Min(r.Bottom - WIN - 4, cy.Value));
            }
            else
            {
                x = (r.Left + r.Right - WIN) / 2;
                y = r.Bottom - WIN - 24;
            }
            Location = new Point(x, y);
        }

        public void ShowNoActivate()
        {
            PlaceInitial();
            if (!Visible) Show();
            EnsureVisible();
            Log.Write("show at (" + Left + "," + Top + ") size " + Width + "x" + Height
                + " workarea " + Screen.FromRectangle(new Rectangle(Left, Top, WIN, WIN)).WorkingArea.ToString());
            Native.ShowWindow(Handle, Native.SW_SHOWNOACTIVATE);
            Native.SetWindowPos(Handle, Native.HWND_TOPMOST, 0, 0, 0, 0,
                Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE | Native.SWP_SHOWWINDOW);
        }

        // Self-healing placement: whatever the saved position says, the full
        // orb must end up inside a real monitor's work area. Uses the monitor
        // nearest to the desired spot (multi-monitor safe), not just primary.
        public void EnsureVisible()
        {
            Rectangle want = new Rectangle(Left, Top, WIN, WIN);
            Screen scr;
            try { scr = Screen.FromRectangle(want); }
            catch { scr = Screen.PrimaryScreen; }
            Rectangle wa = scr.WorkingArea;
            int x = Left, y = Top;
            if (want.Right > wa.Right) x = wa.Right - WIN;
            if (want.Bottom > wa.Bottom) y = wa.Bottom - WIN;
            if (x < wa.Left) x = wa.Left;
            if (y < wa.Top) y = wa.Top;
            if (x != Left || y != Top)
            {
                Location = new Point(x, y);
                try { Settings.SavePos(x, y); } catch { }
                Log.Write("clamped into " + wa.ToString() + " -> (" + x + "," + y + ")");
            }
            // Paranoia: if it STILL doesn't fit anywhere, park bottom-center.
            Rectangle now = new Rectangle(x, y, WIN, WIN);
            if (!wa.Contains(now))
            {
                Rectangle pwa = Screen.PrimaryScreen.WorkingArea;
                Location = new Point((pwa.Left + pwa.Right - WIN) / 2, pwa.Bottom - WIN - 24);
                try { Settings.SavePos(Left, Top); } catch { }
                Log.Write("reset to primary bottom-center");
            }
        }

        public new void Hide()
        {
            Native.ShowWindow(Handle, Native.SW_HIDE);
            base.Hide();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Native.WM_LBUTTONDOWN)
            {
                // Manual capture-based drag (reliable on layered windows,
                // unlike the modal system-move loop).
                try
                {
                    dragStart = new Point(Control.MousePosition.X - Left, Control.MousePosition.Y - Top);
                    dragging = true;
                    Capture = true;
                    Log.Write("drag-start (" + Left + "," + Top + ")");
                }
                catch { }
                return;
            }
            if (m.Msg == 0x0200 && dragging) // WM_MOUSEMOVE
            {
                try
                {
                    Left = Control.MousePosition.X - dragStart.X;
                    Top = Control.MousePosition.Y - dragStart.Y;
                }
                catch { }
                return;
            }
            if (m.Msg == Native.WM_LBUTTONUP && dragging)
            {
                dragging = false;
                try { Capture = false; } catch { }
                try
                {
                    EnsureVisible();
                    Settings.SavePos(Left, Top);
                    Log.Write("drag-end (" + Left + "," + Top + ")");
                }
                catch { }
                return;
            }
            if (m.Msg == Native.WM_LBUTTONDBLCLK)
            {
                Settings.ClearPos();
                PlaceInitial();
                EnsureVisible();
                Log.Write("drag-reset (" + Left + "," + Top + ")");
                return;
            }
            base.WndProc(ref m);
        }

        // Pushes a premultiplied RGBA buffer (RENDER x RENDER) to the screen.
        public void Present(byte[] rgba)
        {
            int n = RENDER;
            Bitmap bmp = new Bitmap(n, n, PixelFormat.Format32bppPArgb);
            BitmapData data = bmp.LockBits(new Rectangle(0, 0, n, n),
                ImageLockMode.WriteOnly, PixelFormat.Format32bppPArgb);
            // glReadPixels is bottom-up; bitmap is top-down: flip rows.
            // GL gives RGBA, GDI+ PArgb wants BGRA.
            for (int y = 0; y < n; y++)
            {
                int src = (n - 1 - y) * n * 4;
                IntPtr dst = new IntPtr(data.Scan0.ToInt64() + (long)y * data.Stride);
                byte[] row = new byte[n * 4];
                for (int x = 0; x < n; x++)
                {
                    row[x * 4 + 0] = rgba[src + x * 4 + 2];
                    row[x * 4 + 1] = rgba[src + x * 4 + 1];
                    row[x * 4 + 2] = rgba[src + x * 4 + 0];
                    row[x * 4 + 3] = rgba[src + x * 4 + 3];
                }
                Marshal.Copy(row, 0, dst, row.Length);
            }
            bmp.UnlockBits(data);

            IntPtr screenDc = Native.GetDC(IntPtr.Zero);
            IntPtr memDc = Native.CreateCompatibleDC(screenDc);
            IntPtr hBmp = bmp.GetHbitmap(Color.FromArgb(0));
            IntPtr old = Native.SelectObject(memDc, hBmp);
            try
            {
                Native.RECT wr;
                Native.GetWindowRect(Handle, out wr);
                Point dst = new Point(wr.Left, wr.Top);
                Size size = new Size(WIN, WIN);
                Point src = new Point(0, 0);
                Native.BLENDFUNCTION blend = new Native.BLENDFUNCTION();
                blend.BlendOp = Native.AC_SRC_OVER;
                blend.SourceConstantAlpha = 255;
                blend.AlphaFormat = Native.AC_SRC_ALPHA;
                Native.UpdateLayeredWindow(Handle, screenDc, ref dst, ref size,
                    memDc, ref src, 0, ref blend, Native.ULW_ALPHA);
            }
            finally
            {
                Native.SelectObject(memDc, old);
                Native.DeleteObject(hBmp);
                Native.DeleteDC(memDc);
                Native.ReleaseDC(IntPtr.Zero, screenDc);
                bmp.Dispose();
            }
        }
    }

    // ======================================================================
    // Renderer: hidden GL window + program + per-frame uniforms
    // ======================================================================
    internal class OrbRenderer : IDisposable
    {
        private Form glForm;
        private IntPtr hdc = IntPtr.Zero;
        private IntPtr hgl = IntPtr.Zero;
        private uint prog;
        private int aPos, aTex;
        private int uResolution, uInteriorColor, uBaseColor, uAccentPrimary,
            uAccentSecondary, uAccentHighlight, uTime, uSeed, uAudioBrightness,
            uAudioPulse, uSpin, uArchetype, uGlass, uVisualIntensity, uDetail,
            uGlow, uState, uStateBlend;

        public string LastError = "";
        public bool Ready = false;

        public bool Init()
        {
            try
            {
                glForm = new Form();
                glForm.Size = new Size(OrbForm.RENDER, OrbForm.RENDER);
                IntPtr hwnd = glForm.Handle; // force handle creation (never shown)
                hdc = Native.GetDC(hwnd);

                Native.PIXELFORMATDESCRIPTOR pfd = new Native.PIXELFORMATDESCRIPTOR();
                pfd.nSize = (short)Marshal.SizeOf(typeof(Native.PIXELFORMATDESCRIPTOR));
                pfd.nVersion = 1;
                pfd.dwFlags = Native.PFD_DRAW_TO_WINDOW | Native.PFD_SUPPORT_OPENGL | Native.PFD_DOUBLEBUFFER;
                pfd.iPixelType = Native.PFD_TYPE_RGBA;
                pfd.cColorBits = 32;
                pfd.cAlphaBits = 8;
                pfd.cDepthBits = 24;
                pfd.iLayerType = Native.PFD_MAIN_PLANE;
                int pf = Native.ChoosePixelFormat(hdc, ref pfd);
                if (pf == 0) { LastError = "ChoosePixelFormat failed"; return false; }
                if (!Native.SetPixelFormat(hdc, pf, ref pfd)) { LastError = "SetPixelFormat failed"; return false; }
                hgl = Native.wglCreateContext(hdc);
                if (hgl == IntPtr.Zero) { LastError = "wglCreateContext failed"; return false; }
                if (!Native.wglMakeCurrent(hdc, hgl)) { LastError = "wglMakeCurrent failed"; return false; }
                if (!GL.LoadAll()) { LastError = "GL extension load failed (no shader-capable driver?)"; return false; }

                uint vs = Compile(GL.VERTEX_SHADER, Shaders.Vert);
                if (vs == 0) return false;
                uint fs = Compile(GL.FRAGMENT_SHADER, Shaders.Frag);
                if (fs == 0) return false;
                prog = GL.CreateProgram();
                GL.AttachShader(prog, vs);
                GL.AttachShader(prog, fs);
                GL.LinkProgram(prog);
                GL.DeleteShader(vs);
                GL.DeleteShader(fs);
                int linked;
                GL.GetProgramiv(prog, GL.LINK_STATUS, out linked);
                if (linked == 0)
                {
                    StringBuilder sb = new StringBuilder(2048);
                    int len;
                    GL.GetProgramInfoLog(prog, 2048, out len, sb);
                    LastError = "link: " + sb.ToString();
                    return false;
                }

                aPos = GL.GetAttribLocation(prog, "aPosition");
                aTex = GL.GetAttribLocation(prog, "aTextureCoord");
                uResolution = GL.GetUniformLocation(prog, "uResolution");
                uInteriorColor = GL.GetUniformLocation(prog, "uInteriorColor");
                uBaseColor = GL.GetUniformLocation(prog, "uBaseColor");
                uAccentPrimary = GL.GetUniformLocation(prog, "uAccentPrimary");
                uAccentSecondary = GL.GetUniformLocation(prog, "uAccentSecondary");
                uAccentHighlight = GL.GetUniformLocation(prog, "uAccentHighlight");
                uTime = GL.GetUniformLocation(prog, "uTime");
                uSeed = GL.GetUniformLocation(prog, "uSeed");
                uAudioBrightness = GL.GetUniformLocation(prog, "uAudioBrightness");
                uAudioPulse = GL.GetUniformLocation(prog, "uAudioPulse");
                uSpin = GL.GetUniformLocation(prog, "uSpin");
                uArchetype = GL.GetUniformLocation(prog, "uArchetype");
                uGlass = GL.GetUniformLocation(prog, "uGlass");
                uVisualIntensity = GL.GetUniformLocation(prog, "uVisualIntensity");
                uDetail = GL.GetUniformLocation(prog, "uDetail");
                uGlow = GL.GetUniformLocation(prog, "uGlow");
                uState = GL.GetUniformLocation(prog, "uState");
                uStateBlend = GL.GetUniformLocation(prog, "uStateBlend");

                uint vbo;
                GL.GenBuffers(1, out vbo);
                GL.BindBuffer(GL.ARRAY_BUFFER, vbo);
                float[] quad = new float[] { -1, -1, 0, 1, 1, -1, 1, 1, -1, 1, 0, 0, 1, 1, 1, 0 };
                GL.BufferData(GL.ARRAY_BUFFER, new IntPtr(quad.Length * 4), quad, GL.STATIC_DRAW);
                GL.EnableVertexAttribArray(aPos);
                GL.VertexAttribPointer(aPos, 2, GL.FLOAT, false, 16, IntPtr.Zero);
                GL.EnableVertexAttribArray(aTex);
                GL.VertexAttribPointer(aTex, 2, GL.FLOAT, false, 16, new IntPtr(8));

                GL.Disable(0x0B71); // DEPTH_TEST
                GL.Enable(GL.BLEND);
                GL.BlendFunc(GL.ONE, GL.ONE_MINUS_SRC_ALPHA);
                GL.UseProgram(prog);
                GL.Uniform3f(uInteriorColor, 0f, 0f, 0f);
                GL.Uniform3f(uBaseColor, Variant.Base[0], Variant.Base[1], Variant.Base[2]);
                GL.Uniform3f(uAccentPrimary, Variant.Acc0[0], Variant.Acc0[1], Variant.Acc0[2]);
                GL.Uniform3f(uAccentSecondary, Variant.Acc1[0], Variant.Acc1[1], Variant.Acc1[2]);
                GL.Uniform3f(uAccentHighlight, Variant.Acc2[0], Variant.Acc2[1], Variant.Acc2[2]);
                GL.Uniform1f(uSeed, Variant.Phase);
                GL.Uniform1f(uArchetype, Variant.Archetype);
                GL.Uniform1f(uGlass, Variant.Glass);
                GL.Uniform1f(uVisualIntensity, 1f);
                GL.Uniform1f(uDetail, 1f);
                GL.Uniform1f(uGlow, 1f);
                GL.Viewport(0, 0, OrbForm.RENDER, OrbForm.RENDER);
                // Release from the init thread; the render thread acquires it.
                Native.wglMakeCurrent(IntPtr.Zero, IntPtr.Zero);
                Ready = true;
                return true;
            }
            catch (Exception ex)
            {
                LastError = "init: " + ex.Message;
                return false;
            }
        }

        private uint Compile(uint type, string src)
        {
            uint sh = GL.CreateShader(type);
            GL.ShaderSource(sh, 1, new string[] { src }, null);
            GL.CompileShader(sh);
            int ok;
            GL.GetShaderiv(sh, GL.COMPILE_STATUS, out ok);
            if (ok == 0)
            {
                StringBuilder sb = new StringBuilder(4096);
                int len;
                GL.GetShaderInfoLog(sh, 4096, out len, sb);
                LastError = "compile: " + sb.ToString();
                return 0;
            }
            return sh;
        }

        public bool MakeCurrent()
        {
            try { return Native.wglMakeCurrent(hdc, hgl); }
            catch { return false; }
        }

        public byte[] Draw(float time, float brightness, float pulse, float spin, int stateIdx, float stateBlend)
        {
            int n = OrbForm.RENDER;
            GL.Viewport(0, 0, n, n);
            GL.ClearColor(0f, 0f, 0f, 0f);
            GL.Clear(GL.COLOR_BUFFER_BIT);
            GL.UseProgram(prog);
            GL.Uniform2f(uResolution, n, n);
            GL.Uniform1f(uTime, time);
            GL.Uniform1f(uAudioBrightness, brightness);
            GL.Uniform1f(uAudioPulse, pulse);
            GL.Uniform1f(uSpin, spin);
            GL.Uniform1f(uState, stateIdx);
            GL.Uniform1f(uStateBlend, stateBlend);
            GL.DrawArrays(GL.TRIANGLE_STRIP, 0, 4);
            Native.SwapBuffers(hdc);
            byte[] px = new byte[n * n * 4];
            GCHandle h = GCHandle.Alloc(px, GCHandleType.Pinned);
            try { GL.ReadPixels(0, 0, n, n, GL.RGBA, GL.UNSIGNED_BYTE, h.AddrOfPinnedObject()); }
            finally { h.Free(); }
            return px;
        }

        public void Dispose()
        {
            try
            {
                if (hgl != IntPtr.Zero) { Native.wglMakeCurrent(IntPtr.Zero, IntPtr.Zero); Native.wglDeleteContext(hgl); hgl = IntPtr.Zero; }
                if (glForm != null && hdc != IntPtr.Zero) { Native.ReleaseDC(glForm.Handle, hdc); hdc = IntPtr.Zero; }
                if (glForm != null) { glForm.Dispose(); glForm = null; }
            }
            catch { }
        }
    }

    // ======================================================================
    // GDI fallback (no shader-capable GL driver): smooth radial orb that
    // breathes with the voice. Guarantees the pill always shows something.
    // ======================================================================
    internal static class GdiFallback
    {
        public static byte[] Draw(float voice, float t)
        {
            int n = OrbForm.RENDER;
            Bitmap bmp = new Bitmap(n, n, PixelFormat.Format32bppPArgb);
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.Clear(Color.FromArgb(0, 0, 0, 0));
                g.SmoothingMode = SmoothingMode.AntiAlias;
                float pulse = 1f + voice * 0.10f + 0.02f * (float)Math.Sin(t * 3.0);
                float d = n * pulse;
                float off = (n - d) / 2f;
                RectangleF rc = new RectangleF(off, off, d, d);
                using (GraphicsPath path = new GraphicsPath())
                {
                    path.AddEllipse(rc);
                    using (PathGradientBrush br = new PathGradientBrush(path))
                    {
                        int hot = Math.Min(255, 200 + (int)(voice * 55));
                        br.CenterColor = Color.FromArgb(255, (hot * 56) / 255, (hot * 225) / 255, 255);
                        br.SurroundColors = new Color[] { Color.FromArgb(255, 4, 26, 30) };
                        br.FocusScales = new PointF(0.35f, 0.35f);
                        g.FillEllipse(br, rc);
                    }
                }
                using (Pen rim = new Pen(Color.FromArgb(120 + (int)(voice * 100), 56, 225, 255), 2f))
                {
                    g.DrawEllipse(rim, rc);
                }
            }
            // Bitmap -> premultiplied RGBA bytes (bottom-up like glReadPixels).
            byte[] px = new byte[n * n * 4];
            BitmapData data = bmp.LockBits(new Rectangle(0, 0, n, n),
                ImageLockMode.ReadOnly, PixelFormat.Format32bppPArgb);
            try
            {
                for (int y = 0; y < n; y++)
                {
                    IntPtr src = new IntPtr(data.Scan0.ToInt64() + (long)y * data.Stride);
                    Marshal.Copy(src, px, (n - 1 - y) * n * 4, n * 4);
                }
            }
            finally { bmp.UnlockBits(data); bmp.Dispose(); }
            // PArgb is BGRA; convert to RGBA.
            for (int i = 0; i < px.Length; i += 4)
            {
                byte tmp = px[i];
                px[i] = px[i + 2];
                px[i + 2] = tmp;
            }
            return px;
        }
    }

    // ======================================================================
    // Program: owns form, renderer, motion, stdin protocol, render loop
    // ======================================================================
    internal static class Program
    {
        private static OrbForm form;
        private static OrbRenderer renderer;
        private static bool useFallback;
        private static Motion motion = new Motion(Variant.Phase);
        private static readonly object stateLock = new object();
        private static volatile bool visible;
        private static volatile bool running = true;
        private static int pillStateIdx = 3; // speaking
        private static float stateBlend = 1f;
        private static double lastStateAt;
        private static bool hasStateAt;
        private static float targetLevel = 0.08f;
        private static float audioSmooth;
        private static float audioFast;
        private static double timeOffset = new Random().NextDouble() * 4000.0;
        private static Stopwatch clock = Stopwatch.StartNew();

        private static int PillToOrbState(string s)
        {
            if (s == "listening") return 3;
            if (s == "transcribing") return 2;
            if (s == "done") return 4;
            if (s == "error") return 5;
            return 3;
        }

        private static void SetState(string s)
        {
            lock (stateLock)
            {
                int idx = PillToOrbState(s);
                if (idx != pillStateIdx) { pillStateIdx = idx; stateBlend = 0f; hasStateAt = false; }
                if (s == "transcribing" && targetLevel < 0.35f) targetLevel = 0.35f;
            }
        }

        private static void StdinLoop()
        {
            string line;
            while (running && (line = Console.In.ReadLine()) != null)
            {
                line = line.Trim();
                if (line.Length == 0) continue;
                string cmd, arg;
                int sp = line.IndexOf(' ');
                if (sp < 0) { cmd = line; arg = ""; }
                else { cmd = line.Substring(0, sp); arg = line.Substring(sp + 1).Trim(); }
                try
                {
                    if (cmd == "show") { SetState(arg.Length > 0 ? arg : "listening"); ShowForm(); }
                    else if (cmd == "state") SetState(arg);
                    else if (cmd == "level")
                    {
                        float f;
                        if (float.TryParse(arg, out f))
                        {
                            lock (stateLock) targetLevel = Math.Min(1f, Math.Max(0f, f));
                        }
                    }
                    else if (cmd == "theme") { }
                    else if (cmd == "hide") HideForm();
                    else if (cmd == "quit") { running = false; try { Application.Exit(); } catch { } break; }
                }
                catch { }
            }
            running = false;
            try { Application.Exit(); } catch { }
        }

        private static void ShowForm()
        {
            try
            {
                if (form.InvokeRequired) form.BeginInvoke(new Action(ShowForm));
                else { visible = true; form.ShowNoActivate(); }
            }
            catch { visible = true; }
        }

        private static void HideForm()
        {
            try
            {
                if (form.InvokeRequired) form.BeginInvoke(new Action(HideForm));
                else { visible = false; form.Hide(); }
            }
            catch { visible = false; }
        }

        private static void RenderLoop()
        {
            if (!useFallback && renderer != null)
            {
                if (!renderer.MakeCurrent())
                    Log.Write("wglMakeCurrent failed on render thread");
            }
            Stopwatch sw = Stopwatch.StartNew();
            long frameMs = 1000 / 45;
            while (running)
            {
                long start = sw.ElapsedMilliseconds;
                if (visible)
                {
                    double now = clock.Elapsed.TotalSeconds;
                    float tlvl, blend;
                    int idx;
                    lock (stateLock)
                    {
                        tlvl = targetLevel;
                        idx = pillStateIdx;
                        double dt = hasStateAt ? Math.Min(0.1, Math.Max(0.0, now - lastStateAt)) : 0.0;
                        lastStateAt = now; hasStateAt = true;
                        if (dt > 0) stateBlend += (1f - stateBlend) * (1f - (float)Math.Exp(-dt / 0.18));
                        blend = stateBlend;
                    }
                    float tc = tlvl > audioSmooth ? 0.11f : 0.30f;
                    audioSmooth += (tlvl - audioSmooth) * (1f - (float)Math.Exp(-(1.0 / 45.0) / tc));
                    float fc = tlvl > audioFast ? 0.04f : 0.18f;
                    audioFast += (tlvl - audioFast) * (1f - (float)Math.Exp(-(1.0 / 45.0) / fc));
                    double t = now + timeOffset;
                    byte[] px;
                    if (!useFallback)
                    {
                        motion.Advance(t, Math.Min(1f, tlvl * 1f));
                        float b = Math.Min(1f, motion.AudioSmooth * 1f);
                        float p = Math.Min(1f, motion.AudioSmooth * 1f);
                        px = renderer.Draw((float)t, b, p, motion.Spin, idx, blend);
                    }
                    else
                    {
                        px = GdiFallback.Draw(Math.Min(1f, audioSmooth), (float)now);
                    }
                    try { form.Present(px); }
                    catch (Exception ex) { Log.Write("present: " + ex.Message); }
                }
                long spent = sw.ElapsedMilliseconds - start;
                int sleep = (int)(frameMs - spent);
                if (sleep > 0) Thread.Sleep(sleep);
            }
        }

        private static int Snapshot(string path, int frames)
        {
            renderer = new OrbRenderer();
            if (!renderer.Init())
            {
                Console.WriteLine("GL init failed: " + renderer.LastError + " — using GDI fallback");
                byte[] px = GdiFallback.Draw(0.6f, 12.0f);
                SavePng(px, path);
                Console.WriteLine("SNAPSHOT-OK fallback");
                return 0;
            }
            Motion m = new Motion(Variant.Phase);
            double t0 = 1234.0;
            byte[] last = null;
            renderer.MakeCurrent();
            for (int i = 0; i < frames; i++)
            {
                double t = t0 + i * (1.0 / 45.0) + timeOffset;
                float lvl = 0.15f + 0.65f * (float)Math.Abs(Math.Sin(i * 0.35));
                m.Advance(t, lvl);
                float b = Math.Min(1f, m.AudioSmooth);
                last = renderer.Draw((float)t, b, b, m.Spin, 3, 1f);
            }
            SavePng(last, path);
            Console.WriteLine("SNAPSHOT-OK gl");
            return 0;
        }

        private static int SelfTest()
        {
            // Exercises the exact production path: GL init on this thread,
            // then MakeCurrent + Draw on a worker thread (like RenderLoop).
            OrbRenderer r = new OrbRenderer();
            if (!r.Init()) { Console.WriteLine("SELFTEST-FAIL gl-init: " + r.LastError); return 1; }
            byte[][] frames = new byte[3][];
            Exception workerErr = null;
            Thread w = new Thread(delegate()
            {
                try
                {
                    if (!r.MakeCurrent()) throw new Exception("MakeCurrent false");
                    Motion m = new Motion(Variant.Phase);
                    for (int i = 0; i < 3; i++)
                    {
                        double t = 500.0 + i * 0.25 + timeOffset;
                        m.Advance(t, 0.6f);
                        frames[i] = r.Draw((float)t, 0.6f, 0.6f, m.Spin, 3, 1f);
                    }
                }
                catch (Exception ex) { workerErr = ex; }
            });
            w.Start();
            if (!w.Join(15000)) { Console.WriteLine("SELFTEST-FAIL worker-timeout"); return 1; }
            r.Dispose();
            if (workerErr != null) { Console.WriteLine("SELFTEST-FAIL worker: " + workerErr.Message); return 1; }
            long alpha = 0, lum = 0;
            for (int i = 0; i < frames[2].Length; i += 4) { alpha += frames[2][i + 3]; lum += frames[2][i] + frames[2][i + 1] + frames[2][i + 2]; }
            // Frames at different times must also differ (animation is alive).
            long diff = 0;
            for (int i = 0; i < frames[0].Length; i++) diff += Math.Abs((int)frames[0][i] - (int)frames[2][i]);
            Console.WriteLine("alpha=" + alpha + " lum=" + lum + " framediff=" + diff);
            if (alpha < 100000 || lum < 100000 || diff < 10000)
            {
                Console.WriteLine("SELFTEST-FAIL dark-or-frozen");
                return 1;
            }
            Console.WriteLine("SELFTEST-OK gl-threaded");
            return 0;
        }

        private static void SavePng(byte[] rgbaBottomUp, string path)
        {
            int n = OrbForm.RENDER;
            Bitmap bmp = new Bitmap(n, n, PixelFormat.Format32bppArgb);
            for (int y = 0; y < n; y++)
            {
                for (int x = 0; x < n; x++)
                {
                    int s = ((n - 1 - y) * n + x) * 4;
                    bmp.SetPixel(x, y, Color.FromArgb(rgbaBottomUp[s + 3], rgbaBottomUp[s], rgbaBottomUp[s + 1], rgbaBottomUp[s + 2]));
                }
            }
            bmp.Save(path, ImageFormat.Png);
            bmp.Dispose();
        }

        [STAThread]
        private static void Main(string[] args)
        {
            try { Native.SetProcessDpiAwareness(2); }
            catch { try { Native.SetProcessDPIAware(); } catch { } }
            try { Native.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App"); } catch { }
            Log.Write("bloomcs starting");

            if (args.Length >= 2 && args[0] == "--snapshot")
            {
                int frames = 24;
                if (args.Length >= 3) { int.TryParse(args[2], out frames); if (frames < 1) frames = 24; }
                Environment.Exit(Snapshot(args[1], frames));
                return;
            }

            if (args.Length >= 1 && args[0] == "--selftest")
            {
                Environment.Exit(SelfTest());
                return;
            }

            Application.EnableVisualStyles();
            form = new OrbForm();

            renderer = new OrbRenderer();
            if (renderer.Init())
            {
                useFallback = false;
                Log.Write("GL renderer ready");
            }
            else
            {
                useFallback = true;
                Log.Write("GL failed (" + renderer.LastError + "), GDI fallback active");
                renderer.Dispose();
                renderer = null;
            }

            Thread stdin = new Thread(StdinLoop);
            stdin.IsBackground = true;
            stdin.Start();

            Thread render = new Thread(RenderLoop);
            render.IsBackground = true;
            render.Start();

            Application.Run();
            running = false;
            if (renderer != null) renderer.Dispose();
        }
    }
}
