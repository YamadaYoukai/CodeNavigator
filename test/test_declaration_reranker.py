import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.models import CodeMatch
from src.services.declaration_reranker import (
    is_declaration_rerank_eligible,
    prioritize_declaration_context,
)


def match(path: str, line: int, snippet: str) -> CodeMatch:
    return CodeMatch(
        repo="click",
        path=path,
        line=line,
        snippet=snippet,
    )


def candidate_ids(matches: list[CodeMatch]) -> list[tuple[str, int]]:
    return [(item.path, item.line) for item in matches]


class DeclarationContextRerankerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name) / "click"
        self.repository_root.mkdir()

        self._write(
            "src/click/globals.py",
            "\n".join([
                "from .base import resolve_color_default",
                "value = resolve_color_default(value)",
                "# resolve_color_default is used for the default color.",
                "",
                "def resolve_color_default(color: bool | None = None) -> bool | None:",
                "    return color",
                "",
            ]),
        )
        self._write(
            "src/click/core.py",
            "\n".join([
                '\"\"\"callback documentation in source.\"\"\"',
                "# callback comments are not declarations.",
                "def call() -> object:",
                "    return callback()",
                "",
                "def invoke(",
                "    self, callback: object, /,",
                ") -> object:",
                "    return callback",
                "",
            ]),
        )
        self._write(
            "src/click/utils.py",
            "\n".join([
                "def get_text_stream(",
                "    name: str,",
                ") -> object:",
                "    return name",
                "",
            ]),
        )
        self._write(
            "src/click/exceptions.py",
            "\n".join([
                "from .exceptions import UsageError",
                "",
                "def parse() -> None:",
                "    raise UsageError()",
                "",
                "class BadParameter(UsageError):",
                "    pass",
                "",
                "class UsageError(Exception):",
                "    pass",
                "",
            ]),
        )
        self._write("docs/api.md", ".. autofunction:: get_text_stream\n")
        self._write("docs/reference.md", "resolve_color_default reference\n")
        self._write("CHANGES.rst", "``get_text_stream(...)`` was added.\n")

    def _write(self, relative_path: str, content: str) -> None:
        path = self.repository_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _rerank(
            self,
            matches: list[CodeMatch],
            *,
            query: str,
            path: str | None = None,
            literal: bool = True,
    ) -> list[CodeMatch]:
        return prioritize_declaration_context(
            matches,
            query=query,
            path=path,
            literal=literal,
            resolve_repository_root=lambda repository: self.repository_root,
        )

    def test_prioritizes_resolve_color_default_definition_before_non_declarations(self):
        original = [
            match(
                "src/click/globals.py",
                1,
                "from .base import resolve_color_default",
            ),
            match(
                "src/click/globals.py",
                2,
                "value = resolve_color_default(value)",
            ),
            match(
                "src/click/globals.py",
                3,
                "# resolve_color_default is used for the default color.",
            ),
            match("docs/reference.md", 1, "resolve_color_default reference"),
            match("CHANGES.rst", 1, "resolve_color_default was introduced"),
            match(
                "src/click/globals.py",
                5,
                "def resolve_color_default(color: bool | None = None) -> bool | None:",
            ),
        ]

        reranked = self._rerank(
            original,
            query="resolve_color_default",
        )

        self.assertEqual(
            candidate_ids(reranked),
            [
                ("src/click/globals.py", 5),
                ("src/click/globals.py", 1),
                ("src/click/globals.py", 2),
                ("src/click/globals.py", 3),
                ("docs/reference.md", 1),
                ("CHANGES.rst", 1),
            ],
        )
        self.assertCountEqual(candidate_ids(reranked), candidate_ids(original))

    def test_prioritizes_callback_multiline_declaration_signature(self):
        original = [
            match(
                "src/click/core.py",
                1,
                '"""callback documentation in source."""',
            ),
            match(
                "src/click/core.py",
                2,
                "# callback comments are not declarations.",
            ),
            match("src/click/core.py", 4, "return callback()"),
            match(
                "src/click/core.py",
                7,
                "self, callback: object, /,",
            ),
        ]

        reranked = self._rerank(original, query="callback")

        self.assertEqual(
            candidate_ids(reranked),
            [
                ("src/click/core.py", 7),
                ("src/click/core.py", 1),
                ("src/click/core.py", 2),
                ("src/click/core.py", 4),
            ],
        )

    def test_prioritizes_get_text_stream_definition_before_documentation_and_changes(self):
        original = [
            match("docs/api.md", 1, ".. autofunction:: get_text_stream"),
            match("CHANGES.rst", 1, "``get_text_stream(...)`` was added."),
            match("src/click/utils.py", 1, "def get_text_stream("),
        ]

        reranked = self._rerank(original, query="get_text_stream")

        self.assertEqual(
            candidate_ids(reranked),
            [
                ("src/click/utils.py", 1),
                ("docs/api.md", 1),
                ("CHANGES.rst", 1),
            ],
        )

    def test_prioritizes_usage_error_definition_but_not_a_child_class_reference(self):
        original = [
            match(
                "src/click/exceptions.py",
                1,
                "from .exceptions import UsageError",
            ),
            match("src/click/exceptions.py", 4, "raise UsageError()"),
            match(
                "src/click/exceptions.py",
                6,
                "class BadParameter(UsageError):",
            ),
            match(
                "src/click/exceptions.py",
                9,
                "class UsageError(Exception):",
            ),
        ]

        reranked = self._rerank(original, query="UsageError")

        self.assertEqual(
            candidate_ids(reranked),
            [
                ("src/click/exceptions.py", 9),
                ("src/click/exceptions.py", 1),
                ("src/click/exceptions.py", 4),
                ("src/click/exceptions.py", 6),
            ],
        )

    def test_does_not_reorder_path_filtered_query(self):
        original = [
            match("docs/api.md", 1, ".. autofunction:: get_text_stream"),
            match("src/click/utils.py", 1, "def get_text_stream("),
        ]

        reranked = self._rerank(
            original,
            query="get_text_stream",
            path="src/click/utils\\.py",
        )

        self.assertEqual(candidate_ids(reranked), candidate_ids(original))

    def test_does_not_reorder_non_bare_or_non_literal_query(self):
        original = [
            match("docs/api.md", 1, ".. autofunction:: get_text_stream"),
            match("src/click/utils.py", 1, "def get_text_stream("),
        ]

        non_bare = self._rerank(
            original,
            query="get_text_stream()",
        )
        non_literal = self._rerank(
            original,
            query="get_text_stream",
            literal=False,
        )

        self.assertEqual(candidate_ids(non_bare), candidate_ids(original))
        self.assertEqual(candidate_ids(non_literal), candidate_ids(original))
        self.assertFalse(
            is_declaration_rerank_eligible(
                query="get_text_stream()",
                path=None,
                literal=True,
            )
        )
        self.assertFalse(
            is_declaration_rerank_eligible(
                query="get_text_stream",
                path=None,
                literal=False,
            )
        )

    def test_no_match_is_unchanged_without_resolving_a_repository(self):
        resolve_repository_root = Mock()

        reranked = prioritize_declaration_context(
            [],
            query="UsageError",
            path=None,
            literal=True,
            resolve_repository_root=resolve_repository_root,
        )

        self.assertEqual(reranked, [])
        resolve_repository_root.assert_not_called()


if __name__ == "__main__":
    unittest.main()
