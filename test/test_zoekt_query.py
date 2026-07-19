import unittest

from src.services.zoekt_client import build_zoekt_query


class BuildZoektQueryTest(unittest.TestCase):

    def test_should_append_filters(self):
        result = build_zoekt_query(
            query="@RestController",
            repo="demo-service",
            lang="java",
            path="src/main",
        )

        self.assertEqual(
            result,
            "@RestController r:demo-service lang:java f:src/main",
        )

    def test_should_quote_literal_with_hyphen(self):
        result = build_zoekt_query(
            query="sample-service-v2",
            repo=None,
            lang="java",
            path=None,
            literal=True,
        )

        self.assertEqual(
            result,
            '"sample-service-v2" lang:java',
        )

    def test_should_preserve_regular_query(self):
        result = build_zoekt_query(
            query="class.*Service",
            repo=None,
            lang=None,
            path=None,
        )

        self.assertEqual(result, "class.*Service")


if __name__ == "__main__":
    unittest.main()
