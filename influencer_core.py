"""Public-search based Instagram creator discovery and outreach drafting.

This module never logs in to Instagram, fetches follower lists, bypasses access
controls, or sends messages.  It converts public search-engine results into a
review queue and prepares drafts for a human operator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import hashlib
import ipaddress
import math
import re
import socket
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
import urllib.robotparser

from apify_instagram_client import (
    ApifyError,
    ApifyTransientError,
    fetch_instagram_hashtag_posts,
    fetch_instagram_profiles_batched,
    fetch_instagram_search_profiles,
)
from country_catalog import COUNTRIES
from instagram_apify_verifier import verify_profile as verify_vietnam_beauty_profile
from version import APP_VERSION, SCHEMA_VERSION


CATEGORY_KEYWORDS = {
    "뷰티·화장품": ["beauty", "skincare", "makeup", "cosmetics", "K-beauty"],
    "패션·잡화": ["fashion", "style", "accessories", "streetwear", "K-fashion"],
    "식품·건강": ["food", "wellness", "healthy lifestyle", "nutrition", "K-food"],
    "육아·키즈": ["parenting", "mom creator", "family lifestyle", "kids products"],
    "리빙·라이프스타일": ["lifestyle", "home decor", "living", "daily life"],
    "여행·지역": ["travel", "local guide", "travel creator", "city guide"],
}

COUNTRY_LOCAL_TERMS = {
    "Vietnam": ["Việt Nam", "Vietnam", "beauty blogger Vietnam", "KOC Vietnam"],
    "Thailand": ["Thailand", "Thai creator", "บิวตี้บล็อกเกอร์"],
    "Indonesia": ["Indonesia", "Indonesian creator", "beauty blogger Indonesia"],
    "Malaysia": ["Malaysia", "Malaysian creator"],
    "Philippines": ["Philippines", "Filipino creator"],
    "Japan": ["Japan", "Japanese creator", "美容インフルエンサー"],
    "United States": ["United States", "US creator"],
}

INSTAGRAM_DISCOVERY_TERMS = {
    "Vietnam": {
        "뷰티·화장품": [
            "Vietnam beauty blogger", "Vietnam beauty creator", "Vietnam KOC beauty",
            "review mỹ phẩm", "beauty blogger Hồ Chí Minh", "beauty blogger Hà Nội",
            "skincare reviewer Vietnam", "makeup reviewer Vietnam",
        ],
        "패션·잡화": ["Vietnam fashion blogger", "Vietnam fashion creator", "Vietnam KOC fashion"],
        "식품·건강": ["Vietnam food blogger", "Vietnam wellness creator", "Vietnam KOC food"],
        "육아·키즈": ["Vietnam mom blogger", "Vietnam parenting creator", "Vietnam family creator"],
        "리빙·라이프스타일": ["Vietnam lifestyle blogger", "Saigon lifestyle creator", "Hanoi lifestyle creator"],
        "여행·지역": ["Vietnam travel blogger", "Saigon travel creator", "Hanoi travel creator"],
    },
}

INSTAGRAM_DISCOVERY_HASHTAGS = {
    "Vietnam": {
        "뷰티·화장품": [
            "beautybloggervietnam", "kocvietnam", "reviewmypham", "myphamvietnam",
            "skincarevietnam", "makeupvietnam", "lamdep", "chamsocda",
        ],
        "패션·잡화": ["fashionvietnam", "vietnamfashion", "ootdvietnam", "kocvietnam"],
        "식품·건강": ["foodbloggervietnam", "healthyvietnam", "kocvietnam"],
        "육아·키즈": ["mombloggervietnam", "mevabe", "giadinhvietnam"],
        "리빙·라이프스타일": ["lifestylevietnam", "saigonlife", "hanoilife"],
        "여행·지역": ["travelvietnam", "vietnamtravel", "saigontravel", "hanoitravel"],
    },
}

# Only strong, public location signals are used for country verification.
# Product-origin words such as "Korean" are intentionally excluded because a
# Vietnam creator can legitimately mention K-beauty without being in Korea.
COUNTRY_IDENTITY_TERMS = {
    "Vietnam": ("vietnam", "viet nam", "việt nam", "vietnamese", "🇻🇳"),
}

# Repeat collections commonly rediscover the same public profiles.  Keep a
# short-lived in-process cache so a Render worker does not charge Apify again
# for unchanged handles during the same operating day.
_APIFY_PROFILE_CACHE: dict[str, tuple[float, dict]] = {}
_APIFY_CACHE_TTL_SECONDS = 24 * 60 * 60

# City/address signals can establish operating location. Country names, flags,
# nationality and ethnicity are only weak identity signals and never establish
# location by themselves (for example, "Vietnamese American" or 🇻🇳).
COUNTRY_LOCATION_TERMS = {
    "Vietnam": ("hanoi", "hà nội", "ho chi minh", "hồ chí minh", "hcmc", "saigon", "sài gòn", "tp.hcm", "da nang", "đà nẵng", "hai phong", "hải phòng", "can tho", "cần thơ", "nha trang", "hue", "huế"),
    "Thailand": ("thailand", "bangkok", "chiang mai", "🇹🇭"),
    "Indonesia": ("indonesia", "jakarta", "bali", "surabaya", "bandung", "🇮🇩"),
    "Malaysia": ("malaysia", "kuala lumpur", "🇲🇾"),
    "Singapore": ("singapore", "🇸🇬"),
    "Philippines": ("philippines", "manila", "cebu", "🇵🇭"),
    "Japan": ("japan", "tokyo", "osaka", "🇯🇵"),
    "China": ("china", "beijing", "shanghai", "🇨🇳"),
    "Taiwan": ("taiwan", "taipei", "🇹🇼"),
    "Hong Kong": ("hong kong", "🇭🇰"),
    "India": ("india", "new delhi", "delhi", "mumbai", "pune", "maharashtra", "chennai", "bangalore", "bengaluru", "ranchi", "coimbatore", "nagercoil", "pondicherry", "puducherry", "kolkata", "hyderabad", "🇮🇳"),
    "South Korea": ("south korea", "seoul", "busan", "대한민국", "서울", "🇰🇷"),
    "United States": ("united states", "u.s.a", "usa", "new york", "los angeles", "cincinnati", "washington dc", "washington, dc", "richmond, va", "rva | dc", "🇺🇸"),
    "Canada": ("canada", "toronto", "vancouver", "🇨🇦"),
    "Mexico": ("mexico", "méxico", "mexico city", "ciudad de méxico", "🇲🇽"),
    "United Kingdom": ("united kingdom", "england", "london", "manchester", "🇬🇧"),
    "Germany": ("germany", "deutschland", "berlin", "hamburg", "🇩🇪"),
    "France": ("france", "paris", "🇫🇷"),
    "Italy": ("italy", "italia", "milan", "rome", "🇮🇹"),
    "Spain": ("spain", "españa", "madrid", "barcelona", "🇪🇸"),
    "Netherlands": ("netherlands", "amsterdam", "rotterdam", "🇳🇱"),
    "United Arab Emirates": ("united arab emirates", "dubai", "abu dhabi", "🇦🇪"),
    "Brazil": ("brazil", "brasil", "são paulo", "rio de janeiro", "🇧🇷"),
    "Australia": ("australia", "sydney", "melbourne", "🇦🇺"),
    "Egypt": ("egypt", "egyptian", "cairo", "alexandria", "🇪🇬"),
    "Turkey": ("turkey", "türkiye", "turkish", "istanbul", "ankara", "🇹🇷"),
    "Finland": ("finland", "helsinki", "espoo", "tampere", "🇫🇮"),
    "Belgium": ("belgium", "belgique", "belgië", "brussels", "bruxelles", "🇧🇪"),
    "Colombia": ("colombia", "bogotá", "bogota", "medellín", "medellin", "🇨🇴"),
    "Guam": ("guam", "agana", "hagåtña", "hagatna", "micronesia mall", "🇬🇺"),
    "Pakistan": ("pakistan", "karachi", "lahore", "islamabad", "🇵🇰"),
    "Bangladesh": ("bangladesh", "dhaka", "🇧🇩"),
    "Nigeria": ("nigeria", "lagos", "abuja", "🇳🇬"),
}

US_STATE_RE = re.compile(
    r"(?i)\b(?:[a-z][a-z .'-]{1,35}),\s*"
    r"(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
    r"(?:\s+|,\s*)\d{5}(?:-\d{4})?\b"
)

INFLUENCER_TERMS = (
    "influencer", "kol", "beauty influencer", "fashion influencer",
    "lifestyle influencer", "travel influencer",
)
KOC_REVIEWER_TERMS = (
    "koc", "reviewer", "beauty reviewer", "skincare reviewer", "product reviewer",
    "beauty blogger", "review mỹ phẩm", "blogger", "vlogger",
)
PROFESSIONAL_CREATOR_TERMS = (
    "makeup artist", "make-up artist", "mua", "hairstylist", "hair stylist",
    "nail artist", "photographer", "stylist",
)
GENERIC_CREATOR_TERMS = (
    "content creator", "digital creator", "ugc creator", "beauty creator",
    "lifestyle creator", "travel creator", "creator",
)
CREATOR_TERMS = tuple(dict.fromkeys(
    INFLUENCER_TERMS + KOC_REVIEWER_TERMS
    + PROFESSIONAL_CREATOR_TERMS + GENERIC_CREATOR_TERMS
))
COLLAB_TERMS = (
    "collab", "collaboration", "booking", "business inquiries", "business enquiry",
    "pr contact", "work with me", "brand partnership", "ugc", "review",
)
BUSINESS_TERMS = (
    "official store", "official shop", "company", "corporation", "clinic", "hospital",
    "shopping mall", "wholesale", "manufacturer", "distributor", "brand official", "official brand",
)
BUSINESS_HANDLE_MARKERS = (
    "shop", "store", "official", "clinic", "salon", "spa", "nails", "nail.",
    "beautycenter", "beauty_center", "beautyclinic", "beauty_clinic", "cosmetics",
    "skincareclinic", "skincare_clinic", "aesthetic", "academy", "studio",
    "institute", "medical", "skinlab", "beauty_concept", "lashes",
    "beautygroup", "beauty_group", "koreanskincare", "skincarevietnam",
    "_influencers", ".influencers", "boutique", "artmarket", "art_market",
)
KNOWN_NON_CREATOR_HANDLES = {
    "anhbeautyvietnam", "bombeautyvn", "cocoon.vietnam", "destinationvn",
    "facesofvietnam", "focalluremakeup_vn", "haniebeauty.vn", "kcobeauty",
    "kocvietnam", "koithevietnam", "miss_saigon_the_beauty_concept",
    "nomamedical", "saigon.beauty", "saigon_beauty", "saigon_beautyhelsinki",
    "saigonbeauty_", "saigonbeauty_sb", "skinlabbyk.thaodien",
    "the.beauty.influencers", "vietnamesebeauty", "yennhimakeup",
    "kscreator_", "marlys.beauty", "kol_beautysg", "sknguam",
    "kimbeautygroup", "paulaschoice.vn", "collagebeautyskin",
    "kiehlscolombia", "rosekoreanbeauty", "beautral.vn",
    "simpleskincarevietnam", "vesta.lifestyle", "pureglow.koreanskincare",
    "simplevietnam", "miss.hanoi55",
    "whereinhcmc", "vietqmedia", "soulvenir.co", "locoartmarket",
    "casetify.vietnam", "lsrhoavuduong", "saigon.social",
    "ms_saigon_beauty_boutique",
    "beautygarden.vn", "swe.vn", "sunnyson.vn", "kirobeauty",
    "kosallure", "kkwbeauty", "skincareedit",
}
ROLE_TERMS = {
    "상점·판매계정": (
        "official store", "official shop", "online store", "online shop",
        "beauty store", "beauty shop", "cosmetics store", "cosmetics shop",
        "tienda", "tienda en línea", "shoppe", "shopping mall", "wholesale",
        "retailer", "showroom", "envíos", "plaza", "order now", "đặt hàng",
        "mua ngay", "giao hàng", "free shipping", "reservation", "đặt lịch",
        "book an appointment", "appointments", "open daily", "hotline",
        "open hours", "opening hours", "flower designer",
        "beli sekarang", "harga", "order via", "whatsapp order",
        "shopping center", "shopping centre", "shopping mall", "shipping to",
        "worldwide shipping", "based in 🇧🇪", "grosir", "ecer", "jam operasional",
        "product viral", "retail & wholesale", "gifts", "gift shop", "stockist",
        "gift store", "art market", "beauty boutique", "freeship", "worldwide shipping",
        "workshops & tastings", "workshops and tastings", "info & bookings",
        "hệ thống phân phối", "he thong phan phoi", "cửa hàng", "cua hang",
        "mỹ phẩm chính hãng", "my pham chinh hang",
    ),
    "브랜드·기업계정": (
        "official account", "official global", "global official", "official page", "brand official",
        "official brand", "global brand", "trang chính thức", "trang chinh thuc",
        "beauty brand", "skincare brand", "cosmetics brand", "mask brand", "company",
        "corporation", "manufacturer", "distributor", "serial entrepreneur",
        "official website", "vat:", "thương hiệu", "nhãn hiệu", "công ty", "chính thức",
        "made in vietnam", "proudly made", "perfume brand", "fragrance brand", "tea brand",
        "our products", "products all designed", "global brand", "thương hiệu toàn cầu",
        "официальный аккаунт бренда", "косметический бренд",
        "research-backed skincare", "skincare since", "healthy skin for all",
        "our official", "global skincare", "authorized retailer",
        "official vietnam", "vietnam official", "factory", "our collection",
        "100% vegan", "peta approved", "plastic neutral",
    ),
    "미디어계정": (
        "magazine", "media company", "media platform", "publication", "editorial",
        "news media", "men's folio", "men’s folio", "mens folio", "pageantry",
        "beauty pageant", "pageant news", "beauty queen news", "creator community",
        "chương trình thực tế", "dành cho các nhà sáng tạo", "livestream bán hàng",
        "culture platform", "cultural publication", "news platform", "directory",
        "city guide", "local guide", "curating & sharing", "curating and sharing",
        "international community", "lifestyle and events", "events and more",
    ),
    "대행사·서비스계정": (
        "influencer agency", "influencer marketing", "talent agency",
        "talent management", "creator agency", "influencer job services",
        "influencer jobs", "marketing agency", "financial and tax assistance",
        "kol agency", "koc agency", "campaign agency", "agency & endorsement",
        "campaign, kol", "endorsement agency", "creator management agency",
    ),
    "클리닉·살롱계정": (
        "beauty clinic", "skin clinic", "aesthetic clinic", "clinic surgery",
        "medical spa", "med spa", "beauty salon", "hair salon", "nail salon",
        "beauty spa", "day spa", "hair spa", "spa &", "spa |",
        "plastic surgery", "cosmetic surgery", "surgeon", "isaps",
        "phẫu thuật thẩm mỹ", "phòng khám", "thẩm mỹ viện", "spa làm đẹp",
        "beauty center", "beauty centre", "nail studio", "nail bar",
        "hair studio", "skin center", "skin centre", "dịch vụ làm đẹp",
        "chăm sóc da", "điều trị da", "nail art service",
        "beauty institute", "한국뷰티센터", "lash lift", "brow lamination",
        "microblading", "lip blushing", "botox", "filler", "makeup studio",
        "sugarwaxing", "rakennekynnet", "geelilakkaukset", "ripsienpidennykset",
        "ajanvaraus", "beauty medical", "medical aesthetics",
        "beauty & clinic", "spa - beauty", "spa & clinic", "whitening", "massage",
    ),
    "큐레이션·리포스트계정": (
        "all girls posted", "feature page", "community page", "repost page",
        "daily features", "submit your photo", "tag us to be featured",
    ),
    "커뮤니티·앰배서더 프로그램계정": (
        "ambassador program", "join our ambassador", "join the ambassador",
        "chance to be featured", "creator community", "influencer community",
        "your k-beauty squad", "our k-beauty squad",
    ),
}
RESERVED_PATHS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "about", "developer", "legal", "press", "web", "challenge", "api", "privacy",
    "popular",
}
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,24})(?![\w.-])")
FOLLOWER_RE = re.compile(
    r"(?i)(\d{1,3}(?:[.,]\d{1,3})?|\d{1,9})\s*([kmb]?)\s*"
    r"(?:followers?|seguidores|팔로워|người\s+theo\s+dõi|pengikut|ผู้ติดตาม)"
)


@dataclass
class InfluencerConfig:
    country: str
    category: str
    keywords: list[str]
    target_count: int = 20
    min_followers: int = 0
    max_followers: int = 0
    max_queries: int = 20
    request_delay: float = 0.2
    include_business_accounts: bool = False
    verify_original_profiles: bool = True
    verify_external_evidence: bool = False
    max_external_pages: int = 2
    search_provider: str = "Brave"
    use_apify_verification: bool = False
    apify_batch_size: int = 40
    use_apify_discovery: bool = False
    use_web_fallback: bool = True
    apify_search_limit: int = 12
    apify_hashtag_limit: int = 10
    max_runtime_seconds: int = 330


class _ProfileMetaParser(HTMLParser):
    """Read only public page metadata; scripts, posts and follower lists are ignored."""
    def __init__(self):
        super().__init__()
        self.meta={}; self.canonical=""; self.title=""; self._in_title=False

    def handle_starttag(self, tag, attrs):
        values={str(k).casefold():str(v or "") for k,v in attrs}
        if tag.casefold()=="meta":
            key=(values.get("property") or values.get("name") or "").casefold()
            if key and values.get("content"):
                self.meta[key]=values["content"]
        elif tag.casefold()=="link" and "canonical" in values.get("rel","").casefold():
            self.canonical=values.get("href","")
        elif tag.casefold()=="title":
            self._in_title=True

    def handle_endtag(self, tag):
        if tag.casefold()=="title":
            self._in_title=False

    def handle_data(self, data):
        if self._in_title:
            self.title+=str(data)


class _EvidenceHTMLParser(HTMLParser):
    """Extract visible public text from a secondary evidence page."""
    def __init__(self):
        super().__init__(); self.text=[]; self._hidden=0

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in {"script","style","noscript","svg","template"}:
            self._hidden+=1

    def handle_endtag(self, tag):
        if tag.casefold() in {"script","style","noscript","svg","template"}:
            self._hidden=max(0,self._hidden-1)

    def handle_data(self, data):
        if not self._hidden:
            value=" ".join(str(data).split())
            if value:
                self.text.append(value)


def _plain(value: object, limit: int = 600) -> str:
    text=unicodedata.normalize("NFC",unescape(str(value or "")))
    text=re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _public_http_url(url: str, resolve_host: bool=True) -> bool:
    """Allow only public HTTP(S) evidence URLs; block local/private networks."""
    try:
        parsed=urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http","https"} or not parsed.hostname:
        return False
    host=parsed.hostname.casefold().rstrip(".")
    if host in {"localhost","localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        direct=ipaddress.ip_address(host)
        if not direct.is_global:
            return False
    except ValueError:
        if resolve_host:
            try:
                addresses={item[4][0] for item in socket.getaddrinfo(host,parsed.port or 443,type=socket.SOCK_STREAM)}
            except OSError:
                return False
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                return False
    return True


def _robots_allows(url: str) -> bool:
    try:
        parsed=urllib.parse.urlsplit(url)
        robots_url=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,"/robots.txt","",""))
        parser=urllib.robotparser.RobotFileParser(); parser.set_url(robots_url); parser.read()
        return parser.can_fetch(f"GLOEUM-PublicEvidenceVerifier/{APP_VERSION}",url)
    except Exception:
        return True


class _SafeEvidenceRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute=urllib.parse.urljoin(req.full_url,newurl)
        if not _public_http_url(absolute,resolve_host=True):
            raise urllib.error.HTTPError(absolute,403,"Unsafe redirect blocked",headers,fp)
        return super().redirect_request(req,fp,code,msg,headers,absolute)


def _default_evidence_fetcher(url: str, headers: dict, timeout: float):
    request=urllib.request.Request(url,headers=headers)
    opener=urllib.request.build_opener(_SafeEvidenceRedirectHandler())
    with opener.open(request,timeout=timeout) as response:
        content_type=str(response.headers.get("Content-Type") or "").casefold()
        if "html" not in content_type and "text" not in content_type:
            return _ProfileHTTPResponse(response.status,"",response.geturl())
        data=response.read(600_000)
        charset=response.headers.get_content_charset() or "utf-8"
        return _ProfileHTTPResponse(response.status,data.decode(charset,errors="replace"),response.geturl())


def fetch_public_evidence_page(url: str, handle: str, fetcher=None, timeout: float=8.0) -> dict:
    """Fetch one exact-handle secondary page without login or access bypass."""
    base={"url":url,"status":"실패","http_status":"","text":"","reason":""}
    if not _public_http_url(url,resolve_host=fetcher is None):
        return {**base,"reason":"공개 URL 아님"}
    host=(urllib.parse.urlsplit(url).hostname or "").casefold()
    if host.endswith("instagram.com"):
        return {**base,"reason":"Instagram 원본은 별도 검증"}
    if fetcher is None and not _robots_allows(url):
        return {**base,"reason":"robots.txt 비허용"}
    getter=fetcher or _default_evidence_fetcher
    try:
        response=getter(
            url,
            headers={"User-Agent":f"GLOEUM-PublicEvidenceVerifier/{APP_VERSION}","Accept":"text/html,text/plain"},
            timeout=timeout,
        )
    except Exception as exc:
        return {**base,"reason":f"{type(exc).__name__}: {str(exc)[:120]}"}
    status=int(getattr(response,"status_code",0) or 0)
    final_url=str(getattr(response,"url",url) or url)
    result={**base,"url":final_url,"http_status":status}
    if status<200 or status>=400:
        return {**result,"reason":f"HTTP {status}"}
    if not _public_http_url(final_url,resolve_host=fetcher is None):
        return {**result,"reason":"리디렉션 대상이 공개 URL 아님"}
    body=str(getattr(response,"text","") or "")[:600_000]
    parser=_EvidenceHTMLParser()
    try:
        parser.feed(body)
        text=_plain(" ".join(parser.text),5000)
    except Exception:
        text=_plain(body,5000)
    normalized_handle=str(handle or "").casefold().lstrip("@")
    if not normalized_handle or normalized_handle not in text.casefold():
        return {**result,"reason":"정확한 계정명 근거 없음"}
    return {**result,"status":"확인","text":text,"reason":""}


def default_keywords(category: str) -> list[str]:
    return list(CATEGORY_KEYWORDS.get(category, ["creator", "reviewer", "influencer"]))


def build_instagram_discovery_terms(config: InfluencerConfig) -> list[str]:
    """Return compact Instagram-native search terms, local language first."""
    curated=INSTAGRAM_DISCOVERY_TERMS.get(config.country,{}).get(config.category,[])
    generated=[]
    for keyword in list(config.keywords)+default_keywords(config.category):
        value=" ".join(str(keyword or "").split())
        if value:
            generated.extend((f"{config.country} {value} creator",f"{config.country} {value} reviewer"))
    terms=[]
    for value in list(curated)+generated:
        cleaned=" ".join(str(value or "").replace("#","").split()).strip()
        if cleaned and cleaned.casefold() not in {term.casefold() for term in terms}:
            terms.append(cleaned)
    return terms[:10]


def build_instagram_discovery_hashtags(config: InfluencerConfig) -> list[str]:
    curated=INSTAGRAM_DISCOVERY_HASHTAGS.get(config.country,{}).get(config.category,[])
    tags=[]
    for value in curated:
        cleaned=re.sub(r"[^0-9a-zA-Z_À-ỹ]","",str(value or "").lstrip("#"))
        if cleaned and cleaned.casefold() not in {tag.casefold() for tag in tags}:
            tags.append(cleaned)
    return tags[:8]


def _first_value(record: dict, *keys: str):
    for key in keys:
        value=record.get(key)
        if value not in (None,"",[],{}):
            return value
    return ""


def _discovery_handle(record: dict) -> str:
    direct=_first_value(record,"username","ownerUsername","owner_username","userName")
    if isinstance(direct,dict):
        direct=_first_value(direct,"username","userName")
    if not direct:
        owner=record.get("owner") or record.get("user") or {}
        if isinstance(owner,dict):
            direct=_first_value(owner,"username","userName")
    handle=str(direct or "").strip().lstrip("@").casefold()
    if re.fullmatch(r"[a-z0-9._]{1,30}",handle):
        return handle
    for key in ("profileUrl","profile_url","url","inputUrl"):
        parsed=normalize_handle(str(record.get(key) or ""))
        if parsed:
            return parsed
    return ""


def discovery_item_to_search_result(record: dict, source_type: str, fallback_term: str="") -> dict | None:
    """Normalize Search/Hashtag Actor rows into the existing candidate parser."""
    handle=_discovery_handle(record)
    if not handle:
        return None
    full_name=_plain(_first_value(record,"fullName","full_name","ownerFullName","owner_full_name","name"),120)
    biography=_plain(_first_value(record,"biography","bio","description"),500)
    business_category=_plain(_first_value(record,"businessCategoryName","business_category_name","category"),120)
    location_parts=[]
    for key in ("locationName","location_name","locationCity","location_city","city","addressStreet","address_street"):
        value=_plain(record.get(key),120)
        if value and value not in location_parts:
            location_parts.append(value)
    captions=[]
    direct_caption=_plain(_first_value(record,"caption","text"),500)
    if direct_caption:
        captions.append(direct_caption)
    for post in (record.get("latestPosts") or record.get("latest_posts") or [])[:12]:
        if isinstance(post,dict):
            caption=_plain(_first_value(post,"caption","text"),300)
            if caption:
                captions.append(caption)
    followers=_first_value(record,"followersCount","followers_count","followers")
    follower_text=""
    try:
        follower_text=f"{int(followers):,} followers"
    except (TypeError,ValueError):
        pass
    search_term=_plain(_first_value(record,"searchTerm","search_term"),160) or fallback_term
    content=" · ".join(part for part in (
        biography,business_category," ".join(location_parts),follower_text," ".join(captions[:6])
    ) if part)
    original_url=str(_first_value(record,"url","postUrl","post_url","profileUrl","profile_url") or "")
    return {
        "title":f"{full_name or '@'+handle} (@{handle}) · Instagram",
        "url":profile_url(handle),
        "content":content,
        "description":content,
        "instagram_discovery_source":source_type,
        "instagram_discovery_term":search_term,
        "instagram_discovery_original_url":original_url,
    }


def normalize_handle(url: str) -> str:
    """Return an Instagram profile handle, never a post/reel/system path."""
    try:
        parsed=urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    host=(parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"instagram.com", "m.instagram.com"}:
        return ""
    parts=[part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    handle=parts[0].lstrip("@").lower()
    if handle in RESERVED_PATHS or not re.fullmatch(r"[a-z0-9._]{1,30}", handle):
        return ""
    return handle


def profile_url(handle: str) -> str:
    return f"https://www.instagram.com/{handle}/" if handle else ""


def parse_follower_count(text: str) -> int | None:
    match=FOLLOWER_RE.search(_plain(text, 1200))
    if not match:
        return None
    raw=match.group(1); suffix=match.group(2).lower()
    if suffix:
        number=float(raw.replace(",", "."))
    else:
        number=float(raw.replace(",", ""))
    multiplier={"":1, "k":1_000, "m":1_000_000, "b":1_000_000_000}[suffix]
    return int(number*multiplier)


def follower_label(value: int | None) -> str:
    if value is None:
        return "미확인"
    if value >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value/1_000:.1f}K"
    return f"{value:,}"


def extract_public_email(text: str) -> str:
    for email in EMAIL_RE.findall(_plain(text, 1600)):
        email=email.lower().strip(".,;:()[]{}<>")
        host=email.rsplit("@",1)[-1]
        if host not in {"instagram.com", "facebook.com", "example.com", "email.com"}:
            return email
    return ""


def clean_display_name(title: str, handle: str) -> str:
    text=_plain(title, 180)
    text=re.sub(r"(?i)\s*[·|\-–—]?\s*instagram(?:\s+photos\s+and\s+videos)?\s*$", "", text)
    text=re.sub(rf"(?i)\s*\(@?{re.escape(handle)}\)\s*", " ", text)
    text=re.sub(r"(?i)^instagram\s*[:\-–—|]\s*", "", text)
    text=re.sub(r"\s+", " ", text).strip(" ·|-–—")
    return (text if 1 <= len(text) <= 80 else "") or f"@{handle}"


def _bio_evidence_text(snippet: str) -> str:
    """Prefer the quoted Instagram bio over follower boilerplate/account names."""
    text=_plain(snippet,1400)
    match=re.search(r"(?i)(?:on|en)\s+instagram\s*:\s*[\"“]?(.+)$",text)
    if match:
        return match.group(1).strip(' "“”')
    return text


def _has_explicit_location_context(text: str, country: str) -> bool:
    if _explicit_country_location_hits(text,country):
        return True
    plain=_plain(text,1800)
    return bool(re.search(r"(?i)(?:address|địa chỉ|dia chi|based|location|from)\s*[:：-]",plain))


def _term_pattern(term: str) -> re.Pattern:
    """Match terms as tokens so KOC never matches KOCO/KOCOSTAR."""
    normalized=term.casefold().strip()
    escaped=re.escape(normalized).replace(r"\ ", r"\s+")
    prefix=r"(?<![\w])" if normalized and normalized[0].isalnum() else ""
    suffix=r"(?![\w])" if normalized and normalized[-1].isalnum() else ""
    return re.compile(prefix+escaped+suffix,re.IGNORECASE)


def _term_hits(text: str, terms) -> list[str]:
    plain=_plain(text,1800).casefold()
    return [term for term in terms if _term_pattern(term).search(plain)]


def _country_terms(country: str) -> tuple[str, ...]:
    strong=list(COUNTRY_LOCATION_TERMS.get(country, (country,)))
    # Countries outside the curated map still receive conservative exact-name
    # matching. Short aliases such as US/UK/SG are not location proof.
    if country not in COUNTRY_LOCATION_TERMS and country in COUNTRIES:
        strong.extend(alias for alias in COUNTRIES[country][3] if len(alias)>=6)
    return tuple(dict.fromkeys(term for term in strong if term))


def _explicit_country_location_hits(text: str, country: str) -> list[str]:
    patterns=(
        rf"(?:based|located|living|working)\s+in\s+{re.escape(country)}",
        rf"{re.escape(country)}[-\s]+based",
        rf"(?:📍|location\s*[:：])\s*{re.escape(country)}",
    )
    plain=_plain(text,1800)
    return [m.group(0) for pattern in patterns for m in re.finditer(pattern,plain,re.IGNORECASE)]


def assess_country(text: str, target_country: str) -> dict:
    target_hits=_term_hits(text,COUNTRY_LOCATION_TERMS.get(target_country,()))
    target_hits+=_explicit_country_location_hits(text,target_country)
    if target_country=="Vietnam" and re.search(r"(?<!\d)\+?84[\s().-]?\d",_plain(text,1800)):
        target_hits.append("+84")
    identity_hits=_term_hits(text,COUNTRY_IDENTITY_TERMS.get(target_country,()))
    foreign={}
    for country,terms in COUNTRY_LOCATION_TERMS.items():
        if country==target_country:
            continue
        hits=_term_hits(text,terms)+_explicit_country_location_hits(text,country)
        if country=="Germany" and re.search(r"(?:@|https?://|www\.)[^\s]+\.de(?:\b|/)",_plain(text,1800),re.IGNORECASE):
            hits.append(".de")
        if hits:
            foreign[country]=hits
    if target_country != "United States":
        us_address=US_STATE_RE.search(_plain(text,1800))
        if us_address:
            foreign.setdefault("United States",[]).append(us_address.group(0))
    if target_country != "Germany" and re.search(r"(?i)(?<![a-z])impressum(?![a-z])",_plain(text,1800)):
        foreign.setdefault("Germany",[]).append("Impressum")
    foreign_evidence="; ".join(
        f"{country}: {', '.join(hits[:3])}" for country,hits in foreign.items()
    )
    if foreign:
        return {
            "country_status":"대상국가 불일치",
            "verified_country":"",
            "country_evidence":", ".join((target_hits+identity_hits)[:5]),
            "foreign_country_evidence":foreign_evidence,
        }
    if target_hits:
        return {
            "country_status":"확인됨",
            "verified_country":target_country,
            "country_evidence":", ".join(target_hits[:5]),
            "foreign_country_evidence":"",
        }
    return {
        "country_status":"위치 미확인",
        "verified_country":"",
        "country_evidence":", ".join(identity_hits[:5]),
        "foreign_country_evidence":"",
    }


def classify_profile_role(text: str, creator_hits: list[str], handle: str="") -> tuple[str,list[str]]:
    lowered_handle=handle.casefold()
    if lowered_handle in KNOWN_NON_CREATOR_HANDLES:
        return "브랜드·기업계정",["검증된 비크리에이터 계정"]
    handle_hits=[marker for marker in BUSINESS_HANDLE_MARKERS if marker in lowered_handle]
    if handle_hits:
        role="클리닉·살롱계정" if any(marker in lowered_handle for marker in ("clinic","salon","spa","nail","center","centre","aesthetic")) else "브랜드·기업계정"
        return role,[f"handle: {marker}" for marker in handle_hits[:3]]
    for role,terms in ROLE_TERMS.items():
        hits=_term_hits(text,terms)
        if hits:
            return role,hits
    influencer_hits=_term_hits(text,INFLUENCER_TERMS)
    if influencer_hits:
        return "인플루언서·KOL",influencer_hits
    reviewer_hits=_term_hits(text,KOC_REVIEWER_TERMS)
    if reviewer_hits:
        return "KOC·리뷰어",reviewer_hits
    professional_hits=_term_hits(text,PROFESSIONAL_CREATOR_TERMS)
    if professional_hits:
        return "전문 크리에이터",professional_hits
    generic_hits=_term_hits(text,GENERIC_CREATOR_TERMS)
    if generic_hits:
        return "개인 크리에이터",generic_hits
    return "크리에이터 후보",[]


def build_queries(config: InfluencerConfig) -> list[str]:
    country=config.country.strip()
    keywords=list(dict.fromkeys(k.strip() for k in config.keywords if k.strip()))
    local=COUNTRY_LOCAL_TERMS.get(country, [country])
    queries=[]
    exclude=""
    if country=="Vietnam":
        exclude='-"official store" -"online shop" -store -brand -company -clinic -salon -studio -agency -manufacturer -distributor -collective -network -"ambassador program" -"creator community"'
        queries.extend((
            f'site:instagram.com "Ho Chi Minh City" "beauty influencer" {exclude}',
            f'site:instagram.com HCMC "skincare reviewer" {exclude}',
            f'site:instagram.com Saigon "beauty blogger" {exclude}',
            f'site:instagram.com Hanoi "beauty influencer" {exclude}',
            f'site:instagram.com "Hà Nội" "beauty blogger" {exclude}',
            f'site:instagram.com "Việt Nam" "beauty blogger" {exclude}',
            f'site:instagram.com Vietnam KOC skincare {exclude}',
            f'site:instagram.com Vietnam KOL cosmetics {exclude}',
            f'site:instagram.com Vietnam "beauty content creator" {exclude}',
            f'site:instagram.com Vietnam "UGC creator" beauty {exclude}',
        ))
    for index,keyword in enumerate(keywords):
        locality=local[index % len(local)] if local else country
        queries.extend((
            f'site:instagram.com {locality} "{keyword}" influencer KOL {exclude}'.strip(),
            f'site:instagram.com {country} "{keyword}" reviewer KOC {exclude}'.strip(),
        ))
    queries.append(f'site:instagram.com {country} "{config.category}" influencer KOC KOL {exclude}'.strip())
    return list(dict.fromkeys(queries))[:max(1, int(config.max_queries))]


def _apify_profile_limit(config: InfluencerConfig, pool_count: int) -> int:
    """Verify a useful first tranche instead of always charging for 40 rows."""
    desired=max(20,int(config.target_count)*2)
    return min(max(0,int(pool_count)),desired,200)


def _cached_apify_profiles(handles: list[str]) -> tuple[list[dict],list[str]]:
    now=time.time(); cached=[]; missing=[]
    for handle in handles:
        key=str(handle or "").casefold().strip()
        item=_APIFY_PROFILE_CACHE.get(key)
        if item and now-item[0] <= _APIFY_CACHE_TTL_SECONDS:
            cached.append(dict(item[1]))
        else:
            _APIFY_PROFILE_CACHE.pop(key,None)
            missing.append(key)
    return cached,missing


def _store_apify_profiles(rows: list[dict]) -> None:
    stamp=time.time()
    for raw in rows:
        key=_apify_input_handle(raw) or str(raw.get("username") or "").casefold().strip()
        if key:
            _APIFY_PROFILE_CACHE[key]=(stamp,dict(raw))


def build_verification_query(record: dict, config: InfluencerConfig) -> str:
    """Verify identity, operating country, creator role and category together."""
    handle=str(record.get("handle") or "").strip()
    category_terms=list(dict.fromkeys(config.keywords+default_keywords(config.category)))[:4]
    category_clause=" OR ".join(f'"{term}"' if " " in term else term for term in category_terms)
    if config.country=="Vietnam":
        location_clause='Vietnam OR "Việt Nam" OR Hanoi OR "Ho Chi Minh" OR HCMC OR Saigon'
        role_clause='influencer OR KOL OR KOC OR blogger OR reviewer OR "content creator"'
    else:
        location_clause=f'"{config.country}"'
        role_clause='influencer OR creator OR blogger OR reviewer'
    return f'"{handle}" ({location_clause}) ({role_clause}) ({category_clause})'.strip()


def relax_query(query: str) -> str:
    """Create one broader retry without removing the target country or site."""
    relaxed=_plain(query,500).replace('"', "")
    replacements={"Việt Nam":"Vietnam", "Hà Nội":"Hanoi", "Hồ Chí Minh":"Ho Chi Minh", "Sài Gòn":"Saigon"}
    for source,target in replacements.items():
        relaxed=relaxed.replace(source,target)
    relaxed=re.sub(r"(?i)\b(?:influencer|KOL|KOC|reviewer)\b", "creator", relaxed)
    relaxed=re.sub(r"\bcreator(?:\s+creator)+\b", "creator", relaxed,flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", relaxed).strip()


def _candidate_from_result(item: dict, query: str, config: InfluencerConfig) -> tuple[dict | None, str]:
    url=str(item.get("url") or "")
    handle=normalize_handle(url)
    if not handle:
        return None, "Instagram 프로필 URL 아님"
    title=_plain(item.get("title"), 220)
    snippet=_plain(item.get("content") or item.get("description"), 900)
    combined=f"{title} {snippet} {handle}"
    bio_text=_bio_evidence_text(snippet)
    evidence_text=bio_text
    lowered=combined.casefold()
    if any(token in lowered for token in ("instagram login", "log in • instagram", "sign up • instagram")):
        return None, "로그인·시스템 페이지"

    creator_hits=_term_hits(combined, CREATOR_TERMS)
    category_terms=list(dict.fromkeys(config.keywords+default_keywords(config.category)))
    category_hits=_term_hits(combined, category_terms)
    collab_hits=_term_hits(combined, COLLAB_TERMS)
    role,role_hits=classify_profile_role(combined,creator_hits,handle)
    creator_roles={"인플루언서·KOL","KOC·리뷰어","전문 크리에이터","개인 크리에이터","크리에이터 후보"}
    is_non_creator=role not in creator_roles
    if is_non_creator and not config.include_business_accounts:
        return None, "브랜드·상점·미디어·대행사 계정"

    # A location word embedded only in a handle (for example saigon_beauty)
    # is not sufficient proof of where the account actually operates.
    country=assess_country(evidence_text,config.country)
    # Brand names such as "Saigon Beauty Helsinki" are not location proof.
    # A creator signal or explicit location expression must accompany the city.
    if (
        country["country_status"]=="확인됨"
        and role=="크리에이터 후보"
        and not _has_explicit_location_context(evidence_text,config.country)
    ):
        country={
            **country,
            "country_status":"위치 미확인",
            "verified_country":"",
            "country": "미확인",
        }
    if country["country_status"]=="대상국가 불일치":
        return None, "대상국가 불일치"
    followers=parse_follower_count(combined)
    if followers is not None and config.min_followers and followers < config.min_followers:
        return None, "최소 팔로워 미달"
    if followers is not None and config.max_followers and followers > config.max_followers:
        return None, "최대 팔로워 초과"

    email=extract_public_email(combined)
    score=10
    role_bonus={
        "인플루언서·KOL":30,
        "KOC·리뷰어":25,
        "개인 크리에이터":15,
        "전문 크리에이터":8,
        "크리에이터 후보":0,
    }.get(role,0)
    score+=role_bonus
    score+=min(20, len(category_hits)*7)
    score+=25 if country["country_status"]=="확인됨" else 0
    score+=15 if followers is not None and followers>=10_000 else 10 if followers is not None and followers>=1_000 else 0
    score+=10 if email else 0
    score+=min(5, len(collab_hits)*5)
    score-=30 if is_non_creator else 0
    score=max(0,min(100,score))
    verified_creator=bool(creator_hits) and role in creator_roles-{"크리에이터 후보"}
    verified_country=country["country_status"]=="확인됨"
    follower_eligible=isinstance(followers,int) and followers>=1_000
    category_eligible=bool(category_hits)
    collaboration_role=role in {"인플루언서·KOL","KOC·리뷰어","개인 크리에이터"}
    grade="A" if score>=75 and verified_creator and verified_country and follower_eligible and category_eligible and collaboration_role else "B" if score>=55 and verified_creator and verified_country and follower_eligible and category_eligible and collaboration_role else "C"
    matched=category_hits or [k for k in config.keywords if k.casefold() in query.casefold()]
    source_parts=[part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    source_type="게시물·릴스 검색결과" if len(source_parts)>1 and source_parts[1] in {"p","reel","reels","tv"} else "프로필 검색결과"
    return {
        "account_id":"INF-"+hashlib.sha256(handle.encode()).hexdigest()[:12].upper(),
        "handle":handle,
        "display_name":clean_display_name(title,handle),
        "profile_type":role,
        "role_evidence":", ".join(role_hits[:6]),
        "target_country":config.country,
        "verified_country":country["verified_country"],
        "country_status":country["country_status"],
        "country_evidence":country["country_evidence"],
        "foreign_country_evidence":country["foreign_country_evidence"],
        "country":country["verified_country"] or "미확인",
        "category":config.category,
        "category_keywords":category_terms,
        "instagram_url":profile_url(handle),
        "followers":followers if followers is not None else "",
        "followers_display":follower_label(followers),
        "follower_status":"검색결과 공개표시" if followers is not None else "추가확인",
        "follower_eligibility":"통과" if follower_eligible else "1,000명 미만" if isinstance(followers,int) else "팔로워 확인 필요",
        "public_email":email,
        "email_status":"공개 검색결과 확인" if email else "미확보",
        "bio_summary":snippet[:360],
        "matched_keywords":", ".join(list(dict.fromkeys(matched))[:6]),
        "creator_evidence":", ".join(list(dict.fromkeys(creator_hits+collab_hits))[:8]),
        "score":score,
        "grade":grade,
        "discovery_query":query,
        "source_url":url,
        "source_type":source_type,
        "source_title":title,
        "evidence_source_urls":[url],
        "identity_source_urls":[url],
        "country_source_urls":[url] if verified_country else [],
        "creator_source_urls":[url] if creator_hits else [],
        "category_source_urls":[url] if category_hits else [],
        "follower_source_urls":[url] if follower_eligible else [],
        "evidence_source_count":1,
        "identity_source_count":1,
        "country_source_count":1 if verified_country else 0,
        "creator_source_count":1 if creator_hits else 0,
        "category_source_count":1 if category_hits else 0,
        "follower_source_count":1 if follower_eligible else 0,
        "evidence_status":"단일 검색근거",
        "dm_status":"초안",
        "email_contact_status":"미발송",
        "collected_at":datetime.now(timezone.utc).isoformat(),
    }, ""


def _merge_evidence(existing: dict, candidate: dict) -> dict:
    for field in ("evidence_source_urls","identity_source_urls","country_source_urls","creator_source_urls","category_source_urls","follower_source_urls"):
        urls=[]
        for source in list(existing.get(field) or [])+list(candidate.get(field) or []):
            source=str(source or "").strip()
            if source and source not in urls:
                urls.append(source)
        existing[field]=urls
    _refresh_evidence_counts(existing)
    return existing


def _source_key(url: str) -> str:
    try:
        host=(urllib.parse.urlsplit(str(url or "")).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        return ""
    return "instagram.com" if host.endswith("instagram.com") else host


def _refresh_evidence_counts(record: dict) -> dict:
    for prefix in ("identity","country","creator","category","follower"):
        urls=list(record.get(f"{prefix}_source_urls") or [])
        record[f"{prefix}_source_count"]=len({_source_key(url) for url in urls if _source_key(url)})
    record["evidence_source_urls"]=list(record.get("identity_source_urls") or record.get("evidence_source_urls") or [])
    record["evidence_source_count"]=int(record.get("identity_source_count") or 0)
    record["evidence_status"]="교차검증" if record["evidence_source_count"]>=2 else "단일 검색근거"
    return record


def _search_evidence_qualified(record: dict) -> bool:
    return (
        int(record.get("identity_source_count") or record.get("evidence_source_count") or 0)>=2
        and int(record.get("country_source_count") or 0)>=1
        and int(record.get("creator_source_count") or 0)>=1
        and int(record.get("category_source_count") or 0)>=1
        and int(record.get("follower_source_count") or 0)>=1
        and record.get("country_status")=="확인됨"
        and record.get("profile_type") in {"인플루언서·KOL","KOC·리뷰어","개인 크리에이터"}
        and isinstance(record.get("followers"),int)
        and record.get("followers")>=1_000
    )


def _sender_label(sender: dict) -> str:
    return _plain(sender.get("brand_name") or sender.get("company_name"), 80)


def generate_outreach(record: dict, sender: dict, product: dict) -> dict:
    """Create honest drafts without claiming that a profile was deeply inspected."""
    brand=_sender_label(sender)
    company=_plain(sender.get("company_name"),80)
    contact=_plain(sender.get("contact_name"),60)
    website=_plain(sender.get("website"),180)
    product_name=_plain(product.get("product_name"),100)
    category=_plain(product.get("category") or record.get("category"),100)
    benefit=_plain(product.get("selling_points"),220)
    offer=_plain(product.get("collaboration_offer"),180)
    name=record.get("display_name") or f"@{record.get('handle','')}"
    ready=bool(brand and product_name)
    if not ready:
        return {
            **record,
            "dm_draft":"발신 브랜드명과 영문 제품명을 입력하면 맞춤 DM이 생성됩니다.",
            "email_subject":"",
            "email_body":"발신 브랜드명과 영문 제품명을 입력하면 맞춤 이메일이 생성됩니다.",
            "email_compose_url":"",
            "outreach_generation_status":"입력 필요",
        }

    intro=f"I'm {contact} from {brand}" if contact else f"I'm reaching out from {brand}"
    company_note=f" ({company})" if company and company.casefold()!=brand.casefold() else ""
    product_line=f"We would like to introduce {product_name}, a Korean {category or 'product'}."
    if benefit:
        product_line+=f" {benefit.rstrip('.')} .".replace(" .", ".")
    offer_line=offer or "Would you be open to receiving a short product introduction and discussing a possible collaboration?"
    research_scope=f"{record.get('category','')} creators"
    if record.get("country_status")=="확인됨" and record.get("verified_country"):
        research_scope+=f" in {record['verified_country']}"
    dm=(
        f"Hi {name},\n\n"
        f"I found your public Instagram profile while researching {research_scope}. "
        f"{intro}{company_note}. {product_line}\n\n{offer_line}\n\n"
        f"Best,\n{contact or brand}"
    )
    if website:
        dm+=f"\n{website}"
    subject=f"Collaboration inquiry from {brand} — {product_name}"
    body=(
        f"Hi {name},\n\n"
        f"I found your public Instagram profile while researching {research_scope}. "
        f"{intro}{company_note}.\n\n{product_line}\n\n{offer_line}\n\n"
        f"Best regards,\n{contact or brand}"
    )
    if website:
        body+=f"\n{website}"
    email=record.get("public_email","")
    mailto=""
    if email:
        mailto=f"mailto:{email}?"+urllib.parse.urlencode({"subject":subject,"body":body})
    return {
        **record,
        "dm_draft":dm[:1600],
        "email_subject":subject[:180],
        "email_body":body[:2400],
        "email_compose_url":mailto,
        "outreach_generation_status":"초안 생성완료",
    }


def _verification_text(results: list[dict], record: dict) -> str:
    """Keep only secondary results that can be tied back to this profile."""
    handle=str(record.get("handle") or "").casefold()
    pieces=[]
    for item in results:
        url=str(item.get("url") or "")
        # The source URL is useful for identity/audit, but words in a domain or
        # path (for example ``creator-directory.example``) do not describe the
        # Instagram account.  Feeding the URL into account classification can
        # therefore turn a genuine person into a false "directory" account.
        visible_text=_plain(f"{item.get('title','')} {item.get('content') or item.get('description','')}",1200)
        binding_text=_plain(f"{visible_text} {url}",1400)
        same_profile=normalize_handle(url)==handle
        mentions_handle=bool(handle and handle in binding_text.casefold())
        if same_profile or mentions_handle:
            pieces.append(visible_text)
    return " ".join(pieces)[:3600]


def _enrich_external_results(results: list[dict], record: dict, fetcher=None, limit: int=2) -> tuple[list[dict],dict]:
    """Add visible text from exact-handle public pages to search snippets."""
    handle=str(record.get("handle") or "").casefold()
    enriched=[]; metrics={"attempted":0,"verified":0,"failed":0}
    used_hosts=set()
    for item in results:
        copy=dict(item); url=str(copy.get("url") or "").strip()
        folded=_plain(f"{copy.get('title','')} {copy.get('content') or copy.get('description','')} {url}",1600).casefold()
        host=(urllib.parse.urlsplit(url).hostname or "").casefold().removeprefix("www.") if url else ""
        eligible=(
            url and handle and handle in folded
            and host and not host.endswith("instagram.com")
            and host not in used_hosts and metrics["attempted"]<max(0,int(limit))
        )
        if eligible:
            used_hosts.add(host); metrics["attempted"]+=1
            page=fetch_public_evidence_page(url,handle,fetcher=fetcher)
            copy["external_verification_status"]=page.get("status")
            copy["external_verification_http"]=page.get("http_status")
            copy["external_verification_reason"]=page.get("reason")
            if page.get("status")=="확인" and page.get("text"):
                copy["content"]=_plain(f"{copy.get('content') or copy.get('description','')} {page['text']}",5000)
                copy["external_evidence_text"]=page["text"]
                metrics["verified"]+=1
            else:
                metrics["failed"]+=1
        enriched.append(copy)
    return enriched,metrics


def _directory_country_signal(text: str, url: str, country: str) -> bool:
    """Accept an exact-handle creator directory as country evidence."""
    plain=_plain(f"{text} {url}",1800).casefold()
    slug=country.casefold().replace(" ","-")
    creator_hits=_term_hits(plain,CREATOR_TERMS)
    if not creator_hits:
        return False
    if f"/{slug}/" in plain or f"/{slug}?" in plain:
        return True
    if country=="Vietnam":
        return bool(re.search(r"(?i)(?:top\s+\d+\s+)?(?:vietnam|vietnamese).{0,45}(?:influencer|blogger|creator|koc|kol)",plain))
    return bool(re.search(rf"(?i){re.escape(country)}.{{0,45}}(?:influencer|blogger|creator)",plain))


def _verification_sources(results: list[dict], record: dict) -> dict[str,list[str]]:
    handle=str(record.get("handle") or "").casefold()
    category_terms=list(dict.fromkeys(
        list(record.get("category_keywords") or [])
        + [value.strip() for value in str(record.get("matched_keywords") or "").split(",") if value.strip()]
        + default_keywords(str(record.get("category") or ""))
    ))
    sources={"identity":[],"country":[],"creator":[],"category":[],"follower":[]}
    for item in results:
        url=str(item.get("url") or "").strip()
        text=_plain(f"{item.get('title','')} {item.get('content') or item.get('description','')} {url}",1200)
        folded=text.casefold()
        tied=normalize_handle(url)==handle or (handle and handle in folded)
        if not tied or not url:
            continue
        if url not in sources["identity"]:
            sources["identity"].append(url)
        country=assess_country(text,str(record.get("target_country") or ""))
        creator_hits=_term_hits(text,CREATOR_TERMS)
        category_hits=_term_hits(text,category_terms)
        followers=parse_follower_count(text)
        if country.get("country_status")=="확인됨" or _directory_country_signal(text,url,str(record.get("target_country") or "")):
            sources["country"].append(url)
        if creator_hits:
            sources["creator"].append(url)
        if category_hits:
            sources["category"].append(url)
        if isinstance(followers,int) and followers>=1_000:
            sources["follower"].append(url)
    return sources


def _dedupe_rejected_records(records: list[dict]) -> list[dict]:
    merged={}
    for record in records:
        handle=str(record.get("handle") or "").casefold().strip()
        source_url=str(record.get("source_url") or "").strip()
        key=handle or source_url.casefold() or f"row-{len(merged)}"
        if key not in merged:
            merged[key]={**record,"query_count":1}
            continue
        current=merged[key]
        current["query_count"]=int(current.get("query_count") or 1)+1
        for field in ("reason","query"):
            values=[value.strip() for value in str(current.get(field) or "").split(" | ") if value.strip()]
            new_value=str(record.get(field) or "").strip()
            if new_value and new_value not in values:
                values.append(new_value)
            current[field]=" | ".join(values[:8])
        for field in ("title","evidence","source_url"):
            if not current.get(field) and record.get(field):
                current[field]=record[field]
    return list(merged.values())


class _ProfileHTTPResponse:
    def __init__(self,status_code: int,text: str,url: str):
        self.status_code=status_code; self.text=text; self.url=url


def _default_profile_fetcher(url: str, headers: dict, timeout: float, allow_redirects: bool=True):
    request=urllib.request.Request(url,headers=headers,method="GET")
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            data=response.read(1_500_000)
            charset=response.headers.get_content_charset() or "utf-8"
            return _ProfileHTTPResponse(response.status,data.decode(charset,errors="replace"),response.geturl())
    except urllib.error.HTTPError as exc:
        try:
            text=exc.read(200_000).decode("utf-8",errors="replace")
        except Exception:
            text=""
        return _ProfileHTTPResponse(exc.code,text,exc.geturl() or url)


def verify_original_profile(record: dict, fetcher=None, timeout: float=12.0) -> dict:
    """Check one public Instagram URL without login, cookies or access bypass."""
    url=profile_url(str(record.get("handle") or ""))
    base={
        "profile_verification_status":"확인 오류",
        "profile_verification_http":"",
        "profile_verification_url":url,
        "profile_verification_evidence":"",
        "profile_checked_at":datetime.now(timezone.utc).isoformat(),
        "direct_profile_text":"",
    }
    if not url:
        return {**base,"profile_verification_status":"계정 없음"}
    getter=fetcher or _default_profile_fetcher
    try:
        response=getter(
            url,
            headers={
                "User-Agent":f"GLOEUM-PublicProfileVerifier/{APP_VERSION}",
                "Accept":"text/html,application/xhtml+xml",
            },
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        return {**base,"profile_verification_evidence":f"{type(exc).__name__}: {str(exc)[:180]}"}
    status=int(getattr(response,"status_code",0) or 0)
    final_url=str(getattr(response,"url",url) or url)
    result={**base,"profile_verification_http":status,"profile_verification_url":final_url}
    if status in {404,410}:
        return {**result,"profile_verification_status":"계정 없음","profile_verification_evidence":f"HTTP {status}"}
    if status in {401,403,429}:
        return {**result,"profile_verification_status":"접근 제한","profile_verification_evidence":f"HTTP {status}"}
    if status < 200 or status >= 400:
        return {**result,"profile_verification_evidence":f"HTTP {status}"}
    if any(part in urllib.parse.urlsplit(final_url).path.casefold() for part in ("/accounts/login", "/challenge/")):
        return {**result,"profile_verification_status":"접근 제한","profile_verification_evidence":"로그인·접근확인 페이지로 전환"}

    body=str(getattr(response,"text","") or "")[:1_500_000]
    lowered=body.casefold()
    if any(marker in lowered for marker in ("sorry, this page isn't available", "page isn't available", "페이지를 사용할 수 없습니다")):
        return {**result,"profile_verification_status":"계정 없음","profile_verification_evidence":"Instagram 페이지 없음 안내"}
    parser=_ProfileMetaParser()
    try:
        parser.feed(body)
    except Exception:
        pass
    canonical=parser.meta.get("og:url") or parser.canonical
    canonical_handle=normalize_handle(canonical) if canonical else ""
    expected=str(record.get("handle") or "").casefold()
    if canonical_handle and canonical_handle!=expected:
        return {
            **result,
            "profile_verification_status":"정보 불일치",
            "profile_verification_evidence":f"요청 @{expected} · 응답 @{canonical_handle}",
        }
    title=_plain(parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title,240)
    description=_plain(parser.meta.get("og:description") or parser.meta.get("description") or parser.meta.get("twitter:description"),1200)
    public_text=_plain(f"{title} {description}",1400)
    if not public_text or all(token in public_text.casefold() for token in ("instagram","log in")):
        return {**result,"profile_verification_status":"접근 제한","profile_verification_evidence":"공개 프로필 메타데이터 미표시"}
    handle_visible=expected in public_text.casefold() or canonical_handle==expected
    if not handle_visible:
        return {**result,"profile_verification_status":"정보 부족","profile_verification_evidence":public_text[:360]}
    return {
        **result,
        "profile_verification_status":"자동검증 완료",
        "profile_verification_evidence":public_text[:600],
        "direct_profile_text":public_text,
    }


def _apply_original_verification(record: dict, verification: dict, config: InfluencerConfig) -> tuple[dict | None,str]:
    updated={**record,**verification}
    status=verification.get("profile_verification_status")
    if status=="계정 없음":
        return None,"원본 확인: 계정 없음"
    if status=="정보 불일치":
        return None,"원본 확인: 정보 불일치"
    if status!="자동검증 완료":
        if status=="접근 제한" and _search_evidence_qualified(updated):
            updated["grade"]="B"
            updated["profile_review_reason"]="Instagram 직접확인 제한 · 공개 검색근거 2개 이상 교차검증"
        else:
            updated["grade"]="C"
            updated["profile_review_reason"]=f"원본 확인: {status}"
        return updated,""

    direct=verification.get("direct_profile_text","")
    creator_hits=_term_hits(direct,CREATOR_TERMS)
    category_terms=list(record.get("category_keywords") or default_keywords(config.category))
    category_hits=_term_hits(direct,category_terms)
    role,role_hits=classify_profile_role(direct,creator_hits,str(record.get("handle") or ""))
    creator_roles={"인플루언서·KOL","KOC·리뷰어","전문 크리에이터","개인 크리에이터","크리에이터 후보"}
    if role not in creator_roles and not config.include_business_accounts:
        return None,"원본 확인: 브랜드·상점·미디어·대행사 계정"
    if role_hits:
        updated["profile_type"]=role
        updated["role_evidence"]=', '.join(role_hits[:6])
    if category_hits:
        urls=list(updated.get("category_source_urls") or [])
        if updated.get("instagram_url") and updated["instagram_url"] not in urls:
            urls.append(updated["instagram_url"])
        updated["category_source_urls"]=urls
        updated["matched_keywords"]=', '.join(list(dict.fromkeys(category_hits))[:6])
        _refresh_evidence_counts(updated)

    direct_country=assess_country(direct,config.country)
    if direct_country["country_status"]=="대상국가 불일치":
        return None,"원본 확인: 대상국가 불일치"
    if direct_country["country_status"]=="확인됨":
        updated.update({
            "verified_country":direct_country["verified_country"],
            "country_status":"확인됨",
            "country_evidence":direct_country["country_evidence"],
            "foreign_country_evidence":"",
            "country":direct_country["verified_country"],
        })
    followers=parse_follower_count(direct)
    if followers is not None:
        updated["followers"]=followers
        updated["followers_display"]=follower_label(followers)
        updated["follower_status"]="Instagram 공개 원본 확인"
        updated["follower_eligibility"]="통과" if followers>=1_000 else "1,000명 미만"
    email=extract_public_email(direct)
    if email:
        updated["public_email"]=email
        updated["email_status"]="Instagram 공개 원본 확인"

    verified_creator=updated.get("profile_type") in {"인플루언서·KOL","KOC·리뷰어","개인 크리에이터"}
    verified_country=updated.get("country_status")=="확인됨"
    follower_eligible=isinstance(updated.get("followers"),int) and updated["followers"]>=1_000
    category_eligible=bool(category_hits)
    if verified_creator and verified_country and follower_eligible and category_eligible:
        updated["grade"]="A"
    else:
        updated["grade"]="C"
        updated["profile_review_reason"]="원본 확인 완료 · 국가/유형/팔로워 기준 추가확인"
    return updated,""


def _apify_input_handle(raw: dict) -> str:
    return normalize_handle(str(raw.get("inputUrl") or raw.get("url") or "")) or str(raw.get("username") or "").casefold()


def _apify_profile_text(raw: dict) -> tuple[str,list[str]]:
    captions=[]
    for post in list(raw.get("latestPosts") or [])[:12]:
        if isinstance(post,dict) and post.get("caption"):
            captions.append(_plain(post.get("caption"),1800))
    profile_text=_plain(" ".join((
        str(raw.get("username") or ""),
        str(raw.get("fullName") or ""),
        str(raw.get("biography") or ""),
        str(raw.get("businessCategoryName") or ""),
        str(raw.get("externalUrl") or ""),
    )),3000)
    return profile_text,captions


def _generic_apify_evaluation(raw: dict, config: InfluencerConfig) -> dict:
    """Conservative fallback for non-Vietnam or non-beauty searches."""
    input_handle=_apify_input_handle(raw)
    returned_handle=str(raw.get("username") or "").casefold().strip()
    handle_match=bool(input_handle and returned_handle and input_handle==returned_handle)
    followers=raw.get("followersCount") if isinstance(raw.get("followersCount"),int) else None
    profile_text,captions=_apify_profile_text(raw)
    all_text=_plain(" ".join([profile_text]+captions),12000)
    country=assess_country(all_text,config.country)
    creator_hits=_term_hits(profile_text,CREATOR_TERMS+COLLAB_TERMS)
    role,role_hits=classify_profile_role(profile_text,creator_hits,returned_handle)
    creator_roles={"인플루언서·KOL","KOC·리뷰어","개인 크리에이터"}
    category_terms=tuple(dict.fromkeys(default_keywords(config.category)+list(config.keywords)))
    profile_category_hits=_term_hits(profile_text,category_terms)
    category_posts=sum(bool(_term_hits(caption,category_terms)) for caption in captions)
    category_ratio=category_posts/max(1,len(captions))
    category_ok=bool(profile_category_hits and category_ratio>=0.20) or category_ratio>=0.40
    minimum=max(1_000,int(config.min_followers or 0))
    follower_ok=isinstance(followers,int) and followers>=minimum and (not config.max_followers or followers<=config.max_followers)
    business=role not in creator_roles
    email=extract_public_email(str(raw.get("biography") or ""))

    if raw.get("error") or not returned_handle:
        status,eligible,reason="보류_계정확인불가",False,str(raw.get("errorDescription") or raw.get("error") or "Apify 프로필 반환 없음")
    elif not handle_match:
        status,eligible,reason="보류_handle불일치",False,"요청 계정과 반환 계정이 일치하지 않음"
    elif country["country_status"]=="대상국가 불일치":
        status,eligible,reason="제외_타국가",False,country["foreign_country_evidence"]
    elif business and not config.include_business_accounts:
        status,eligible,reason="제외_기업계정",False,f"개인 크리에이터가 아닌 계정: {role}"
    elif country["country_status"]!="확인됨":
        status,eligible,reason="보류_국가근거부족",False,"대상국가 활동지역 근거 부족"
    elif business and config.include_business_accounts and category_ok and follower_ok:
        status,eligible,reason="적합_사업계정포함",True,"사용자 설정에 따라 사업계정 포함"
    elif role not in creator_roles:
        status,eligible,reason="보류_개인근거부족",False,"개인 크리에이터 근거 부족"
    elif not category_ok:
        status,eligible,reason="보류_분야근거부족",False,"선택 분야가 주된 콘텐츠인지 추가 확인 필요"
    elif not follower_ok:
        status,eligible,reason="보류_팔로워조건",False,"팔로워 수가 선택 범위와 맞지 않거나 확인되지 않음"
    else:
        status,eligible,reason="적합",True,"대상국가·개인 크리에이터·선택 분야·팔로워 근거 확인"
    return {
        "input_handle":input_handle,"returned_handle":returned_handle,"handle_match":handle_match,
        "status":status,"eligible":eligible,"country":country.get("verified_country") or "미확인",
        "country_confidence":"높음" if country["country_status"]=="확인됨" else "없음",
        "account_type":role,"category_relevance":"확인" if category_ok else "미확인",
        "followers":followers,"email":email,"profile_url":str(raw.get("url") or profile_url(returned_handle)),
        "country_evidence":country.get("country_evidence") or country.get("foreign_country_evidence") or "",
        "account_evidence":", ".join(role_hits[:6]),
        "category_evidence":f"프로필 키워드 {len(profile_category_hits)}개, 최근 게시물 {category_posts}/{len(captions)}개",
        "reason":reason,
    }


def evaluate_apify_profile(raw: dict, config: InfluencerConfig) -> dict:
    if config.country=="Vietnam" and config.category=="뷰티·화장품":
        result=asdict(verify_vietnam_beauty_profile(raw))
        result["category_relevance"]=result.pop("beauty_relevance")
        result["category_evidence"]=result.pop("beauty_evidence")
        followers=result.get("followers")
        minimum=max(1_000,int(config.min_followers or 0))
        follower_ok=isinstance(followers,int) and followers>=minimum and (not config.max_followers or followers<=config.max_followers)
        if result.get("eligible") and not follower_ok:
            result.update({
                "status":"보류_팔로워조건","eligible":False,
                "reason":"팔로워 수가 선택 범위와 맞지 않거나 확인되지 않음",
            })
        if result.get("status")=="제외_기업계정" and config.include_business_accounts:
            category_ok=result.get("category_relevance")=="확인"
            country_ok=result.get("country")==config.country
            if category_ok and country_ok and follower_ok:
                result.update({
                    "status":"적합_사업계정포함","eligible":True,
                    "reason":"사용자 설정에 따라 사업계정 포함",
                })
        return result
    return _generic_apify_evaluation(raw,config)


def _apply_apify_verification(record: dict, result: dict, config: InfluencerConfig) -> tuple[dict | None,str]:
    checked_at=datetime.now(timezone.utc).isoformat()
    updated={**record,
        "apify_status":result.get("status",""),
        "apify_handle_match":bool(result.get("handle_match")),
        "apify_country_confidence":result.get("country_confidence",""),
        "apify_account_type":result.get("account_type",""),
        "apify_category_relevance":result.get("category_relevance",""),
        "apify_reason":result.get("reason",""),
        "apify_checked_at":checked_at,
        "profile_verification_status":"Apify 검증완료" if result.get("returned_handle") else "Apify 확인불가",
        "profile_verification_http":"API",
        "profile_verification_url":result.get("profile_url") or record.get("instagram_url",""),
        "profile_verification_evidence":" · ".join(filter(None,(
            result.get("country_evidence"),result.get("account_evidence"),result.get("category_evidence")
        )))[:900],
        "profile_checked_at":checked_at,
    }
    followers=result.get("followers")
    if isinstance(followers,int):
        updated.update({
            "followers":followers,"followers_display":follower_label(followers),
            "follower_status":"Apify Instagram 원본 확인",
            "follower_eligibility":"통과" if followers>=max(1_000,int(config.min_followers or 0)) else "선택 기준 미달",
        })
    if result.get("email"):
        updated.update({"public_email":result["email"],"email_status":"Apify Instagram 원본 확인"})
    # Country evidence is independent of category/follower eligibility.  A
    # beauty-weak Vietnam creator can remain C while still displaying Vietnam,
    # rather than contradicting the Apify audit columns with "위치 미확인".
    if (
        result.get("country")==config.country
        and result.get("country_confidence") in {"높음","중간"}
    ):
        updated.update({
            "verified_country":config.country,
            "country_status":"확인됨",
            "country_evidence":result.get("country_evidence") or updated.get("country_evidence",""),
            "foreign_country_evidence":"",
            "country":config.country,
        })
    if result.get("account_type") in {"인플루언서·KOL","KOC·리뷰어","전문 크리에이터","개인 크리에이터"}:
        updated.update({
            "profile_type":result.get("account_type"),
            "role_evidence":result.get("account_evidence") or updated.get("role_evidence",""),
        })
    status=str(result.get("status") or "")
    if status.startswith("제외_타국가"):
        return None,f"Apify: {result.get('reason') or '대상국가 불일치'}"
    if status.startswith("제외_기업계정") and not config.include_business_accounts:
        return None,f"Apify: {result.get('reason') or '기업계정'}"
    if result.get("eligible"):
        country_confidence=result.get("country_confidence")
        final_grade="A" if country_confidence=="높음" else "B"
        minimum_score=80 if final_grade=="A" else 70
        updated.update({
            "grade":final_grade,
            "score":max(minimum_score,int(updated.get("score") or 0)),
            "profile_type":result.get("account_type") or "개인 크리에이터",
            "role_evidence":result.get("account_evidence") or updated.get("role_evidence",""),
            "verified_country":config.country,
            "country_status":"확인됨",
            "country_evidence":result.get("country_evidence") or updated.get("country_evidence",""),
            "foreign_country_evidence":"",
            "country":config.country,
            "matched_keywords":result.get("category_evidence") or updated.get("matched_keywords",""),
            "profile_review_reason":"",
            "evidence_status":"Apify 원본 프로필 검증",
        })
    else:
        updated.update({
            "grade":"C",
            "profile_review_reason":f"Apify {status}: {result.get('reason','추가 확인 필요')}",
        })
    return updated,""


def collect_influencers(config: InfluencerConfig, provider, sender: dict | None=None,
                        product: dict | None=None, progress=None, profile_fetcher=None,
                        external_fetcher=None, apify_token: str | None=None,
                        apify_fetcher=None, apify_search_fetcher=None,
                        apify_hashtag_fetcher=None) -> dict:
    started=time.monotonic()
    sender=sender or {}; product=product or {}
    planned_queries=build_queries(config)
    max_calls=max(1,int(config.max_queries))
    # Apify already verifies the exact profile. Reserving half the search calls
    # for a redundant per-handle web search reduced discovery without improving
    # the final decision.
    verification_reserve=0 if config.use_apify_verification else min(10,max(0,max_calls//2))
    discovery_budget=max(1,max_calls-verification_reserve) if config.use_web_fallback else 0
    queue=list(planned_queries)
    executed_queries=[]; native_queries=[]; seen_queries=set()
    native_api_log=[]; query_result_counts={}
    pool={}; rejected={}; rejected_records=[]; errors=[]; raw_results=0; discovery_results=0; verification_results=0
    profile_check_counts={"total":0,"자동검증 완료":0,"Apify 검증완료":0,"Apify 확인불가":0,"접근 제한":0,"확인 오류":0,"계정 없음":0,"정보 불일치":0,"정보 부족":0}
    apify_cache_hits=0; apify_requested=0; apify_stages=0
    instagram_discovery_calls=0; instagram_search_results=0; instagram_hashtag_posts=0
    instagram_discovered_handles=set(); web_fallback_calls=0
    external_check_counts={"attempted":0,"verified":0,"failed":0}

    def ingest_discovery_rows(rows: list[dict], source_type: str, fallback_term: str="") -> None:
        nonlocal raw_results,discovery_results
        raw_results+=len(rows); discovery_results+=len(rows)
        for raw in rows:
            item=discovery_item_to_search_result(raw,source_type,fallback_term)
            if not item:
                rejected["Instagram 발견결과 계정명 없음"]=rejected.get("Instagram 발견결과 계정명 없음",0)+1
                continue
            term=item.get("instagram_discovery_term") or fallback_term or source_type
            query=f"[{source_type}] {term}".strip()
            candidate,reason=_candidate_from_result(item,query,config)
            if not candidate:
                rejected[reason]=rejected.get(reason,0)+1
                rejected_records.append({
                    "handle":_discovery_handle(raw),"reason":reason,
                    "title":_plain(item.get("title"),220),"evidence":_plain(item.get("content"),500),
                    "source_url":item.get("instagram_discovery_original_url") or item.get("url",""),
                    "query":query,
                })
                continue
            handle=candidate["handle"]
            instagram_discovered_handles.add(handle)
            candidate.update({
                "source_type":source_type,
                "source_url":item.get("instagram_discovery_original_url") or candidate.get("source_url",""),
                "discovery_query":query,
            })
            existing=pool.get(handle)
            if not existing or candidate["score"]>existing["score"]:
                pool[handle]=_merge_evidence(candidate,existing) if existing else candidate
            else:
                _merge_evidence(existing,candidate)

    def record_native_query_counts(rows: list[dict], source_label: str, terms: list[str]) -> None:
        """Preserve per-term counts when the Actor identifies the originating term."""
        normalized={str(term).casefold():str(term) for term in terms}
        for raw in rows:
            raw_term=_plain(
                _first_value(raw,"searchTerm","search_term","hashtag","tagName","tag_name"),160
            ).lstrip("#")
            canonical=normalized.get(raw_term.casefold())
            if canonical:
                prefix="#" if "해시태그" in source_label else ""
                key=f"[{source_label}] {prefix}{canonical}"
                query_result_counts[key]=query_result_counts.get(key,0)+1

    if config.use_apify_discovery:
        all_terms=build_instagram_discovery_terms(config)
        # Instagram Search Scraper applies searchLimit to every search term.
        # A large comma-separated batch can therefore exceed the synchronous
        # Actor deadline. Start with only the strongest terms; hashtag and web
        # fallback stages can still supplement a genuinely short candidate pool.
        primary_term_count=3 if int(config.target_count)<=20 else 5
        terms=all_terms[:primary_term_count]
        search_limit=min(int(config.apify_search_limit),8 if int(config.target_count)<=20 else 10)
        native_queries.extend(f"[Apify Instagram 사용자검색] {term}" for term in terms)
        search_fetcher=apify_search_fetcher or fetch_instagram_search_profiles
        try:
            if progress:
                progress(0,3,"Instagram 내부 사용자 검색으로 후보를 찾고 있습니다.")
            actor_started=time.monotonic()
            try:
                search_rows=search_fetcher(
                    terms,token=apify_token,search_limit=search_limit,
                )
            except ApifyTransientError:
                # Apify reports Actor TIMED-OUT as HTTP 400. Retry only the
                # highest-priority term so the retry is materially cheaper.
                compact_terms=terms[:1]
                if progress:
                    progress(0,3,"Instagram 사용자검색 시간초과 · 핵심 검색어 1개로 재시도합니다.")
                search_rows=search_fetcher(
                    compact_terms,token=apify_token,
                    search_limit=min(search_limit,6),
                )
                terms=compact_terms
            instagram_discovery_calls+=1; instagram_search_results=len(search_rows)
            # Match the Actor's own searchTerm against the complete configured
            # term list so audit labels remain intact for cached/test fixtures.
            record_native_query_counts(search_rows,"Apify Instagram 사용자검색",all_terms)
            native_api_log.append({
                "provider":"Apify Instagram Search","query":f"사용자 검색어 {len(terms)}개 실행",
                "result_count":len(search_rows),"http_status":200,"request_id":"",
                "response_time":round(time.monotonic()-actor_started,2),"error":"",
            })
            ingest_discovery_rows(search_rows,"Apify Instagram 사용자검색",terms[0] if terms else "")
        except ApifyError:
            if not (config.use_web_fallback and provider is not None):
                raise
            errors.append("Apify Instagram 사용자검색 실패 · 웹 보조검색으로 전환")
        except Exception as exc:
            if not (config.use_web_fallback and provider is not None):
                raise ApifyError(f"Apify Instagram 후보 발견 실패 · {type(exc).__name__}: {str(exc)[:240]}") from None
            errors.append(f"Apify Instagram 사용자검색 실패 · {type(exc).__name__}: {str(exc)[:180]}")

        # Hashtag post crawling is the slowest Actor. Do not run it merely to
        # inflate a 10-row trial to a 30-row pool when native user search has
        # already supplied enough accounts for meaningful verification.
        desired_pool=max(10,math.ceil(int(config.target_count)*1.5))
        hashtags=build_instagram_discovery_hashtags(config)
        runtime_ok=(time.monotonic()-started)<max(60,int(config.max_runtime_seconds)*0.45)
        if len(pool)<desired_pool and hashtags and runtime_ok:
            native_queries.extend(f"[Apify Instagram 해시태그] #{tag}" for tag in hashtags)
            hashtag_fetcher=apify_hashtag_fetcher or fetch_instagram_hashtag_posts
            try:
                if progress:
                    progress(1,3,"Instagram 해시태그 게시물 작성자를 보충하고 있습니다.")
                actor_started=time.monotonic()
                hashtag_rows=hashtag_fetcher(
                    hashtags,token=apify_token,results_limit=config.apify_hashtag_limit,
                )
                instagram_discovery_calls+=1; instagram_hashtag_posts=len(hashtag_rows)
                record_native_query_counts(hashtag_rows,"Apify Instagram 해시태그",hashtags)
                native_api_log.append({
                    "provider":"Apify Instagram Hashtag","query":f"해시태그 {len(hashtags)}개 일괄 실행",
                    "result_count":len(hashtag_rows),"http_status":200,"request_id":"",
                    "response_time":round(time.monotonic()-actor_started,2),"error":"",
                })
                ingest_discovery_rows(hashtag_rows,"Apify Instagram 해시태그게시물",hashtags[0])
            except Exception as exc:
                errors.append(f"Apify Instagram 해시태그 보강 실패 · {type(exc).__name__}: {str(exc)[:180]}")

    while provider is not None and queue and len(executed_queries)<discovery_budget:
        query=queue.pop(0)
        if query in seen_queries:
            continue
        seen_queries.add(query); executed_queries.append(query)
        web_fallback_calls+=1
        index=len(executed_queries)
        if progress:
            progress(index-1,max_calls,f"Instagram 공개 검색 {index}/{max_calls}")
        try:
            results=provider.search(query,max_results=20,country="",minimal=False)
        except Exception as exc:
            errors.append(f"{query} · {type(exc).__name__}: {str(exc)[:240]}")
            continue
        raw_results+=len(results); discovery_results+=len(results)
        if not results:
            fallback=relax_query(query)
            if fallback and fallback not in seen_queries and fallback not in queue:
                queue.insert(0,fallback)
        for item in results:
            candidate,reason=_candidate_from_result(item,query,config)
            if not candidate:
                rejected[reason]=rejected.get(reason,0)+1
                rejected_records.append({
                    "handle":normalize_handle(str(item.get("url") or "")),
                    "reason":reason,
                    "title":_plain(item.get("title"),220),
                    "evidence":_plain(item.get("content") or item.get("description"),500),
                    "source_url":str(item.get("url") or ""),
                    "query":query,
                })
                continue
            handle=candidate["handle"]
            existing=pool.get(handle)
            if not existing or candidate["score"]>existing["score"]:
                pool[handle]=_merge_evidence(candidate,existing) if existing else candidate
            elif candidate.get("public_email") and not existing.get("public_email"):
                existing.update({k:candidate[k] for k in ("public_email","email_status")})
                _merge_evidence(existing,candidate)
            else:
                _merge_evidence(existing,candidate)
        qualified_count=sum(row.get("grade") in {"A","B"} for row in pool.values())
        desired_pool=max(20,int(config.target_count)*3)
        if qualified_count>=config.target_count and len(pool)>=desired_pool:
            break
        time.sleep(max(0,float(config.request_delay)))

    # A small second pass also checks plausible individuals whose snippet does
    # not explicitly contain an influencer label. They remain C until proven.
    # It uses public search snippets and never logs in to Instagram.
    promising=[] if config.use_apify_verification or provider is None else sorted((
        row for row in pool.values()
        if row.get("profile_type") in {"인플루언서·KOL","KOC·리뷰어","개인 크리에이터","크리에이터 후보"}
        and (isinstance(row.get("followers"),int) and row.get("followers")>=1_000)
        and not _search_evidence_qualified(row)
    ),key=lambda row:(
        row.get("profile_type")!="크리에이터 후보",
        row.get("country_status")=="확인됨",
        bool(row.get("public_email")),
        row.get("score",0),
        row.get("followers") if isinstance(row.get("followers"),int) else -1,
    ),reverse=True)
    for row in promising[:max(0,max_calls-len(executed_queries))]:
        query=build_verification_query(row,config)
        if query in seen_queries:
            continue
        seen_queries.add(query); executed_queries.append(query)
        if progress:
            progress(len(executed_queries)-1,max_calls,f"후보 국가·계정유형 재확인 {len(executed_queries)}/{max_calls}")
        try:
            results=provider.search(query,max_results=10,country="",minimal=False)
        except Exception as exc:
            errors.append(f"{query} · {type(exc).__name__}: {str(exc)[:240]}")
            continue
        verification_results+=len(results); raw_results+=len(results)
        if config.verify_external_evidence:
            results,external_metrics=_enrich_external_results(
                results,row,fetcher=external_fetcher,limit=max(0,int(config.max_external_pages))
            )
            for key in external_check_counts:
                external_check_counts[key]+=int(external_metrics.get(key) or 0)
        evidence=_verification_text(results,row)
        verification_sources=_verification_sources(results,row)
        if not evidence:
            continue
        synthetic={
            "title":row.get("source_title") or row.get("display_name"),
            "url":row.get("source_url") or row.get("instagram_url"),
            "content":f'{row.get("bio_summary","")} {evidence}',
        }
        refreshed,reason=_candidate_from_result(synthetic,row.get("discovery_query",query),config)
        if not refreshed:
            pool.pop(row["handle"],None)
            rejected[f"2차 확인: {reason}"]=rejected.get(f"2차 확인: {reason}",0)+1
            continue
        refreshed.update({
            "discovery_query":row.get("discovery_query",query),
            "source_url":row.get("source_url",refreshed.get("source_url","")),
            "source_type":row.get("source_type",refreshed.get("source_type","")),
            "bio_summary":row.get("bio_summary",refreshed.get("bio_summary","")),
            "category_keywords":row.get("category_keywords",refreshed.get("category_keywords",[])),
            "verification_query":query,
            "verification_evidence":evidence[:600],
        })
        for prefix in ("identity","country","creator","category","follower"):
            refreshed[f"{prefix}_source_urls"]=list(dict.fromkeys(
                list(row.get(f"{prefix}_source_urls") or [])+verification_sources[prefix]
            ))
        _refresh_evidence_counts(refreshed)
        if verification_sources["country"] and refreshed.get("country_status")!="대상국가 불일치":
            refreshed.update({
                "verified_country":config.country,
                "country_status":"확인됨",
                "country_evidence":refreshed.get("country_evidence") or "외부 공개 크리에이터 디렉터리",
                "foreign_country_evidence":"",
                "country":config.country,
            })
        if _search_evidence_qualified(refreshed):
            refreshed["grade"]="B"
        pool[row["handle"]]=refreshed
        time.sleep(max(0,float(config.request_delay)))

    if config.use_apify_verification:
        role_priority={
            "인플루언서·KOL":5,"KOC·리뷰어":4,"개인 크리에이터":3,
            "크리에이터 후보":2,"전문 크리에이터":1,
        }
        verification_order=sorted(
            pool.values(),
            key=lambda row:(
                role_priority.get(str(row.get("profile_type") or ""),0),
                row.get("country_status")=="확인됨",
                row.get("grade") in {"A","B"},row.get("score",0),
            ),
            reverse=True,
        )
        initial_limit=_apify_profile_limit(config,len(verification_order))
        hard_limit=min(len(verification_order),max(initial_limit,int(config.target_count)*4),120)
        fetcher=apify_fetcher or fetch_instagram_profiles_batched
        processed=0
        stage_size=max(1,initial_limit)
        while processed<hard_limit:
            if time.monotonic()-started>=max(60,int(config.max_runtime_seconds)):
                errors.append("전체 실행시간 상한 도달 · 미검증 후보는 검토대기로 보존")
                break
            requested=verification_order[processed:min(processed+stage_size,hard_limit)]
            if not requested:
                break
            apify_stages+=1
            raw_profiles=[]
            requested_handles=[str(row.get("handle") or "") for row in requested]
            handles_to_fetch=requested_handles
            # Cache only the real client path. Injected test fetchers must stay
            # deterministic and isolated from earlier tests.
            if apify_fetcher is None:
                cached,handles_to_fetch=_cached_apify_profiles(requested_handles)
                raw_profiles.extend(cached)
                apify_cache_hits+=len(cached)
            apify_requested+=len(handles_to_fetch)
            transient_profile_failure=False
            try:
                if handles_to_fetch:
                    fetched=fetcher(
                        handles_to_fetch,
                        token=apify_token,
                        batch_size=config.apify_batch_size,
                        progress=progress,
                    )
                    raw_profiles.extend(fetched)
                    if apify_fetcher is None:
                        _store_apify_profiles(fetched)
            except ApifyTransientError as exc:
                # Discovery results remain useful. Preserve them as review
                # candidates instead of losing the complete run.
                errors.append(f"Apify 프로필 검증 일시 지연 · 부분결과 보존 · {str(exc)[:180]}")
                transient_profile_failure=True
            except ApifyError:
                raise
            except Exception as exc:
                raise ApifyError(f"Apify 검증 실패 · {type(exc).__name__}: {str(exc)[:240]}") from None
            raw_by_handle={}
            for raw in raw_profiles:
                key=_apify_input_handle(raw)
                if key:
                    raw_by_handle[key]=raw
                returned=str(raw.get("username") or "").casefold().strip()
                if returned and returned not in raw_by_handle:
                    raw_by_handle[returned]=raw
            for row in requested:
                handle=str(row.get("handle") or "").casefold()
                raw=raw_by_handle.get(handle) or {
                    "inputUrl":profile_url(handle),
                    "error":"PROFILE_NOT_RETURNED",
                    "errorDescription":"Apify 응답에 해당 프로필이 없습니다.",
                }
                evaluation=evaluate_apify_profile(raw,config)
                check_status="Apify 검증완료" if evaluation.get("returned_handle") else "Apify 확인불가"
                profile_check_counts["total"]+=1
                profile_check_counts[check_status]=profile_check_counts.get(check_status,0)+1
                checked,reason=_apply_apify_verification(row,evaluation,config)
                if not checked:
                    pool.pop(handle,None)
                    rejected[reason]=rejected.get(reason,0)+1
                    rejected_records.append({
                        "handle":handle,"reason":reason,"title":row.get("display_name",""),
                        "evidence":evaluation.get("reason") or row.get("bio_summary",""),
                        "source_url":row.get("source_url",""),"query":row.get("discovery_query",""),
                    })
                else:
                    pool[handle]=checked
            processed+=len(requested)
            verified_eligible=sum(
                row.get("grade") in {"A","B"}
                and row.get("profile_verification_status")=="Apify 검증완료"
                for row in pool.values()
            )
            if verified_eligible>=int(config.target_count):
                break
            if transient_profile_failure or time.monotonic()-started>=max(60,int(config.max_runtime_seconds)):
                errors.append("후속 Apify 검증 생략 · 미검증 후보는 검토대기로 보존")
                break
        for row in verification_order[processed:]:
            current=pool.get(row["handle"])
            if current:
                current.update({
                    "profile_verification_status":"미실행","grade":"C",
                    "profile_review_reason":"Apify 검증 상한 초과",
                })
    elif config.verify_original_profiles:
        verification_order=sorted(
            pool.values(),
            key=lambda row:(row.get("grade") in {"A","B"},row.get("score",0)),
            reverse=True,
        )
        max_profile_checks=min(len(verification_order),max(20,config.target_count*2),40)
        for check_index,row in enumerate(verification_order[:max_profile_checks],1):
            if progress:
                progress(check_index-1,max_profile_checks,f"Instagram 원본 프로필 확인 {check_index}/{max_profile_checks}")
            verification=verify_original_profile(row,fetcher=profile_fetcher)
            check_status=verification.get("profile_verification_status") or "확인 오류"
            profile_check_counts["total"]+=1
            profile_check_counts[check_status]=profile_check_counts.get(check_status,0)+1
            checked,reason=_apply_original_verification(row,verification,config)
            if not checked:
                pool.pop(row["handle"],None)
                rejected[reason]=rejected.get(reason,0)+1
                rejected_records.append({
                    "handle":row.get("handle",""),"reason":reason,
                    "title":row.get("display_name",""),
                    "evidence":verification.get("profile_verification_evidence","") or row.get("bio_summary",""),
                    "source_url":row.get("source_url",""),
                    "query":row.get("discovery_query",""),
                })
            else:
                pool[row["handle"]]=checked
            time.sleep(max(0,min(float(config.request_delay),0.5)))
        for row in verification_order[max_profile_checks:]:
            current=pool.get(row["handle"])
            if current:
                current.update({
                    "profile_verification_status":"미실행",
                    "profile_verification_http":"",
                    "profile_verification_url":current.get("instagram_url",""),
                    "profile_verification_evidence":"검증 상한 초과",
                    "profile_checked_at":"",
                    "grade":"C",
                    "profile_review_reason":"원본 확인 미실행",
                })
    ranked=sorted(pool.values(),key=lambda row:(row["score"],row.get("followers") if isinstance(row.get("followers"),int) else -1),reverse=True)
    qualified=[row for row in ranked if row.get("grade") in {"A","B"}]
    review=[row for row in ranked if row.get("grade")=="C"]
    records=[]
    for rank,row in enumerate(qualified[:config.target_count],1):
        records.append(generate_outreach({**row,"rank":rank},sender,product))
    review_records=[]
    for rank,row in enumerate(review,1):
        review_records.append(generate_outreach({**row,"rank":rank},sender,product))
    if progress:
        progress(max_calls,max_calls,"Instagram 후보 정리 완료")
    all_candidates=records+review_records
    summary={
        "records":len(records),
        "tier_a":sum(r["grade"]=="A" for r in records),
        "tier_b":sum(r["grade"]=="B" for r in records),
        "tier_c":sum(r["grade"]=="C" for r in records),
        "public_emails":sum(bool(r.get("public_email")) for r in records),
        "dm_drafts":sum(r.get("outreach_generation_status")=="초안 생성완료" for r in records),
        "country_verified":sum(r.get("country_status")=="확인됨" for r in all_candidates),
        "country_unverified":sum(r.get("country_status")=="위치 미확인" for r in all_candidates),
        "review_records":len(review_records),
        "raw_results":raw_results,
        "discovery_results":discovery_results,
        "verification_results":verification_results,
        "external_pages_checked":external_check_counts["attempted"],
        "external_pages_verified":external_check_counts["verified"],
        "external_pages_failed":external_check_counts["failed"],
        "query_calls":len(executed_queries),
        "instagram_discovery_calls":instagram_discovery_calls,
        "instagram_search_results":instagram_search_results,
        "instagram_hashtag_posts":instagram_hashtag_posts,
        "instagram_discovered_handles":len(instagram_discovered_handles),
        "web_fallback_calls":web_fallback_calls,
        "profile_checks":profile_check_counts["total"],
        "profile_verified":profile_check_counts["자동검증 완료"],
        "apify_verified":profile_check_counts["Apify 검증완료"],
        "apify_unavailable":profile_check_counts["Apify 확인불가"],
        "apify_requested":apify_requested,
        "apify_cache_hits":apify_cache_hits,
        "apify_stages":apify_stages,
        "profile_access_limited":profile_check_counts["접근 제한"],
        "profile_check_errors":profile_check_counts["확인 오류"],
        "profile_missing":profile_check_counts["계정 없음"],
        "profile_mismatched":profile_check_counts["정보 불일치"],
        "unique_profiles":len(pool),
        "elapsed_seconds":round(time.monotonic()-started,2),
    }
    quality_gate_required=min(config.target_count,max(10,math.ceil(config.target_count*0.5)))
    summary["quality_gate_required"]=quality_gate_required
    summary["quality_gate_passed"]=(
        len(records)>=quality_gate_required
        and all(r.get("country_status")=="확인됨" for r in records)
        and all(r.get("profile_type") in {"인플루언서·KOL","KOC·리뷰어","개인 크리에이터"} for r in records)
        and all(isinstance(r.get("followers"),int) and r.get("followers")>=1_000 for r in records)
        and (not (config.verify_original_profiles or config.use_apify_verification) or all(
            r.get("profile_verification_status") in {"자동검증 완료","Apify 검증완료"}
            or (r.get("profile_verification_status")=="접근 제한" and _search_evidence_qualified(r))
            for r in records
        ))
    )
    deduped_rejected_records=_dedupe_rejected_records(rejected_records)
    return {
        "schema_version":SCHEMA_VERSION,
        "app_version":APP_VERSION,
        "mode":"instagram_influencer",
        "config":asdict(config),
        "records":records,
        "review_records":review_records,
        "summary":summary,
        "queries":native_queries+executed_queries,
        "rejected_reasons":rejected,
        "rejected_records":deduped_rejected_records,
        "query_result_counts":query_result_counts,
        "api_response_log":native_api_log+(list(getattr(provider,"response_log",[])) if provider is not None else []),
        "errors":errors,
        "sender_profile":sender,
        "product_profile":product,
        "safety":{
            "instagram_login_used":False,
            "instagram_public_profile_checked":bool(config.verify_original_profiles or config.use_apify_verification),
            "apify_profile_verification_used":bool(config.use_apify_verification),
            "apify_instagram_discovery_used":bool(config.use_apify_discovery),
            "external_public_pages_checked":bool(config.verify_external_evidence),
            "access_restriction_bypassed":False,
            "follower_list_collected":False,
            "email_guessed":False,
            "automatic_dm_sent":False,
            "automatic_email_sent":False,
        },
    }
