"""Exact repository alias resolution for model-generated retrieval calls."""

from __future__ import annotations

from collections.abc import Iterable

from .context import RepositoryHint
from .events import ToolCall
from .tool_router import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    ToolCallResolutionError,
    ToolErrorCode,
)


class RepositoryAliasResolver:
    """Map only configured exact aliases to canonical indexed names.

    No basename, case, URL, or fuzzy normalization is attempted. This makes an
    incorrect or ambiguous model value an explicit trace error instead of a
    successful search with zero matches.
    """

    def __init__(self, repository_hints: Iterable[RepositoryHint]) -> None:
        hints = tuple(repository_hints)
        if not all(isinstance(hint, RepositoryHint) for hint in hints):
            raise TypeError("repository_hints must contain RepositoryHint instances")

        canonical_names = tuple(hint.canonical_name for hint in hints)
        if len(set(canonical_names)) != len(canonical_names):
            raise ValueError("repository_hints must have unique canonical names")

        candidates: dict[str, set[str]] = {}
        for hint in hints:
            for repository_name in (hint.canonical_name, *hint.aliases):
                candidates.setdefault(repository_name, set()).add(
                    hint.canonical_name
                )
        self._candidates = {
            repository_name: frozenset(names)
            for repository_name, names in candidates.items()
        }

    def resolve(self, call: ToolCall) -> ToolCall:
        """Return a detached call with one canonical repository argument."""

        if not isinstance(call, ToolCall):
            raise TypeError("call must be a ToolCall")

        argument_name: str | None = None
        if call.tool_name == SEARCH_CODE:
            argument_name = "repo"
        elif call.tool_name == GET_FILE_CONTEXT:
            argument_name = "repository"
        if argument_name is None:
            return call.model_copy(deep=True)

        repository_name = call.arguments.get(argument_name)
        if repository_name is None and call.tool_name == SEARCH_CODE:
            return call.model_copy(deep=True)
        if not isinstance(repository_name, str):
            # Leave type/required-field classification to the router contract.
            return call.model_copy(deep=True)
        if not repository_name.strip():
            return call.model_copy(deep=True)

        candidates = self._candidates.get(repository_name)
        if not candidates:
            raise ToolCallResolutionError(ToolErrorCode.UNKNOWN_REPOSITORY)
        if len(candidates) != 1:
            raise ToolCallResolutionError(ToolErrorCode.AMBIGUOUS_REPOSITORY)

        canonical_name = next(iter(candidates))
        arguments = dict(call.arguments)
        arguments[argument_name] = canonical_name
        return call.model_copy(update={"arguments": arguments}, deep=True)
