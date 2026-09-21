"""Setuno entry point."""
import sys

from PySide6.QtWidgets import QApplication

from playlistbro.ui.icon_loader import app_icon
from playlistbro.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Setuno")
    app.setWindowIcon(app_icon())
    window = MainWindow()  # applies the saved theme (dark/light) on construction
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
