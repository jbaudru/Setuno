import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from playlistbro.core.models import Track
from playlistbro.ui.stats_widget import StatsWidget


class StatsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_theme_and_linked_hover(self):
        panel = StatsWidget()
        self.addCleanup(panel.close)
        panel.apply_theme("#ffffff", "#333333", "#111111", "#1677ff")
        panel.update_stats([
            Track(id=1, title="First", tempo=120, energy=4, genre="House"),
            Track(id=2, title="Second", tempo=128, energy=7, genre="Techno"),
            Track(id=3, title="Third", tempo=135, energy=9, genre="House"),
        ])
        panel.resize(1000, 300)
        panel.show()
        self.app.processEvents()

        chart = panel.tempo_chart
        QTest.mouseMove(chart, QPoint(chart.width() // 2, 65))
        self.assertEqual(chart.hovered_index, 1)
        self.assertEqual(panel.energy_chart.hovered_index, 1)
        self.assertIn("Second", panel.summary_label.text())
        rect = panel.genre_chart._pie_rect()
        QTest.mouseMove(panel.genre_chart, QPoint(int(rect.right() + 18), 36))
        self.assertIn("House: 2 tracks", panel.genre_chart.toolTip())

        panel.genre_chart.set_genres({f"Genre {index}": 1 for index in range(25)})
        self.assertGreaterEqual(panel.genre_chart._pie_rect().width(), 48)

        panel.apply_theme("#121212", "#aaaaaa", "#ffffff", "#bfcc94")
        self.assertEqual(panel.genre_chart.accent_color.name(), "#bfcc94")
        self.assertEqual(chart.bg_color.name(), "#121212")


if __name__ == "__main__":
    unittest.main()