import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playlistbro.core import database


class DataDirectoryTests(unittest.TestCase):
    def test_legacy_data_migrates_to_windows_and_macos_user_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "project" / "data"
            (legacy / "covers").mkdir(parents=True)
            cover = legacy / "covers" / "art.jpg"
            cover.write_bytes(b"art")
            (legacy / "library.json").write_text(json.dumps([
                {"id": 1, "cover_path": str(cover)}
            ]), encoding="utf-8")
            (legacy / "settings.json").write_text('{"theme":"light"}', encoding="utf-8")

            fake_source = root / "project" / "playlistbro" / "core" / "database.py"
            with patch.object(database, "__file__", str(fake_source)), patch.object(
                database.Path, "home", return_value=root / "home"
            ), patch.dict(database.os.environ, {"LOCALAPPDATA": str(root / "local")}, clear=False):
                for platform, expected in (
                    ("win32", root / "local" / "Setuno" / "data"),
                    ("darwin", root / "home" / "Library" / "Application Support" / "Setuno" / "data"),
                ):
                    with self.subTest(platform=platform), patch.object(database.sys, "platform", platform):
                        destination = database.default_data_dir()
                        self.assertEqual(destination, expected)
                        record = json.loads((destination / "library.json").read_text(encoding="utf-8"))[0]
                        self.assertEqual(Path(record["cover_path"]).read_bytes(), b"art")
                        self.assertTrue((destination / "settings.json").exists())
            self.assertEqual(cover.read_bytes(), b"art")

    def test_frozen_app_reads_legacy_data_beside_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "installed" / "data"
            legacy.mkdir(parents=True)
            (legacy / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
            with patch.object(database.sys, "platform", "win32"), patch.object(
                database.sys, "frozen", True, create=True
            ), patch.object(database.sys, "executable", str(root / "installed" / "Setuno.exe")), patch.dict(
                database.os.environ, {"LOCALAPPDATA": str(root / "local")}, clear=False
            ):
                destination = database.default_data_dir()
            self.assertEqual(destination, root / "local" / "Setuno" / "data")
            self.assertEqual((destination / "settings.json").read_text(encoding="utf-8"), '{"theme":"dark"}')


if __name__ == "__main__":
    unittest.main()