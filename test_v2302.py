import unittest
from unittest.mock import patch

from apify_instagram_client import ApifyTransientError, fetch_instagram_profiles_batched
from influencer_core import InfluencerConfig, collect_influencers
from version import APP_VERSION


def config(**changes):
    values = {
        "country": "Vietnam",
        "category": "뷰티·화장품",
        "keywords": ["beauty", "skincare"],
        "target_count": 10,
        "max_queries": 2,
        "request_delay": 0,
        "verify_original_profiles": False,
        "use_apify_verification": False,
        "use_apify_discovery": True,
        "use_web_fallback": False,
        "max_runtime_seconds": 330,
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


class V2302Tests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(APP_VERSION, "2.30.3")

    def test_transient_profile_call_retries_once(self):
        calls = []

        def fake(handles, **kwargs):
            calls.append(list(handles))
            if len(calls) == 1:
                raise ApifyTransientError("temporary timeout")
            return [{"username": handles[0]}]

        with patch("apify_instagram_client.fetch_instagram_profiles", side_effect=fake):
            rows = fetch_instagram_profiles_batched(["creator1"], token="secret", retry_delay=0)
        self.assertEqual([row["username"] for row in rows], ["creator1"])
        self.assertEqual(len(calls), 2)

    def test_late_batch_timeout_preserves_earlier_batch(self):
        calls = []

        def fake(handles, **kwargs):
            calls.append(list(handles))
            if handles == ["creator1"]:
                return [{"username": "creator1"}]
            raise ApifyTransientError("temporary timeout")

        with patch("apify_instagram_client.fetch_instagram_profiles", side_effect=fake):
            rows = fetch_instagram_profiles_batched(
                ["creator1", "creator2"], token="secret", batch_size=1, retry_delay=0,
            )
        self.assertEqual([row["username"] for row in rows], ["creator1"])
        self.assertEqual(calls.count(["creator2"]), 2)

    def test_hashtag_actor_is_skipped_when_user_search_pool_is_sufficient(self):
        search_rows = [discovery(f"creator{index}") for index in range(1, 16)]
        hashtag_calls = []
        result = collect_influencers(
            config(),
            None,
            apify_token="secret",
            apify_search_fetcher=lambda terms, **kwargs: search_rows,
            apify_hashtag_fetcher=lambda tags, **kwargs: hashtag_calls.append(tags) or [],
        )
        self.assertEqual(hashtag_calls, [])
        self.assertEqual(result["summary"]["instagram_discovery_calls"], 1)
        self.assertEqual(result["summary"]["instagram_hashtag_posts"], 0)

    def test_profile_timeout_returns_review_payload_instead_of_aborting(self):
        result = collect_influencers(
            config(target_count=1, use_apify_verification=True),
            None,
            apify_token="secret",
            apify_search_fetcher=lambda terms, **kwargs: [discovery("creator1")],
            apify_hashtag_fetcher=lambda tags, **kwargs: [],
            apify_fetcher=lambda handles, **kwargs: (_ for _ in ()).throw(
                ApifyTransientError("temporary timeout")
            ),
        )
        self.assertEqual(result["records"], [])
        self.assertEqual(len(result["review_records"]), 1)
        self.assertTrue(any("부분결과 보존" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
