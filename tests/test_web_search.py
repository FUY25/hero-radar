import unittest


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class WebSearchClientTest(unittest.TestCase):
    def test_duckduckgo_html_parser_extracts_bounded_results(self):
        from pipeline.decision.web_search import parse_duckduckgo_html

        html = """
        <html>
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2FOwner%2FRepo">Owner Repo</a>
          <a class="result__snippet">Official GitHub repository.</a>
          <a class="result__a" href="https://example.com/product">Product Home</a>
          <a class="result__snippet">Official website.</a>
        </html>
        """

        results = parse_duckduckgo_html(html, limit=1)

        self.assertEqual(
            results,
            [
                {
                    "title": "Owner Repo",
                    "url": "https://github.com/Owner/Repo",
                    "description": "Official GitHub repository.",
                }
            ],
        )

    def test_duckduckgo_search_client_is_bounded_and_uses_query(self):
        from pipeline.decision.web_search import DuckDuckGoSearchClient

        calls = []

        def fake_opener(request, timeout):
            calls.append({"url": request.full_url, "timeout": timeout})
            return FakeResponse(
                """
                <a class="result__a" href="https://example.com/a">A</a>
                <a class="result__a" href="https://example.com/b">B</a>
                """
            )

        client = DuckDuckGoSearchClient(opener=fake_opener, timeout=7)
        results = client.search("Clawdbot GitHub repo", limit=1)

        self.assertEqual(len(results), 1)
        self.assertIn("Clawdbot+GitHub+repo", calls[0]["url"])
        self.assertEqual(calls[0]["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
