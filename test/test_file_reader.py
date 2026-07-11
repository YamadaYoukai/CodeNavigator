import tempfile
import unittest
from pathlib import Path

from src.services.file_reader import (
    InvalidFilePathError,
    SourceFileNotFoundError,
    read_file_context,
)


class ReadFileContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temporary_directory.name) / "demo"
        self.repository_root.mkdir()
        (self.repository_root / "example.py").write_text(
            "\n".join(f"line {number}" for number in range(1, 6)),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_reads_context_around_target_line(self):
        result = read_file_context(
            repository="demo",
            repository_root=self.repository_root,
            file_path="example.py",
            line_number=3,
            lines_before=1,
            lines_after=1,
        )

        self.assertEqual(result.start_line, 2)
        self.assertEqual(result.end_line, 4)
        self.assertIn(">    3 | line 3", result.content)
        self.assertTrue(result.truncated)

    def test_clamps_context_to_file_boundaries(self):
        result = read_file_context(
            repository="demo",
            repository_root=self.repository_root,
            file_path="example.py",
            line_number=1,
        )

        self.assertEqual(result.start_line, 1)
        self.assertEqual(result.end_line, 5)
        self.assertFalse(result.truncated)

    def test_rejects_path_outside_repository(self):
        with self.assertRaises(InvalidFilePathError):
            read_file_context(
                repository="demo",
                repository_root=self.repository_root,
                file_path="../secret.txt",
                line_number=1,
            )

    def test_rejects_line_beyond_end_of_file(self):
        with self.assertRaisesRegex(ValueError, "exceeds file length"):
            read_file_context(
                repository="demo",
                repository_root=self.repository_root,
                file_path="example.py",
                line_number=6,
            )

    def test_reports_missing_file(self):
        with self.assertRaises(SourceFileNotFoundError):
            read_file_context(
                repository="demo",
                repository_root=self.repository_root,
                file_path="missing.py",
                line_number=1,
            )


if __name__ == "__main__":
    unittest.main()
