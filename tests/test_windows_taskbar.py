import ctypes
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


@unittest.skipUnless(sys.platform == "win32", "Windows thumbnail toolbar only")
class WindowsTaskbarTests(unittest.TestCase):
    def test_thumbnail_play_click_routes_to_callback(self):
        from playlistbro.ui.windows_taskbar import (
            BUTTON_PLAY, THBN_CLICKED, WM_COMMAND, WindowsTaskbarControls, _MSG,
        )

        play = Mock()
        window = SimpleNamespace(hwnd=123, created_message=9000,
                                 actions={BUTTON_PLAY: play}, _add_buttons=Mock())
        message = _MSG()
        message.hwnd = 123
        message.message = WM_COMMAND
        message.wparam = (THBN_CLICKED << 16) | BUTTON_PLAY
        result = WindowsTaskbarControls.nativeEventFilter(
            window, b"windows_generic_MSG", ctypes.addressof(message)
        )
        self.assertEqual(result, (True, 0))
        play.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()