import os
import json
import datetime
import requests
import time
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ─────────────────────────────────────────────
# CONFIG — loaded from GitHub Secrets
# ─────────────────────────────────────────────

CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SPREADSHEET_ID   = os.environ["SPREADSHEET_ID"]
WEBSITES_RAW     = os.environ["WEBSITES"]
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")  # fallback key

WEBSITES = [url.strip() for url in WEBSITES_RAW.split(",") if url.strip()]
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]

GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# OpenRouter fallback (used when Groq hits daily rate limit)
OPENROUTER_MODEL   = "meta-llama/llama-3.3-70b-instruct"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

SEO_EXPERT_SYSTEM_PROMPT = """You are Rex Morgan, a battle-hardened SEO expert with 15 years of experience.
You have personally ranked hundreds of websites to #1 on Google — from small local businesses to Fortune 500 companies.
You live and breathe Google's algorithm updates: from Panda, Penguin, Hummingbird, BERT, MUM, to the latest 2025-2026 Helpful Content, Core Updates, and E-E-A-T signals.

Your job is to analyze a webpage's raw SEO data and deliver a sharp, actionable expert analysis.

RULES:
- Be direct, specific, and expert-level. No fluff.
- Always reference the LATEST Google algorithm signals (E-E-A-T, Helpful Content, Core Web Vitals, Semantic SEO, entity authority, topical depth) up to 2026.
- For every problem you find, explain WHY Google penalizes or ignores it based on current algorithm behavior.
- Give a concrete improved version for titles and meta descriptions when they are suboptimal.
- Prioritize issues by impact: HIGH / MEDIUM / LOW.
- Be the expert that gets sites to #1. Talk like someone who has done it 100 times.
- Keep each section tight — no filler words.

Respond ONLY in this exact JSON format, no markdown, no extra text:
{
  "expert_summary": "2-3 sentence overall verdict from the SEO expert",
  "title_suggestion": "improved title tag text here, or 'GOOD - no change needed'",
  "meta_suggestion": "improved meta description here, or 'GOOD - no change needed'",
  "top_issues": [
    {"priority": "HIGH|MEDIUM|LOW", "issue": "issue name", "reason": "why Google cares about this in 2025-2026", "fix": "exact fix to implement"},
    {"priority": "HIGH|MEDIUM|LOW", "issue": "issue name", "reason": "why Google cares about this in 2025-2026", "fix": "exact fix to implement"},
    {"priority": "HIGH|MEDIUM|LOW", "issue": "issue name", "reason": "why Google cares about this in 2025-2026", "fix": "exact fix to implement"}
  ],
  "quick_wins": "1-2 sentence list of the fastest things to fix this week for immediate ranking boost",
  "ai_score": 0
}

The ai_score is your expert score out of 100 based on overall SEO health."""


# ─────────────────────────────────────────────
# GOOGLE SHEETS AUTH
# ─────────────────────────────────────────────

def get_sheets_service():
    creds_dict = json.loads(CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


# ─────────────────────────────────────────────
# PAGE FETCHER
# ─────────────────────────────────────────────

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SEOAuditBot/1.0; +https://github.com)"
}

def fetch_page(url: str):
    try:
        resp = requests.get(url, headers=FETCH_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        return resp, soup
    except Exception as e:
        print(f"  ⚠️  Could not fetch {url}: {e}")
        return None, None


# ─────────────────────────────────────────────
# BASIC SEO SCRAPER
# ─────────────────────────────────────────────

def check_google_index(base_url: str) -> dict:
    """
    Check if the site is indexed in Google using a site: search query.
    Scrapes the Google search result count — no API key needed.
    Returns indexed status, approximate page count, and any issues.
    """
    from urllib.parse import urlparse, quote_plus
    parsed = urlparse(base_url)
    domain = parsed.netloc.replace("www.", "")
    query  = quote_plus(f"site:{domain}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    result = {
        "indexed": "Unknown",
        "count":   "Unknown",
        "issue":   "⚠️ Could not check",
    }

    try:
        resp = requests.get(
            f"https://www.google.com/search?q={query}&num=10&hl=en",
            headers=headers,
            timeout=15,
        )

        if resp.status_code == 429 or "detected unusual traffic" in resp.text.lower():
            result["indexed"] = "⚠️ Rate limited by Google"
            result["count"]   = "N/A"
            result["issue"]   = "⚠️ Google blocked the check — try again later"
            return result

        soup = BeautifulSoup(resp.text, "lxml")

        # Check for "did not match any documents" — definitive not-indexed signal
        no_results_signals = [
            "did not match any documents",
            "no results found",
            "your search did not match",
        ]
        page_text = soup.get_text(" ", strip=True).lower()
        if any(s in page_text for s in no_results_signals):
            result["indexed"] = "❌ Not indexed"
            result["count"]   = "0"
            result["issue"]   = "❌ Site has NO pages in Google — submit sitemap via Google Search Console immediately"
            return result

        # Try to find result count in the stats bar (e.g. "About 1,240 results")
        count_str = ""
        # Method 1: #result-stats div
        stats = soup.find(id="result-stats")
        if stats:
            count_str = stats.get_text(" ", strip=True)
        # Method 2: look for "About X results" pattern anywhere
        if not count_str:
            import re
            m = re.search(r"About ([\d,]+) result", resp.text)
            if m:
                count_str = m.group(1).replace(",", "")

        # Count actual result snippets as a fallback
        result_divs = soup.select("div.g, div[data-ved]")
        snippet_count = len([d for d in result_divs if d.find("h3")])

        if count_str:
            num = int("".join(filter(str.isdigit, count_str.split()[0]))) if count_str.split() else 0
            result["indexed"] = "✅ Indexed"
            result["count"]   = f"~{count_str.split()[0]} pages"
            result["issue"]   = "✅ OK"
        elif snippet_count > 0:
            result["indexed"] = "✅ Indexed"
            result["count"]   = f"~{snippet_count}+ pages visible"
            result["issue"]   = "✅ OK"
        else:
            # Could be indexed but Google didn't show count — inconclusive
            result["indexed"] = "⚠️ Possibly indexed"
            result["count"]   = "Unknown"
            result["issue"]   = "⚠️ Could not confirm — verify manually in Google Search Console"

    except Exception as e:
        result["indexed"] = "⚠️ Check failed"
        result["count"]   = "N/A"
        result["issue"]   = f"⚠️ Error: {e}"

    return result


def check_robots_txt(base_url: str) -> dict:
    """Fetch and analyse /robots.txt for the site."""
    from urllib.parse import urlparse, urljoin
    parsed   = urlparse(base_url)
    root     = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(root, "/robots.txt")
    result   = {"url": robots_url, "status": "", "issue": "", "sitemap_hint": ""}
    try:
        resp = requests.get(robots_url, headers=FETCH_HEADERS, timeout=10)
        if resp.status_code == 200:
            text = resp.text
            result["status"] = f"✅ Found ({len(text.splitlines())} lines)"
            result["issue"]  = "✅ OK"
            # Extract sitemap directives from robots.txt
            sitemap_lines = [l.strip() for l in text.splitlines()
                             if l.strip().lower().startswith("sitemap:")]
            if sitemap_lines:
                result["sitemap_hint"] = sitemap_lines[0].split(":", 1)[1].strip()
        elif resp.status_code == 404:
            result["status"] = "❌ Not found (404)"
            result["issue"]  = "❌ Missing — Google can't find crawl rules"
        else:
            result["status"] = f"⚠️ HTTP {resp.status_code}"
            result["issue"]  = f"⚠️ Unexpected status {resp.status_code}"
    except Exception as e:
        result["status"] = "❌ Error fetching"
        result["issue"]  = f"❌ Error: {e}"
    return result


def check_sitemap(base_url: str, hint_url: str = "") -> dict:
    """
    Try to find a valid sitemap. Priority:
    1. URL hinted from robots.txt Sitemap: directive
    2. /sitemap.xml
    3. /sitemap_index.xml
    4. /sitemap.php
    5. Listed in <link rel=sitemap> tag on homepage (checked by caller)
    """
    from urllib.parse import urlparse, urljoin
    parsed = urlparse(base_url)
    root   = f"{parsed.scheme}://{parsed.netloc}"

    candidates = []
    if hint_url:
        candidates.append(hint_url)
    candidates += [
        urljoin(root, "/sitemap.xml"),
        urljoin(root, "/sitemap_index.xml"),
        urljoin(root, "/sitemap.php"),
        urljoin(root, "/sitemap/sitemap.xml"),
    ]

    for candidate in candidates:
        try:
            resp = requests.get(candidate, headers=FETCH_HEADERS, timeout=10)
            if resp.status_code == 200:
                ct   = resp.headers.get("Content-Type", "")
                text = resp.text[:2000]
                is_xml  = "xml" in ct or text.strip().startswith("<?xml") or "<urlset" in text or "<sitemapindex" in text
                is_index = "<sitemapindex" in text
                url_count = text.count("<url>")
                kind = "index" if is_index else "urlset"
                detail = f"{url_count}+ URLs visible" if url_count else "valid XML"
                return {
                    "url":    candidate,
                    "status": f"✅ Found ({kind}, {detail})" if is_xml else f"⚠️ Found but not valid XML",
                    "issue":  "✅ OK" if is_xml else "⚠️ File exists but may not be valid XML sitemap",
                }
        except Exception:
            continue

    return {
        "url":    "",
        "status": "❌ Not found",
        "issue":  "❌ No sitemap found — Google can't efficiently crawl all pages",
    }


def scrape_seo_data(url: str) -> dict:
    data = {
        "url": url,
        "status_code": "ERROR",
        "title": "", "title_length": 0, "title_issue": "",
        "meta_description": "", "meta_desc_length": 0, "meta_desc_issue": "",
        "h1_count": 0, "h1_text": "", "h1_issue": "",
        "h2_count": 0, "h2_texts": "",
        "images_total": 0, "images_missing_alt": 0, "images_issue": "",
        "canonical": "", "canonical_issue": "",
        "robots_meta": "",
        "open_graph": "",
        "page_text_snippet": "",
        "internal_links": 0,
        "external_links": 0,
        "word_count": 0,
        "schema_markup": "",
        "robots_txt": "",
        "robots_txt_issue": "",
        "sitemap_url": "",
        "sitemap_status": "",
        "sitemap_issue": "",
        "base_score": 0,
    }

    resp, soup = fetch_page(url)
    if resp is None:
        return data

    data["status_code"] = resp.status_code

    # TITLE
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    data["title"] = title
    data["title_length"] = len(title)
    if not title:
        data["title_issue"] = "❌ Missing title tag"
    elif len(title) < 30:
        data["title_issue"] = "⚠️ Too short (< 30 chars)"
    elif len(title) > 60:
        data["title_issue"] = "⚠️ Too long (> 60 chars)"
    else:
        data["title_issue"] = "✅ OK"

    # META DESCRIPTION
    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta = meta_tag["content"].strip() if meta_tag and meta_tag.get("content") else ""
    data["meta_description"] = meta[:200]
    data["meta_desc_length"] = len(meta)
    if not meta:
        data["meta_desc_issue"] = "❌ Missing"
    elif len(meta) < 70:
        data["meta_desc_issue"] = "⚠️ Too short (< 70 chars)"
    elif len(meta) > 160:
        data["meta_desc_issue"] = "⚠️ Too long (> 160 chars)"
    else:
        data["meta_desc_issue"] = "✅ OK"

    # H1
    h1s = soup.find_all("h1")
    data["h1_count"] = len(h1s)
    data["h1_text"] = " | ".join(h.get_text(strip=True) for h in h1s)[:200]
    if len(h1s) == 0:
        data["h1_issue"] = "❌ No H1 found"
    elif len(h1s) > 1:
        data["h1_issue"] = f"⚠️ Multiple H1s ({len(h1s)})"
    else:
        data["h1_issue"] = "✅ OK"

    # H2
    h2s = soup.find_all("h2")
    data["h2_count"] = len(h2s)
    data["h2_texts"] = " | ".join(h.get_text(strip=True) for h in h2s[:5])[:300]

    # IMAGES
    imgs = soup.find_all("img")
    missing_alt = [i for i in imgs if not i.get("alt")]
    data["images_total"] = len(imgs)
    data["images_missing_alt"] = len(missing_alt)
    data["images_issue"] = f"⚠️ {len(missing_alt)} missing alt" if missing_alt else ("✅ OK" if imgs else "➖ No images")

    # CANONICAL
    canon = soup.find("link", attrs={"rel": "canonical"})
    data["canonical"] = canon["href"].strip() if canon and canon.get("href") else ""
    data["canonical_issue"] = "✅ OK" if data["canonical"] else "⚠️ Missing canonical"

    # ROBOTS
    robots = soup.find("meta", attrs={"name": "robots"})
    data["robots_meta"] = robots["content"].strip() if robots and robots.get("content") else "not set"
    if "noindex" in data["robots_meta"].lower():
        data["robots_meta"] = f"❌ NOINDEX — {data['robots_meta']}"

    # OPEN GRAPH
    og_parts = []
    if soup.find("meta", property="og:title"):   og_parts.append("title")
    if soup.find("meta", property="og:description"): og_parts.append("desc")
    if soup.find("meta", property="og:image"):   og_parts.append("image")
    data["open_graph"] = f"✅ ({', '.join(og_parts)})" if og_parts else "⚠️ Missing OG tags"

    # SCHEMA
    schema_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
    data["schema_markup"] = f"✅ {len(schema_tags)} schema block(s) found" if schema_tags else "⚠️ No schema/structured data"

    # LINKS
    from urllib.parse import urlparse
    base_domain = urlparse(url).netloc
    all_links = soup.find_all("a", href=True)
    internal = [a for a in all_links if base_domain in a["href"] or a["href"].startswith("/")]
    data["internal_links"] = len(internal)
    data["external_links"] = len(all_links) - len(internal)

    # WORD COUNT + TEXT SNIPPET
    body_text = soup.get_text(separator=" ", strip=True)
    words = body_text.split()
    data["word_count"] = len(words)
    data["page_text_snippet"] = " ".join(words[:300])  # first 300 words for AI context

    # ROBOTS.TXT
    from urllib.parse import urlparse as _urlparse
    _parsed  = _urlparse(url)
    _root    = f"{_parsed.scheme}://{_parsed.netloc}"
    robots_result = check_robots_txt(_root)
    data["robots_txt"]       = robots_result["status"]
    data["robots_txt_issue"] = robots_result["issue"]

    # SITEMAP — check robots.txt hint first, then common paths, then <link rel=sitemap>
    hint = robots_result.get("sitemap_hint", "")
    # Also check for <link rel="sitemap"> in HTML
    sitemap_link_tag = soup.find("link", attrs={"rel": "sitemap"})
    if not hint and sitemap_link_tag and sitemap_link_tag.get("href"):
        hint = sitemap_link_tag["href"].strip()
        if hint.startswith("/"):
            hint = _root + hint
    sitemap_result = check_sitemap(_root, hint)
    data["sitemap_url"]    = sitemap_result["url"]
    data["sitemap_status"] = sitemap_result["status"]
    data["sitemap_issue"]  = sitemap_result["issue"]

    # BASE SCORE
    score = 100
    if not title:                              score -= 20
    elif "⚠️" in data["title_issue"]:         score -= 5
    if not meta:                               score -= 20
    elif "⚠️" in data["meta_desc_issue"]:     score -= 5
    if data["h1_count"] == 0:                  score -= 15
    elif data["h1_count"] > 1:                 score -= 5
    if data["images_missing_alt"]:             score -= 10
    if not data["canonical"]:                  score -= 5
    if not og_parts:                           score -= 5
    if "noindex" in data["robots_meta"].lower(): score -= 20
    if not schema_tags:                        score -= 5
    if data["word_count"] < 300:               score -= 10
    if "❌" in data["robots_txt_issue"]:       score -= 5
    if "❌" in data["sitemap_issue"]:          score -= 5
    data["base_score"] = max(score, 0)

    return data


# ─────────────────────────────────────────────
# AI ANALYSIS (OpenRouter → Groq fallback)
# ─────────────────────────────────────────────

def _build_user_prompt(seo_data: dict) -> str:
    return f"""Analyze this webpage SEO data and give me your expert verdict:

URL: {seo_data['url']}
Status Code: {seo_data['status_code']}

--- ON-PAGE SIGNALS ---
Title: "{seo_data['title']}" ({seo_data['title_length']} chars) — {seo_data['title_issue']}
Meta Description: "{seo_data['meta_description']}" ({seo_data['meta_desc_length']} chars) — {seo_data['meta_desc_issue']}
H1 Tags ({seo_data['h1_count']}): {seo_data['h1_text']}
H2 Tags ({seo_data['h2_count']}): {seo_data['h2_texts']}
Word Count: {seo_data['word_count']} words

--- TECHNICAL SEO ---
Canonical URL: {seo_data['canonical']} — {seo_data['canonical_issue']}
Robots Meta: {seo_data['robots_meta']}
Robots.txt: {seo_data['robots_txt']} — {seo_data['robots_txt_issue']}
Sitemap: {seo_data['sitemap_url']} — {seo_data['sitemap_status']} — {seo_data['sitemap_issue']}
Google Index Status: {seo_data['google_indexed']} | Pages indexed: {seo_data['google_indexed_count']} — {seo_data['google_indexed_issue']}
Schema Markup: {seo_data['schema_markup']}
Images: {seo_data['images_total']} total, {seo_data['images_missing_alt']} missing alt — {seo_data['images_issue']}

--- CONTENT & LINKS ---
Internal Links: {seo_data['internal_links']}
External Links: {seo_data['external_links']}
Open Graph: {seo_data['open_graph']}

--- PAGE CONTENT SAMPLE (first 300 words) ---
{seo_data['page_text_snippet']}

Base Technical Score: {seo_data['base_score']}/100

Give me your full expert SEO analysis in the JSON format specified."""


def _parse_ai_response(content: str) -> dict:
    """Strip markdown fences and parse JSON from AI response."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)


def _is_rate_limit_error(resp: requests.Response) -> bool:
    """Return True if the response signals a Groq daily rate limit."""
    if resp.status_code == 429:
        return True
    # Groq also returns 413 / error codes for token-based limits
    try:
        body = resp.json()
        error_type = body.get("error", {}).get("type", "")
        error_msg  = str(body.get("error", {}).get("message", "")).lower()
        if error_type in ("rate_limit_exceeded", "tokens_exceeded"):
            return True
        if "rate limit" in error_msg or "daily limit" in error_msg or "quota" in error_msg:
            return True
    except Exception:
        pass
    return False


def _call_groq(user_prompt: str) -> dict:
    """Send request to Groq as fallback provider."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY secret is not set — cannot fall back.")

    print("    → 🔀 OpenRouter limit hit — switching to Groq...")
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SEO_EXPERT_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1500,
    }
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    print("    → ✅ Used Groq (fallback)")
    return _parse_ai_response(content)


def ask_groq_expert(seo_data: dict) -> dict:
    """Send scraped SEO data to OpenRouter (with Groq fallback) and get expert AI analysis."""

    user_prompt = _build_user_prompt(seo_data)

    fallback = {
        "expert_summary": "AI analysis unavailable.",
        "title_suggestion": "N/A",
        "meta_suggestion": "N/A",
        "top_issues": [],
        "quick_wins": "N/A",
        "ai_score": seo_data["base_score"],
    }

    # ── 1. Try OpenRouter first ──────────────────────────────────────────────
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com",  # recommended by OpenRouter
            "X-Title": "SEO Audit Bot",
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": SEO_EXPERT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 1500,
        }

        resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=30)

        # ── 2. Detect rate/credit limit → fall back to Groq ─────────────────
        if _is_rate_limit_error(resp):
            return _call_groq(user_prompt)

        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        print("    → ✅ Used OpenRouter")
        return _parse_ai_response(content)

    except requests.HTTPError as e:
        print(f"  ⚠️  OpenRouter HTTP error: {e}")
        # One more chance: try Groq before giving up entirely
        try:
            return _call_groq(user_prompt)
        except Exception as gr_err:
            print(f"  ⚠️  Groq also failed: {gr_err}")
            return fallback

    except Exception as e:
        print(f"  ⚠️  AI API error: {e}")
        return fallback


# ─────────────────────────────────────────────
# GOOGLE SHEETS WRITER
# ─────────────────────────────────────────────

COLUMN_HEADERS = [
    # Basic Info
    "Audited At", "URL", "Status Code",
    # Title
    "Title", "Title Length", "Title Issue", "💡 AI Title Suggestion",
    # Meta
    "Meta Description", "Meta Desc Length", "Meta Desc Issue", "💡 AI Meta Suggestion",
    # Headings & Content
    "H1 Count", "H1 Text", "H1 Issue",
    "H2 Count", "Word Count",
    # Technical
    "Canonical URL", "Canonical Issue",
    "Robots Meta", "Schema Markup",
    "Robots.txt Status", "Robots.txt Issue",
    "Sitemap URL", "Sitemap Status", "Sitemap Issue",
    # Media & Links
    "Images Total", "Images Missing Alt", "Images Issue",
    "Internal Links", "External Links",
    # Social
    "Open Graph",
    # AI Expert Analysis
    "🧠 Expert Summary",
    "🔴 Issue #1 (Priority | Issue | Reason | Fix)",
    "🟡 Issue #2 (Priority | Issue | Reason | Fix)",
    "🟢 Issue #3 (Priority | Issue | Reason | Fix)",
    "⚡ Quick Wins This Week",
    # Scores
    "Base Score (/100)", "🏆 AI Expert Score (/100)",
]


def format_issue(issue: dict) -> str:
    if not issue:
        return ""
    priority = issue.get('priority', '?')
    icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(priority, "⚪")
    return (
        f"{icon} {priority} PRIORITY\n"
        f"Issue: {issue.get('issue', '')}\n"
        f"Why: {issue.get('reason', '')}\n"
        f"Fix: {issue.get('fix', '')}"
    )


def build_row(seo: dict, ai: dict, audited_at: str) -> list:
    issues = ai.get("top_issues", [{}, {}, {}])
    while len(issues) < 3:
        issues.append({})

    return [
        audited_at,
        seo["url"],
        seo["status_code"],
        seo["title"],
        seo["title_length"],
        seo["title_issue"],
        ai.get("title_suggestion", ""),
        seo["meta_description"],
        seo["meta_desc_length"],
        seo["meta_desc_issue"],
        ai.get("meta_suggestion", ""),
        seo["h1_count"],
        seo["h1_text"],
        seo["h1_issue"],
        seo["h2_count"],
        seo["word_count"],
        seo["canonical"],
        seo["canonical_issue"],
        seo["robots_meta"],
        seo["schema_markup"],
        seo["robots_txt"],
        seo["robots_txt_issue"],
        seo["sitemap_url"],
        seo["sitemap_status"],
        seo["sitemap_issue"],
        seo["images_total"],
        seo["images_missing_alt"],
        seo["images_issue"],
        seo["internal_links"],
        seo["external_links"],
        seo["open_graph"],
        ai.get("expert_summary", ""),
        format_issue(issues[0]),
        format_issue(issues[1]),
        format_issue(issues[2]),
        ai.get("quick_wins", ""),
        seo["base_score"],
        ai.get("ai_score", seo["base_score"]),
    ]


def create_or_get_sheet(service, spreadsheet_id: str, sheet_name: str) -> int:
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in spreadsheet["sheets"]}
    if sheet_name in existing:
        print(f"  Sheet '{sheet_name}' already exists.")
        return existing[sheet_name]
    body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
    resp = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    new_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    print(f"  ✅ Created sheet: '{sheet_name}'")
    return new_id


def sheet_has_header(service, spreadsheet_id: str, sheet_name: str) -> bool:
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:A1",
    ).execute()
    return bool(result.get("values"))


def get_next_empty_row(service, spreadsheet_id: str, sheet_name: str) -> int:
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:A",
    ).execute()
    return len(result.get("values", [])) + 1


def write_to_sheet(service, spreadsheet_id: str, sheet_name: str, rows: list):
    sheet_id = create_or_get_sheet(service, spreadsheet_id, sheet_name)
    is_new   = not sheet_has_header(service, spreadsheet_id, sheet_name)

    if is_new:
        values     = [COLUMN_HEADERS] + rows
        write_range = f"'{sheet_name}'!A1"
        data_start  = 2  # row 2 = first data row (1-based)
    else:
        next_row    = get_next_empty_row(service, spreadsheet_id, sheet_name)
        values      = rows
        write_range = f"'{sheet_name}'!A{next_row}"
        data_start  = next_row

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=write_range,
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    # ── Column index helpers ──────────────────────────────────────────────────
    col = {name: i for i, name in enumerate(COLUMN_HEADERS)}

    # Columns that hold long AI text — cap at 350px wide, rest narrow/medium
    wide_cols  = {col["🧠 Expert Summary"], col["⚡ Quick Wins This Week"],
                  col["💡 AI Title Suggestion"], col["💡 AI Meta Suggestion"],
                  col["🔴 Issue #1 (Priority | Issue | Reason | Fix)"],
                  col["🟡 Issue #2 (Priority | Issue | Reason | Fix)"],
                  col["🟢 Issue #3 (Priority | Issue | Reason | Fix)"]}
    medium_cols = {col["URL"], col["Title"], col["Meta Description"],
                   col["H1 Text"], col["Canonical URL"]}

    def col_width_request(col_index, pixel_width):
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_index,
                    "endIndex": col_index + 1,
                },
                "properties": {"pixelSize": pixel_width},
                "fields": "pixelSize",
            }
        }

    column_width_requests = []
    for i in range(len(COLUMN_HEADERS)):
        if i in wide_cols:
            column_width_requests.append(col_width_request(i, 350))
        elif i in medium_cols:
            column_width_requests.append(col_width_request(i, 220))
        else:
            column_width_requests.append(col_width_request(i, 110))

    # ── Row height — taller data rows so wrapped text breathes ───────────────
    row_height_requests = []
    if rows:
        row_height_requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": data_start - 1,
                    "endIndex": data_start - 1 + len(rows),
                },
                "properties": {"pixelSize": 120},
                "fields": "pixelSize",
            }
        })

    # ── Data rows: wrap text + top-align + explicit white bg ────────────────
    data_wrap_request = {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": data_start - 1,
                "endRowIndex": data_start - 1 + len(rows),
            },
            "cell": {
                "userEnteredFormat": {
                    "wrapStrategy": "WRAP",
                    "verticalAlignment": "TOP",
                    "textFormat": {"fontSize": 9, "foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1}},
                    "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                }
            },
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,textFormat,backgroundColor)",
        }
    }

    # ── Highlight AI / issue columns with subtle background ──────────────────
    def bg_col_request(col_index, r, g, b):
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": data_start - 1,
                    "endRowIndex": data_start - 1 + len(rows),
                    "startColumnIndex": col_index,
                    "endColumnIndex": col_index + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": r, "green": g, "blue": b}
                    }
                },
                "fields": "userEnteredFormat(backgroundColor)",
            }
        }

    color_requests = [
        # Expert summary — soft blue
        bg_col_request(col["🧠 Expert Summary"], 0.90, 0.95, 1.0),
        # Quick wins — soft green
        bg_col_request(col["⚡ Quick Wins This Week"], 0.90, 1.0, 0.92),
        # Issue columns — soft red / yellow / green tint
        bg_col_request(col["🔴 Issue #1 (Priority | Issue | Reason | Fix)"], 1.0, 0.92, 0.92),
        bg_col_request(col["🟡 Issue #2 (Priority | Issue | Reason | Fix)"], 1.0, 0.98, 0.88),
        bg_col_request(col["🟢 Issue #3 (Priority | Issue | Reason | Fix)"], 0.92, 1.0, 0.92),
        # Score columns — light gold
        bg_col_request(col["Base Score (/100)"],          0.99, 0.97, 0.82),
        bg_col_request(col["🏆 AI Expert Score (/100)"],  0.99, 0.97, 0.82),
    ]

    # ── Alternate row banding for easier reading ──────────────────────────────
    banding_request = {
        "addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": data_start - 1,
                    "endRowIndex": data_start - 1 + len(rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": len(COLUMN_HEADERS),
                },
                "rowProperties": {
                    "firstBandColor":  {"red": 1.0,  "green": 1.0,  "blue": 1.0},
                    "secondBandColor": {"red": 0.95, "green": 0.96, "blue": 0.98},
                },
            }
        }
    }

    fmt_requests = [
        # ── Dark header row ──────────────────────────────────────────────────
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.13, "green": 0.13, "blue": 0.13},
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                            "fontSize": 10,
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        # ── Freeze header + first column ─────────────────────────────────────
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        banding_request,
        data_wrap_request,
        *column_width_requests,
        *row_height_requests,
        *color_requests,
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": fmt_requests},
    ).execute()

    print(f"  ✅ Wrote {len(rows)} row(s) to '{sheet_name}'")


# ─────────────────────────────────────────────
# SITEMAP CRAWLER — discover all pages to audit
# ─────────────────────────────────────────────

def _parse_urls_from_sitemap(xml_text: str) -> list[str]:
    """Extract all <loc> URLs from a sitemap or sitemap index XML."""
    soup = BeautifulSoup(xml_text, "lxml-xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def get_pages_from_sitemap(base_url: str, max_pages: int = 200) -> list[str]:
    """
    Discover every page URL for a site via its sitemap.
    Handles sitemap indexes (recursively fetches child sitemaps).
    Falls back to crawling internal links from the homepage if no sitemap found.
    Returns a deduplicated list of URLs, capped at max_pages.
    """
    from urllib.parse import urlparse, urljoin

    parsed = urlparse(base_url)
    root   = f"{parsed.scheme}://{parsed.netloc}"

    # ── Step 1: find the sitemap ────────────────────────────────────────────
    robots_result  = check_robots_txt(root)
    sitemap_hint   = robots_result.get("sitemap_hint", "")
    sitemap_result = check_sitemap(root, sitemap_hint)
    sitemap_url    = sitemap_result.get("url", "")

    all_urls: list[str] = []

    if sitemap_url:
        print(f"    → 🗺️  Sitemap found: {sitemap_url}")
        try:
            resp = requests.get(sitemap_url, headers=FETCH_HEADERS, timeout=15)
            resp.raise_for_status()
            xml  = resp.text

            # ── Sitemap index? Recurse into child sitemaps ──────────────────
            if "<sitemapindex" in xml:
                child_urls = _parse_urls_from_sitemap(xml)
                print(f"    → 📂 Sitemap index with {len(child_urls)} child sitemap(s)")
                for child_url in child_urls:
                    try:
                        cr = requests.get(child_url, headers=FETCH_HEADERS, timeout=15)
                        cr.raise_for_status()
                        all_urls.extend(_parse_urls_from_sitemap(cr.text))
                        if len(all_urls) >= max_pages:
                            break
                    except Exception as e:
                        print(f"    ⚠️  Could not fetch child sitemap {child_url}: {e}")
            else:
                all_urls = _parse_urls_from_sitemap(xml)

        except Exception as e:
            print(f"    ⚠️  Sitemap fetch failed: {e}")

    # ── Step 2: fallback — crawl homepage links ─────────────────────────────
    if not all_urls:
        print(f"    → 🕷️  No sitemap — crawling homepage links as fallback...")
        try:
            _, soup = fetch_page(root)
            if soup:
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith("/"):
                        href = root + href
                    if href.startswith(root) and href not in all_urls:
                        all_urls.append(href)
        except Exception as e:
            print(f"    ⚠️  Homepage crawl failed: {e}")

    # ── Step 3: filter to same domain, deduplicate, cap ─────────────────────
    seen = set()
    filtered = []
    for u in all_urls:
        # Keep only URLs on the same domain, skip anchors/feeds/assets
        if urlparse(u).netloc != parsed.netloc:
            continue
        if any(u.endswith(ext) for ext in (".xml", ".pdf", ".jpg", ".png", ".gif", ".zip")):
            continue
        if "#" in u:
            u = u.split("#")[0]
        if u and u not in seen:
            seen.add(u)
            filtered.append(u)
        if len(filtered) >= max_pages:
            break

    # Always ensure the root homepage is included
    if root + "/" not in seen and root not in seen:
        filtered.insert(0, root)

    print(f"    → 📄 {len(filtered)} unique page(s) discovered (cap: {max_pages})")
    return filtered


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    today      = datetime.date.today().strftime("%Y-%m-%d")
    audited_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sheet_name = f"SEO Audit {today}"

    print(f"\n🦴 SEO AUDIT + AI EXPERT — {audited_at}")
    print(f"   Primary  : OpenRouter ({OPENROUTER_MODEL})")
    print(f"   Fallback : Groq ({GROQ_MODEL})")
    print(f"   Websites : {WEBSITES}")
    print(f"   Sheet    : {sheet_name}\n")

    service  = get_sheets_service()
    all_rows = []

    for site_url in WEBSITES:
        print(f"\n🌐 Discovering pages for: {site_url}")
        pages = get_pages_from_sitemap(site_url, max_pages=200)
        print(f"   → Auditing {len(pages)} page(s)...\n")

        for i, url in enumerate(pages):
            print(f"  [{i+1}/{len(pages)}] Auditing: {url}")

            print("    → Scraping page...")
            seo_data = scrape_seo_data(url)
            print(f"    → Base Score: {seo_data['base_score']}/100")

            print("    → Asking AI SEO Expert...")
            ai_analysis = ask_groq_expert(seo_data)
            print(f"    → AI Expert Score: {ai_analysis.get('ai_score', '?')}/100")
            print(f"    → Summary: {ai_analysis.get('expert_summary', '')[:100]}...")

            row = build_row(seo_data, ai_analysis, audited_at)
            all_rows.append(row)

            # Be polite to servers — don't hammer too fast
            if i < len(pages) - 1:
                time.sleep(3)

    write_to_sheet(service, SPREADSHEET_ID, sheet_name, all_rows)
    print(f"\n🎉 All done! Check Google Sheet: SEO Audit {today} — audited {len(all_rows)} page(s) total")


if __name__ == "__main__":
    main()
