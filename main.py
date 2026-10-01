"""Setuno entry point."""
import sys

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QProgressBar, QSplashScreen

from playlistbro import __version__
from playlistbro.ui.icon_loader import app_icon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Setuno")
    app.setWindowIcon(app_icon())

    image = QPixmap(360, 185)
    image.fill(QColor("#f4f4fa"))
    painter = QPainter(image)
    painter.drawPixmap(140, 20, app_icon().pixmap(80, 80))
    painter.setPen(QColor("#2c2c44"))
    painter.setFont(QFont("Segoe UI", 17, QFont.DemiBold))
    painter.drawText(QRect(0, 105, 360, 32), Qt.AlignCenter, "Setuno")
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(QRect(0, 134, 360, 18), Qt.AlignCenter, f"v{__version__}")
    painter.end()

    splash = QSplashScreen(image)
    progress = QProgressBar(splash)
    progress.setGeometry(30, 155, 300, 6)
    progress.setTextVisible(False)
    progress.setValue(10)
    progress.setStyleSheet("QProgressBar { border: none; background: #e8e8f5; } "
                           "QProgressBar::chunk { background: #1677ff; }")
    splash.show()
    app.processEvents()

    from playlistbro.ui.main_window import MainWindow

    progress.setValue(45)
    app.processEvents()
    window = MainWindow()  # applies the saved theme or the light default
    progress.setValue(100)
    window.show()
    splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
