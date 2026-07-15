import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import get_repository_root


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

        self.base_repository = Path(self.temporary_directory.name).resolve()
        (self.base_repository / "api").mkdir()

        self.repository_root_patch = patch.dict(
            os.environ,
            {"REPOSITORY_ROOT": str(self.base_repository)},
        )
        self.repository_root_patch.start()
        self.addCleanup(self.repository_root_patch.stop)

    def test_get_repository_root(self):
        result = get_repository_root("api")
        self.assertEqual(result, Path(self.base_repository) / "api")

    def test_rejects_path_outside_base_repository(self):
        with self.assertRaises(ValueError):
            get_repository_root("../outside")

    def test_rejects_path_is_not_dir(self):
        (self.base_repository / "example.py").write_text(
            "\n".join(f"line {number}" for number in range(1, 6)),
            encoding="utf-8",
        )

        with self.assertRaises(FileNotFoundError):
            get_repository_root("example.py")


if __name__ == '__main__':
    unittest.main()
