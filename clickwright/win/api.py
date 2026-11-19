"""ctypes declarations for the Win32 calls the driver uses.

Everything here is a thin binding: no logic, so the other modules can stay
readable. All coordinates are physical pixels because the process is made
per-monitor DPI aware (see dpi.py) before anything else touches the screen.
"""
import ctypes
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover - dwmapi exists on every supported Windows
    dwmapi = None
try:
    shcore = ctypes.WinDLL("shcore")
except OSError:  # pragma: no cover
    shcore = None

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
HCURSOR = wt.HANDLE


# --- structures -------------------------------------------------------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", HCURSOR),
                ("ptScreenPos", wt.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]


assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

# --- constants ---------------------------------------------------------------
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_ABSOLUTE = 0x4000, 0x8000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
WHEEL_DELTA = 120

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
MONITORINFOF_PRIMARY = 1
MDT_EFFECTIVE_DPI = 0

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
HALFTONE = 4
BI_RGB, DIB_RGB_COLORS = 0, 0
CURSOR_SHOWING = 1
DI_NORMAL = 3

DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GA_ROOT = 2
SW_RESTORE = 9
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80000, 0x80, 0x08000000
GW_HWNDNEXT = 2
WM_CLOSE = 0x0010
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# user32
_sig(user32.SendInput, wt.UINT, wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_sig(user32.SetCursorPos, wt.BOOL, ctypes.c_int, ctypes.c_int)
_sig(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
_sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(user32.EnumDisplayMonitors, wt.BOOL, wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM)
_sig(user32.GetMonitorInfoW, wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
_sig(user32.GetDC, wt.HDC, wt.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
_sig(user32.GetCursorInfo, wt.BOOL, ctypes.POINTER(CURSORINFO))
_sig(user32.GetIconInfo, wt.BOOL, HCURSOR, ctypes.POINTER(ICONINFO))
_sig(user32.DrawIconEx, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, HCURSOR, ctypes.c_int,
     ctypes.c_int, wt.UINT, wt.HBRUSH, wt.UINT)
_sig(user32.GetForegroundWindow, wt.HWND)
_sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
_sig(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.IsIconic, wt.BOOL, wt.HWND)
_sig(user32.IsWindowVisible, wt.BOOL, wt.HWND)
_sig(user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
_sig(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_sig(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_sig(user32.EnumWindows, wt.BOOL, WNDENUMPROC, wt.LPARAM)
_sig(user32.WindowFromPoint, wt.HWND, wt.POINT)
_sig(user32.GetAncestor, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.GetWindowLongPtrW, ctypes.c_ssize_t, wt.HWND, ctypes.c_int)
_sig(user32.GetTopWindow, wt.HWND, wt.HWND)
_sig(user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.VkKeyScanW, ctypes.c_short, wt.WCHAR)
_sig(user32.MapVirtualKeyW, wt.UINT, wt.UINT, wt.UINT)
_sig(user32.GetDoubleClickTime, wt.UINT)
_sig(user32.RegisterHotKey, wt.BOOL, wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
_sig(user32.UnregisterHotKey, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.GetMessageW, wt.BOOL, ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
_sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.PostThreadMessageW, wt.BOOL, wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
_sig(user32.GetKeyState, ctypes.c_short, ctypes.c_int)
_sig(user32.MessageBeep, wt.BOOL, wt.UINT)

# gdi32
_sig(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
_sig(gdi32.CreateCompatibleBitmap, wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_sig(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
_sig(gdi32.DeleteDC, wt.BOOL, wt.HDC)
_sig(gdi32.SetStretchBltMode, ctypes.c_int, wt.HDC, ctypes.c_int)
_sig(gdi32.SetBrushOrgEx, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_sig(gdi32.StretchBlt, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD)
_sig(gdi32.BitBlt, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)
_sig(gdi32.GetDIBits, ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
     ctypes.POINTER(BITMAPINFO), wt.UINT)
_sig(gdi32.SetDIBitsToDevice, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD, wt.DWORD,
     ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT)
_sig(gdi32.CreateSolidBrush, wt.HBRUSH, wt.COLORREF)
_sig(gdi32.CreatePen, wt.HPEN, ctypes.c_int, ctypes.c_int, wt.COLORREF)
_sig(gdi32.GetStockObject, wt.HGDIOBJ, ctypes.c_int)
_sig(gdi32.Rectangle, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_sig(gdi32.Ellipse, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_sig(gdi32.MoveToEx, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
_sig(gdi32.LineTo, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SetBkMode, ctypes.c_int, wt.HDC, ctypes.c_int)
_sig(gdi32.SetTextColor, wt.COLORREF, wt.HDC, wt.COLORREF)
_sig(gdi32.CreateFontW, wt.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR)
_sig(gdi32.TextOutW, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, wt.LPCWSTR, ctypes.c_int)
_sig(gdi32.GetTextExtentPoint32W, wt.BOOL, wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.SIZE))

# kernel32
_sig(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_sig(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
_sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_sig(kernel32.GetCurrentThreadId, wt.DWORD)

if dwmapi is not None:
    _sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
if shcore is not None:
    _sig(shcore.GetDpiForMonitor, ctypes.c_long, wt.HMONITOR, ctypes.c_int,
         ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT))


def rgb(r, g, b):
    """COLORREF from 0-255 components (GDI stores them as 0x00BBGGRR)."""
    return (b << 16) | (g << 8) | r
