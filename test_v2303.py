import io
import json
import urllib.error
import unittest

from apify_instagram_client import ApifyTransientError, fetch_instagram_search_profiles
from influencer_core import InfluencerConfig, collect_influencers
from version import APP_VERSION


def config(**changes):
    values = {
        "country": "Vietnam",
        "category": "뷰티·화장품",
        "keywords": ["beauty", "skincare"],
        "target_count": 10,
        "max_queries": 8,
        "request_delay": 0,
        "verify_original_profiles": False,
        "use_apify_verification": False,
        "use_apify_discovery": True,
        "use_web_fallback": False,
        "max_runtime_seconds": 330,
        "apify_search_limit": 12,
    }
    values.update(changes)
    return InfluencerConfig(**values)


def discovery(handle):
    return {
        "username": handle,
        "fullName": f"{handle} Beauty Creator",
        "biography": "Beauty creator based in Ho Chi Minh City",
        "businessCategoryName": "Digital creator",
        "followersCount": 5000,
        "profileUrl": f"https://www.instagram.com/{handle}/",
        "searchTerm": "Vietnam beauty creator",
    }


class V2303Tests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(APP_VERSION, "2.30.3")

    def test_http_400_actor_timeout_is_retryable(self):
        body = json.dumps({
            "error": {
                "message": "Actor run did not succeed (run ID: example, status: TIMED-OUT)."
            }
        }).encode("utf-8")

        def opener(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {}, io.BytesIO(body)
            )

        with self.assertRaises(ApifyTransientError):
            fetch_instagram_search_profiles(
                ["Vietnam beauty creator"], token="secret", opener=opener
            )

    def test_search_uses_small_batch_for_trial_collection(self):
        calls = []

        def search(terms, **kwargs):
            calls.append((list(terms), kwargs["search_limit"]))
            return [discovery(f"creator{index}") for index in range(1, 16)]

        collect_influencers(
            config(),
            None,
            apify_token="secret",
            apify_search_fetcher=search,
            apify_hashtag_fetcher=lambda tags, **kwargs: [],
        )
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(len(calls[0][0]), 3)
        self.assertLessEqual(calls[0][1], 8)

    def test_timeout_retries_one_term_with_lower_limit(self):
        calls = []

        def search(terms, **kwargs):
            calls.append((list(terms), kwargs["search_limit"]))
            if len(calls) == 1:
                raise ApifyTransientError("status: TIMED-OUT")
            return [discovery(f"creator{index}") for index in range(1, 11)]

        result = collect_influencers(
            config(),
            None,
            apify_token="secret",
            apify_search_fetcher=search,
            apify_hashtag_fetcher=lambda tags, **kwargs: [],
        )
        self.assertEqual(len(calls), 2)
        self.assertLessEqual(len(calls[1][0]), 1)
        self.assertLessEqual(calls[1][1], 6)
        self.assertGreaterEqual(len(result["records"]) + len(result["review_records"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
