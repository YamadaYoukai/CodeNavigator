import base64
import unittest

from src.services.zoekt_client import ZoektClient


class ParseZoektResponseTest(unittest.TestCase):

    def test_should_parse_line_match(self):
        encoded = base64.b64encode(
            b"public class UserService {}"
        ).decode("ascii")

        data = {
            "Result": {
                "Duration": 2_000_000,
                "Files": [
                    {
                        "Repository": "user-service",
                        "FileName": "src/UserService.java",
                        "LineMatches": [
                            {
                                "LineNumber": 12,
                                "Line": encoded,
                            }
                        ],
                    }
                ],
            }
        }

        result = ZoektClient._parse_response(
            query="UserService",
            data=data,
            limit=10,
        )

        self.assertEqual(result.query, "UserService")
        self.assertEqual(result.duration_ms, 2)
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].repo, "user-service")
        self.assertEqual(result.matches[0].line, 12)
        self.assertEqual(
            result.matches[0].snippet,
            "public class UserService {}",
        )

    def test_should_return_empty_matches(self):
        result = ZoektClient._parse_response(
            query="NotFound",
            data={"Result": {"Duration": 0, "Files": []}},
            limit=10,
        )

        self.assertEqual(result.matches, [])

    def test_should_respect_limit(self):
        encoded = base64.b64encode(b"match").decode("ascii")
        line_matches = [
            {"LineNumber": index, "Line": encoded}
            for index in range(1, 6)
        ]

        result = ZoektClient._parse_response(
            query="match",
            data={
                "Result": {
                    "Files": [
                        {
                            "Repository": "repo",
                            "FileName": "file.py",
                            "LineMatches": line_matches,
                        }
                    ]
                }
            },
            limit=2,
        )

        self.assertEqual(len(result.matches), 2)


if __name__ == "__main__":
    unittest.main()
