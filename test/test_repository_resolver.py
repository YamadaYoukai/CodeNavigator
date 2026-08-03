import pytest

from src.examples.code_understanding_agent import (
    GET_FILE_CONTEXT,
    SEARCH_CODE,
    RepositoryAliasResolver,
    RepositoryHint,
    ToolCall,
    ToolCallResolutionError,
    ToolErrorCode,
)


def build_resolver() -> RepositoryAliasResolver:
    return RepositoryAliasResolver(
        (
            RepositoryHint(
                canonical_name="click",
                aliases=("Click", "pallets/click", "github.com/pallets/click"),
            ),
        )
    )


def test_resolves_exact_search_alias_without_mutating_model_call() -> None:
    resolver = build_resolver()
    model_call = ToolCall(
        call_id="call-search",
        tool_name=SEARCH_CODE,
        arguments={"query": "def make_context", "repo": "pallets/click"},
    )

    executable_call = resolver.resolve(model_call)

    assert model_call.arguments["repo"] == "pallets/click"
    assert executable_call.arguments["repo"] == "click"
    assert executable_call.call_id == model_call.call_id
    assert executable_call is not model_call


def test_resolves_get_file_context_alias_to_same_canonical_name() -> None:
    resolver = build_resolver()
    model_call = ToolCall(
        call_id="call-context",
        tool_name=GET_FILE_CONTEXT,
        arguments={
            "repository": "github.com/pallets/click",
            "file_path": "src/click/core.py",
            "line_number": 1169,
        },
    )

    executable_call = resolver.resolve(model_call)

    assert executable_call.arguments["repository"] == "click"


def test_keeps_null_search_repository_unscoped() -> None:
    resolver = build_resolver()
    model_call = ToolCall(
        call_id="call-unscoped",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": None},
    )

    assert resolver.resolve(model_call) == model_call


@pytest.mark.parametrize("repository_name", ["CLICK", "click.git", "unknown/click"])
def test_rejects_unconfigured_names_without_fuzzy_fallback(
    repository_name: str,
) -> None:
    resolver = build_resolver()
    model_call = ToolCall(
        call_id="call-unknown",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": repository_name},
    )

    with pytest.raises(ToolCallResolutionError) as caught:
        resolver.resolve(model_call)

    assert caught.value.error_code == ToolErrorCode.UNKNOWN_REPOSITORY


def test_rejects_alias_shared_by_multiple_canonical_repositories() -> None:
    resolver = RepositoryAliasResolver(
        (
            RepositoryHint(canonical_name="click", aliases=("shared/click",)),
            RepositoryHint(canonical_name="click-fork", aliases=("shared/click",)),
        )
    )
    model_call = ToolCall(
        call_id="call-ambiguous",
        tool_name=SEARCH_CODE,
        arguments={"query": "make_context", "repo": "shared/click"},
    )

    with pytest.raises(ToolCallResolutionError) as caught:
        resolver.resolve(model_call)

    assert caught.value.error_code == ToolErrorCode.AMBIGUOUS_REPOSITORY
