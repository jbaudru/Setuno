"""Windows taskbar thumbnail playback controls (not imported on other systems)."""
import ctypes
import uuid
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

from .icon_loader import icon


WM_COMMAND = 0x0111
THBN_CLICKED = 0x1800
BUTTON_PREVIOUS, BUTTON_PLAY, BUTTON_NEXT = 1, 2, 3
THB_ICON = 0x2
THB_TOOLTIP = 0x4


class _GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _THUMBBUTTON(ctypes.Structure):
    _fields_ = [("mask", wintypes.DWORD), ("id", wintypes.UINT),
                ("bitmap", wintypes.UINT), ("icon", wintypes.HANDLE),
                ("tip", wintypes.WCHAR * 260), ("flags", wintypes.DWORD)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wparam", wintypes.WPARAM), ("lparam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("point", _POINT), ("private", wintypes.DWORD)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("is_icon", wintypes.BOOL), ("x", wintypes.DWORD),
                ("y", wintypes.DWORD), ("mask", wintypes.HANDLE),
                ("color", wintypes.HANDLE)]


def _native_icon(name):
    from PySide6.QtGui import QImage

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    size = user32.GetSystemMetrics(11)
    image = icon(name, "#ffffff").pixmap(size, size).toImage().convertToFormat(
        QImage.Format_ARGB32_Premultiplied
    )
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                                   wintypes.UINT, ctypes.c_void_p]
    gdi32.CreateBitmap.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(_ICONINFO)]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    bitmap = gdi32.CreateBitmap(size, size, 1, 32, image.bits().tobytes())
    mask = gdi32.CreateBitmap(size, size, 1, 1, None)
    info = _ICONINFO(True, 0, 0, mask, bitmap)
    user32.CreateIconIndirect.restype = wintypes.HICON
    result = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(mask)
    gdi32.DeleteObject(bitmap)
    return result


class WindowsTaskbarControls(QAbstractNativeEventFilter):
    def __init__(self, window, previous, play_pause, next_track):
        super().__init__()
        self.window = window
        self.actions = {BUTTON_PREVIOUS: previous, BUTTON_PLAY: play_pause,
                        BUTTON_NEXT: next_track}
        user32 = ctypes.windll.user32
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        self.created_message = user32.RegisterWindowMessageW("TaskbarButtonCreated")
        self.hwnd = int(window.winId())
        self.interface = None
        self.added = False
        self.playing = False
        QCoreApplication.instance().installNativeEventFilter(self)
        QCoreApplication.instance().aboutToQuit.connect(self.close)

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        event = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        if event.hwnd != self.hwnd:
            return False, 0
        if event.message == self.created_message:
            self.added = False
            self._add_buttons()
        elif event.message == WM_COMMAND and (event.wparam >> 16) == THBN_CLICKED:
            callback = self.actions.get(event.wparam & 0xffff)
            if callback:
                callback()
                return True, 0
        return False, 0

    def _taskbar(self):
        if self.interface:
            return self.interface
        ole32 = ctypes.windll.ole32
        clsid = _GUID.parse("56FDF344-FD6D-11D0-958A-006097C9A090")
        iid = _GUID.parse("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")
        pointer = ctypes.c_void_p()
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(_GUID),
                                           ctypes.POINTER(ctypes.c_void_p)]
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                  ctypes.byref(pointer)) != 0:
            return None
        self.interface = pointer
        return pointer

    def _call(self, index, buttons):
        pointer = self._taskbar()
        if not pointer:
            return False
        # ITaskbarList3 inherits IUnknown, ITaskbarList and ITaskbarList2.
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                                    wintypes.UINT, ctypes.POINTER(_THUMBBUTTON))(vtable[index])
        return method(pointer, self.hwnd, len(buttons), buttons) == 0

    def _buttons(self, playing=False, only_play=False):
        names = ((BUTTON_PREVIOUS, "prev", "Previous"),
                 (BUTTON_PLAY, "pause" if playing else "play", "Pause" if playing else "Play"),
                 (BUTTON_NEXT, "next", "Next"))
        if only_play:
            names = names[1:2]
        buttons = (_THUMBBUTTON * len(names))()
        for button, (identifier, name, tooltip) in zip(buttons, names):
            button.mask = THB_ICON | THB_TOOLTIP
            button.id = identifier
            button.icon = _native_icon(name)
            button.tip = tooltip
        return buttons

    def _add_buttons(self):
        buttons = self._buttons(self.playing)
        try:
            self.added = self._call(15, buttons)
        finally:
            for button in buttons:
                ctypes.windll.user32.DestroyIcon(button.icon)

    def set_playing(self, playing):
        self.playing = bool(playing)
        if not self.added:
            return
        buttons = self._buttons(playing, only_play=True)
        try:
            self._call(16, buttons)
        finally:
            for button in buttons:
                ctypes.windll.user32.DestroyIcon(button.icon)

    def close(self):
        QCoreApplication.instance().removeNativeEventFilter(self)
        if self.interface:
            vtable = ctypes.cast(self.interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])(self.interface)
            self.interface = None