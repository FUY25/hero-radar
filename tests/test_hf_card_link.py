import unittest

from pipeline.run_pipeline import extract_github_repo_from_card


class HfCardLinkTest(unittest.TestCase):
    def test_extracts_github_repo_from_url(self):
        text = "See https://github.com/owner/repo for source."

        self.assertEqual(
            extract_github_repo_from_card(text),
            "https://github.com/owner/repo",
        )

    def test_ignores_extra_path(self):
        text = "Bug tracker: https://github.com/owner/repo/issues/3"

        self.assertEqual(
            extract_github_repo_from_card(text),
            "https://github.com/owner/repo",
        )

    def test_returns_none_without_github_link(self):
        self.assertIsNone(extract_github_repo_from_card("No repo here."))


if __name__ == "__main__":
    unittest.main()
