"""Stable, local post-ranking for declaration-oriented identifier searches.

Zoekt remains the sole retrieval and filtering engine.  This module only
stable-partitions an already returned candidate list when a query is a bare
identifier literal without a path filter.  A candidate is promoted only when
the queried identifier itself is declared in local source (for example, a
function/class definition or a Python function parameter declaration).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from src.models import CodeMatch
from src.services.file_reader import (
    InvalidFilePathError,
    SourceFileNotFoundError,
    resolve_source_file,
)


_BARE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_GENERIC_TYPE_DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+)?"
    r"(?:class|interface|enum|struct|trait|record|type)\s+",
)
_GENERIC_FUNCTION_DECLARATION = re.compile(
    r"^\s*(?:(?:public|private|protected|static|async|export)\s+)*"
    r"(?:func|function|def)\s+",
)
_GENERIC_VARIABLE_DECLARATION = re.compile(
    r"^\s*(?:(?:public|private|protected|static|export|final)\s+)*"
    r"(?:const|let|var|val)\s+",
)


RepositoryRootResolver = Callable[[str], Path]


def is_declaration_rerank_eligible(
        *,
        query: str,
        path: str | None,
        literal: bool,
) -> bool:
    """Return whether the narrow declaration-context rule may run.

    The rule intentionally excludes regular-expression/non-literal searches
    and any explicit path-filtered search, so their Zoekt order is untouched.
    """
    return bool(
        literal
        and not path
        and _BARE_IDENTIFIER.fullmatch(query)
    )


def prioritize_declaration_context(
        matches: Sequence[CodeMatch],
        *,
        query: str,
        path: str | None,
        literal: bool,
        resolve_repository_root: RepositoryRootResolver,
) -> list[CodeMatch]:
    """Stable-partition existing matches by declaration context.

    This never calls Zoekt, changes a query/filter, drops a candidate, or adds
    a candidate.  Each partition retains Zoekt's original order, including
    original ties.  If local source is unavailable or cannot be parsed, that
    candidate stays in its original non-priority partition.
    """
    original_matches = list(matches)

    if (
            not original_matches
            or not is_declaration_rerank_eligible(
                query=query,
                path=path,
                literal=literal,
            )
    ):
        return original_matches

    repository_roots: dict[str, Path | None] = {}
    declaration_lines_cache: dict[tuple[Path, str], frozenset[int]] = {}
    declarations: list[CodeMatch] = []
    remaining: list[CodeMatch] = []

    for match in original_matches:
        if _is_declaration_context(
                match,
                query=query,
                resolve_repository_root=resolve_repository_root,
                repository_roots=repository_roots,
                declaration_lines_cache=declaration_lines_cache,
        ):
            declarations.append(match)
        else:
            remaining.append(match)

    return declarations + remaining


def _is_declaration_context(
        match: CodeMatch,
        *,
        query: str,
        resolve_repository_root: RepositoryRootResolver,
        repository_roots: dict[str, Path | None],
        declaration_lines_cache: dict[tuple[Path, str], frozenset[int]],
) -> bool:
    repository_root = _resolve_repository_root(
        match.repo,
        resolve_repository_root=resolve_repository_root,
        repository_roots=repository_roots,
    )
    if repository_root is None:
        return False

    try:
        source_file = resolve_source_file(
            repository_root=repository_root,
            file_path=match.path,
        )
    except (InvalidFilePathError, SourceFileNotFoundError, OSError):
        return False

    cache_key = (source_file, query)
    declaration_lines = declaration_lines_cache.get(cache_key)
    if declaration_lines is None:
        declaration_lines = _declaration_lines_for_query(
            source_file,
            query=query,
        )
        declaration_lines_cache[cache_key] = declaration_lines

    return match.line in declaration_lines


def _resolve_repository_root(
        repository: str,
        *,
        resolve_repository_root: RepositoryRootResolver,
        repository_roots: dict[str, Path | None],
) -> Path | None:
    if repository in repository_roots:
        return repository_roots[repository]

    try:
        repository_root = Path(resolve_repository_root(repository)).resolve()
    except (OSError, TypeError, ValueError):
        repository_root = None

    repository_roots[repository] = repository_root
    return repository_root


def _declaration_lines_for_query(
        source_file: Path,
        *,
        query: str,
) -> frozenset[int]:
    try:
        source = source_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = source_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()

    if source_file.suffix.lower() == ".py":
        return _python_declaration_lines(source, query=query)

    return _generic_declaration_lines(source, query=query)


def _python_declaration_lines(source: str, *, query: str) -> frozenset[int]:
    """Find lines where ``query`` itself is declared in valid Python code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    declaration_lines: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == query:
                declaration_lines.add(node.lineno)

            for argument in _function_arguments(node):
                if argument.arg == query:
                    declaration_lines.add(argument.lineno)

        elif isinstance(node, ast.ClassDef) and node.name == query:
            declaration_lines.add(node.lineno)

        elif isinstance(node, ast.AnnAssign):
            if _assignment_declares_name(node.target, query):
                declaration_lines.add(node.target.lineno)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if _assignment_declares_name(target, query):
                    declaration_lines.add(target.lineno)

    return frozenset(declaration_lines)


def _function_arguments(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    arguments = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]

    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)

    return arguments


def _assignment_declares_name(target: ast.expr, query: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == query

    if isinstance(target, (ast.Tuple, ast.List)):
        return any(
            _assignment_declares_name(element, query)
            for element in target.elts
        )

    return False


def _generic_declaration_lines(source: str, *, query: str) -> frozenset[int]:
    """Conservatively recognize direct declarations in non-Python files."""
    name_pattern = re.compile(
        rf"(?:^|\s){re.escape(query)}(?![A-Za-z0-9_])"
    )
    declaration_lines: set[int] = set()

    for line_number, line in enumerate(source.splitlines(), start=1):
        if not name_pattern.search(line):
            continue

        prefix_matches_declaration = any(
            pattern.match(line)
            for pattern in (
                _GENERIC_TYPE_DECLARATION,
                _GENERIC_FUNCTION_DECLARATION,
                _GENERIC_VARIABLE_DECLARATION,
            )
        )
        if prefix_matches_declaration:
            declaration_lines.add(line_number)

    return frozenset(declaration_lines)
