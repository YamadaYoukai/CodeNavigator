import unittest
from unittest.mock import AsyncMock, Mock, patch

from evaluation.run_eval import (
    EvaluationCaseError,
    build_summary,
    evaluate_case,
    validate_case,
)
from src.models.code_search import CodeMatch, CodeSearchResponse
from src.models.file_context import FileContext


def match_case(*, context: dict | None = None) -> dict:
    expected = {
        "outcome": "match",
        "repo": "click",
        "path": "src/click/globals.py",
        "line": 1,
    }
    if context is not None:
        expected["context"] = context

    return {
        "id": "context-boundary",
        "category": "context_boundary_start",
        "search": {
            "query": "from __future__ import annotations",
            "repo": "click",
            "limit": 10,
            "literal": True,
        },
        "expected": expected,
    }


class ValidateEvaluationCaseTest(unittest.TestCase):
    def test_accepts_no_match_with_explicit_scope_and_reason(self):
        case = {
            "id": "no-match",
            "category": "no_match",
            "search": {
                "query": "missing_symbol",
                "repo": "click",
                "path": "src/click/core\\.py",
                "literal": True,
            },
            "expected": {
                "outcome": "no_match",
                "repo": "click",
                "path": "src/click/core.py",
                "line": None,
                "failure_reason": "The fixed source does not contain it.",
            },
        }

        result = validate_case(case, 1)

        self.assertEqual(result["expected"]["outcome"], "no_match")
        self.assertIsNone(result["expected"]["line"])

    def test_rejects_no_match_without_failure_reason(self):
        case = {
            "id": "no-match",
            "category": "no_match",
            "search": {"query": "missing_symbol"},
            "expected": {
                "outcome": "no_match",
                "repo": "click",
                "path": "src/click/core.py",
                "line": None,
            },
        }

        with self.assertRaisesRegex(
                EvaluationCaseError,
                "no_match requires a non-empty",
        ):
            validate_case(case, 1)

    def test_rejects_no_match_without_explicit_null_line(self):
        case = {
            "id": "no-match",
            "category": "no_match",
            "search": {"query": "missing_symbol"},
            "expected": {
                "outcome": "no_match",
                "repo": "click",
                "path": "src/click/core.py",
                "failure_reason": "The fixed source does not contain it.",
            },
        }

        with self.assertRaisesRegex(
                EvaluationCaseError,
                "expected.line must be null",
        ):
            validate_case(case, 1)

    def test_rejects_invalid_context_boundary_range(self):
        case = match_case(context={
            "start_line": 4,
            "end_line": 3,
        })

        with self.assertRaisesRegex(
                EvaluationCaseError,
                "start_line cannot exceed end_line",
        ):
            validate_case(case, 1)


class EvaluateCaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_match_skips_context_and_is_a_separate_success(self):
        case = validate_case({
            "id": "no-match",
            "category": "no_match",
            "search": {"query": "missing_symbol", "limit": 10},
            "expected": {
                "outcome": "no_match",
                "repo": "click",
                "path": "src/click/core.py",
                "line": None,
                "failure_reason": "The fixed source does not contain it.",
            },
        }, 1)
        search_result = CodeSearchResponse(
            query="missing_symbol",
            duration_ms=2,
            matches=[],
        )

        with patch(
                "evaluation.run_eval.search_code",
                new=AsyncMock(return_value=search_result),
        ), patch("evaluation.run_eval.get_file_context") as get_context:
            record = await evaluate_case(case)

        get_context.assert_not_called()
        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["no_match_ok"])
        self.assertIsNone(record["hit_at_1"])
        self.assertIsNone(record["closed_loop"])
        self.assertEqual(record["context_skipped_reason"], "expected_no_match")

        summary = build_summary([record])
        self.assertEqual(summary["hit_at_1"]["total"], 0)
        self.assertEqual(summary["no_match_success"]["count"], 1)
        self.assertEqual(summary["expected_result_success"]["count"], 1)

    async def test_boundary_expectation_checks_context_clamping(self):
        case = validate_case(match_case(context={
            "start_line": 1,
            "end_line": 21,
            "total_lines": 67,
            "truncated": True,
        }), 1)
        search_result = CodeSearchResponse(
            query="from __future__ import annotations",
            duration_ms=1,
            matches=[CodeMatch(
                repo="click",
                path="src/click/globals.py",
                line=1,
                snippet="from __future__ import annotations",
            )],
        )
        context_result = FileContext(
            repository="click",
            file_path="src/click/globals.py",
            target_line=1,
            start_line=1,
            end_line=21,
            total_lines=67,
            content=">    1 | from __future__ import annotations",
            truncated=True,
        )

        with patch(
                "evaluation.run_eval.search_code",
                new=AsyncMock(return_value=search_result),
        ), patch(
                "evaluation.run_eval.get_file_context",
                new=Mock(return_value=context_result),
        ):
            record = await evaluate_case(case)

        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["context_ok"])
        self.assertTrue(record["closed_loop"])
        self.assertTrue(
            record["context_validation"]["boundary_expectation_met"]
        )

    async def test_boundary_mismatch_fails_context_assertion(self):
        case = validate_case(match_case(context={
            "start_line": 1,
            "end_line": 21,
            "total_lines": 67,
            "truncated": True,
        }), 1)
        search_result = CodeSearchResponse(
            query="from __future__ import annotations",
            duration_ms=1,
            matches=[CodeMatch(
                repo="click",
                path="src/click/globals.py",
                line=1,
                snippet="from __future__ import annotations",
            )],
        )
        context_result = FileContext(
            repository="click",
            file_path="src/click/globals.py",
            target_line=1,
            start_line=1,
            end_line=20,
            total_lines=67,
            content=">    1 | from __future__ import annotations",
            truncated=True,
        )

        with patch(
                "evaluation.run_eval.search_code",
                new=AsyncMock(return_value=search_result),
        ), patch(
                "evaluation.run_eval.get_file_context",
                new=Mock(return_value=context_result),
        ):
            record = await evaluate_case(case)

        self.assertEqual(record["status"], "context_assertion_failed")
        self.assertFalse(record["context_ok"])
        self.assertIn(
            "expected.context.end_line",
            record["context_validation"]["failures"],
        )


if __name__ == "__main__":
    unittest.main()
