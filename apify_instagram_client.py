"""Minimal Apify client used by GLOEUM's Instagram verification stage."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_ACTOR_ID = "apify~instagram-profile-scraper"
DEFAULT_SEARCH_ACTOR_ID = "apify~instagram-search-scraper"
DEFAULT_HASHTAG_ACTOR_ID = "apify~instagram-hashtag-scraper"
API_BASE = "https://api.apify.com/v2/actors"
CONNECTION_TEST_HANDLE = "humansofny"


class ApifyError(RuntimeError):
    """Raised when Apify cannot return a usable profile dataset."""


class ApifyTransientError(ApifyError):
    """Raised for retryable Apify or network failures."""


def _is_transient_http_error(code: int, message: str) -> bool:
    """Treat Actor-level timeouts as retryable even when Apify returns 400."""

    normalized = " ".join(str(message or "").casefold().replace("_", "-").split())
    timeout_markers = ("timed-out", "timed out", "timeout", "time-out")
    return int(code) in {408, 425, 429, 500, 502, 503, 504} or any(
        marker in normalized for marker in timeout_markers
    )


def _http_error_message(code: int, detail: str) -> str:
    guidance = {
        400: "요청 형식이 올바르지 않습니다.",
        401: "APIFY_TOKEN이 유효하지 않습니다. Personal API token을 다시 확인하세요.",
        402: "Apify 사용 크레딧 또는 결제 한도를 확인하세요.",
        403: "토큰에 Actor 실행 권한이 없습니다.",
        404: "Instagram Profile Scraper Actor를 찾을 수 없습니다.",
        429: "Apify 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
    }.get(int(code), "Apify가 요청을 거부했습니다.")
    cleaned = " ".join(str(detail or "").split())[:300]
    suffix = f" · {cleaned}" if cleaned else ""
    return f"Apify 요청 실패 (HTTP {code}) · {guidance}{suffix}"


def _clean_handles(handles: list[str]) -> list[str]:
    cleaned = []
    for value in handles:
        handle = str(value or "").strip().lstrip("@").casefold()
        if handle and handle not in cleaned:
            cleaned.append(handle)
    return cleaned


def _clean_terms(values: list[str], limit: int = 12) -> list[str]:
    cleaned = []
    for value in values:
        term = " ".join(str(value or "").replace("#", "").split()).strip(" ,")
        if term and term.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(term)
        if len(cleaned) >= max(1, int(limit)):
            break
    return cleaned


def _run_actor_sync(
    actor_id: str,
    payload: dict,
    token: str | None = None,
    timeout: int = 120,
    max_items: int = 100,
    max_charge_usd: float = 1.0,
    opener=None,
) -> list[dict]:
    """Run one Actor synchronously while keeping the API token out of URLs."""

    api_token = (token or os.getenv("APIFY_TOKEN") or "").strip()
    if not api_token:
        raise ApifyError("APIFY_TOKEN 환경변수가 없습니다.")
    actor = str(actor_id or "").strip().replace("/", "~")
    query = urllib.parse.urlencode({
        "format": "json",
        "clean": "1",
        "timeout": str(max(30, min(int(timeout), 290))),
        "maxItems": str(max(1, min(int(max_items), 1000))),
        "maxTotalChargeUsd": f"{max(0.10, float(max_charge_usd)):.2f}",
    })
    url = f"{API_BASE}/{urllib.parse.quote(actor, safe='~')}/run-sync-get-dataset-items?{query}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GLOEUM-Apify-Instagram/2.30.3",
        },
    )
    getter = opener or urllib.request.urlopen
    try:
        with getter(request, timeout=max(45, min(int(timeout) + 30, 320))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(4000).decode("utf-8", errors="replace")
            parsed = json.loads(detail)
            message = parsed.get("error", {}).get("message") or parsed.get("message") or detail
        except Exception:
            message = f"HTTP {exc.code}"
        safe_message = str(message).replace(api_token, "[REDACTED]")
        error_type = ApifyTransientError if _is_transient_http_error(exc.code, safe_message) else ApifyError
        raise error_type(_http_error_message(exc.code, safe_message)) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ApifyTransientError(f"Apify 연결 일시 지연: {str(exc)[:240]}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ApifyError(f"Apify 응답 JSON 해석 실패: {type(exc).__name__}") from None
    if not isinstance(data, list):
        raise ApifyError("Apify 응답이 목록 형식이 아닙니다.")
    return [item for item in data if isinstance(item, dict)]


def fetch_instagram_profiles(
    handles: list[str],
    token: str | None = None,
    actor_id: str | None = None,
    timeout: int = 120,
    max_charge_usd: float = 1.0,
    opener=None,
) -> list[dict]:
    """Run the official Instagram Profile Scraper and return dataset items.

    Authentication is sent in the Authorization header so the token does not
    appear in request URLs, logs, exported workbooks, or exception messages.
    """

    usernames = _clean_handles(handles)
    if not usernames:
        return []
    actor = (actor_id or os.getenv("APIFY_INSTAGRAM_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
    return _run_actor_sync(
        actor,
        {"usernames": usernames, "includeAboutSection": False},
        token=token,
        timeout=timeout,
        max_items=len(usernames),
        max_charge_usd=max_charge_usd,
        opener=opener,
    )


def fetch_instagram_search_profiles(
    search_terms: list[str],
    token: str | None = None,
    actor_id: str | None = None,
    search_limit: int = 12,
    timeout: int = 120,
    opener=None,
) -> list[dict]:
    """Discover public Instagram profiles from Instagram's own user search."""

    terms = _clean_terms(search_terms)
    if not terms:
        return []
    per_term = max(1, min(int(search_limit), 40))
    actor = actor_id or os.getenv("APIFY_INSTAGRAM_SEARCH_ACTOR_ID") or DEFAULT_SEARCH_ACTOR_ID
    return _run_actor_sync(
        actor,
        {
            "search": ", ".join(terms),
            "searchType": "user",
            "searchLimit": per_term,
            "enhanceUserSearchWithFacebookPage": False,
            "liveSearch": False,
        },
        token=token,
        timeout=timeout,
        max_items=len(terms) * per_term,
        max_charge_usd=max(0.25, len(terms) * per_term * 0.004),
        opener=opener,
    )


def fetch_instagram_hashtag_posts(
    hashtags: list[str],
    token: str | None = None,
    actor_id: str | None = None,
    results_limit: int = 12,
    timeout: int = 120,
    opener=None,
) -> list[dict]:
    """Discover recent public post owners from tightly scoped hashtags."""

    tags = _clean_terms(hashtags, limit=10)
    if not tags:
        return []
    per_tag = max(1, min(int(results_limit), 30))
    actor = actor_id or os.getenv("APIFY_INSTAGRAM_HASHTAG_ACTOR_ID") or DEFAULT_HASHTAG_ACTOR_ID
    return _run_actor_sync(
        actor,
        {"hashtags": tags, "resultsType": "posts", "resultsLimit": per_tag},
        token=token,
        timeout=timeout,
        max_items=len(tags) * per_tag,
        max_charge_usd=max(0.25, len(tags) * per_tag * 0.004),
        opener=opener,
    )


def check_apify_connection(
    token: str | None = None,
    actor_id: str | None = None,
    test_handle: str = CONNECTION_TEST_HANDLE,
    opener=None,
) -> dict:
    """Run one public profile through the real Actor and return safe diagnostics."""

    started = time.perf_counter()
    try:
        rows = fetch_instagram_profiles(
            [test_handle],
            token=token,
            actor_id=actor_id,
            timeout=120,
            max_charge_usd=0.25,
            opener=opener,
        )
    except ApifyError as exc:
        return {
            "ok": False,
            "stage": "actor_run",
            "message": str(exc),
            "result_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "stage": "actor_run",
            "message": f"Apify 연결 실패 · {type(exc).__name__}: {str(exc)[:240]}",
            "result_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    if not rows:
        return {
            "ok": False,
            "stage": "actor_result",
            "message": "Actor 실행은 완료됐지만 테스트 프로필 결과가 비어 있습니다.",
            "result_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    returned = str(rows[0].get("username") or "").strip().casefold()
    expected = str(test_handle).strip().lstrip("@").casefold()
    if returned != expected:
        return {
            "ok": False,
            "stage": "actor_result",
            "message": f"Actor는 실행됐지만 계정명이 일치하지 않습니다: {returned or '미확인'}",
            "result_count": len(rows),
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    return {
        "ok": True,
        "stage": "complete",
        "message": "토큰 인증과 Instagram Profile Scraper 실행이 모두 정상입니다.",
        "result_count": len(rows),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }


def fetch_instagram_profiles_batched(
    handles: list[str],
    token: str | None = None,
    actor_id: str | None = None,
    batch_size: int = 40,
    progress=None,
    timeout: int = 120,
    retry_delay: float = 1.0,
) -> list[dict]:
    """Fetch bounded batches and preserve completed work after a late timeout."""

    usernames = _clean_handles(handles)
    size = max(1, min(int(batch_size), 40))
    output: list[dict] = []
    total_batches = (len(usernames) + size - 1) // size
    for index in range(0, len(usernames), size):
        batch = usernames[index:index + size]
        batch_no = index // size + 1
        if progress:
            progress(batch_no - 1, max(total_batches, 1), f"Apify 원본 프로필 검증 {batch_no}/{total_batches}")
        try:
            rows = fetch_instagram_profiles(
                batch, token=token, actor_id=actor_id, timeout=timeout,
            )
        except ApifyTransientError:
            if progress:
                progress(batch_no - 1, max(total_batches, 1), f"Apify 일시 지연 · {batch_no}/{total_batches} 1회 재시도")
            time.sleep(max(0.0, min(float(retry_delay), 3.0)))
            try:
                rows = fetch_instagram_profiles(
                    batch, token=token, actor_id=actor_id, timeout=timeout,
                )
            except ApifyTransientError:
                if not output:
                    raise
                # Do not discard earlier successful batches. Missing handles
                # are converted to review rows by the collection layer.
                rows = []
        output.extend(rows)
    if progress:
        progress(total_batches, max(total_batches, 1), "Apify 원본 프로필 검증 완료")
    return output
