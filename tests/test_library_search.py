import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from playlistbro.core.models import Track
from playlistbro.ui.library_view import LibraryView


class LibrarySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_large_search_paging_and_sorting(self):
        db = Mock()
        db.get_all_tracks.return_value = [
            Track(id=index + 1, title=f"Mix {index}", energy=10 if index == 449 else index / 1000)
            for index in range(450)
        ]
        view = LibraryView(db)
        self.addCleanup(view.close)

        self.assertEqual(view.table.rowCount(), 450)
        view.table.sortItems(8, Qt.DescendingOrder)
        self.assertEqual(view.get_row_ids()[0], 450)
        self.assertEqual(len(set(view.get_row_ids())), 450)

        view.search_edit.setText("mix 44")
        view.refresh_table()
        self.assertEqual(view.table.rowCount(), 11)
        view.search_edit.setText("mix")
        view.refresh_table()
        self.assertEqual(view.table.rowCount(), 450)
        self.assertFalse(hasattr(view, "more_btn"))


if __name__ == "__main__":
    unittest.main()