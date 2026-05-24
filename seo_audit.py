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
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]

WEBSITES = [url.strip() for url in WEBSITES_RAW.split(",") if url.strip()]
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]

GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

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
    data["base_score"] = max(score, 0)

    return data


# ─────────────────────────────────────────────
# GROQ AI ANALYSIS
# ─────────────────────────────────────────────

def ask_groq_expert(seo_data: dict) -> dict:
    """Send scraped SEO data to Groq and get expert AI analysis."""

    user_prompt = f"""Analyze this webpage SEO data and give me your expert verdict:

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

    fallback = {
        "expert_summary": "AI analysis unavailable.",
        "title_suggestion": "N/A",
        "meta_suggestion": "N/A",
        "top_issues": [],
        "quick_wins": "N/A",
        "ai_score": seo_data["base_score"],
    }

    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        return json.loads(content)

    except Exception as e:
        print(f"  ⚠️  Groq API error: {e}")
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
# MAIN
# ─────────────────────────────────────────────

def main():
    today      = datetime.date.today().strftime("%Y-%m-%d")
    audited_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sheet_name = f"SEO Audit {today}"

    print(f"\n🦴 SEO AUDIT + AI EXPERT — {audited_at}")
    print(f"   Model    : {GROQ_MODEL}")
    print(f"   Websites : {WEBSITES}")
    print(f"   Sheet    : {sheet_name}\n")

    service = get_sheets_service()

    all_rows = []
    for i, url in enumerate(WEBSITES):
        print(f"\n  [{i+1}/{len(WEBSITES)}] Auditing: {url}")

        print("    → Scraping page...")
        seo_data = scrape_seo_data(url)
        print(f"    → Base Score: {seo_data['base_score']}/100")

        print("    → Asking AI SEO Expert (Groq)...")
        ai_analysis = ask_groq_expert(seo_data)
        print(f"    → AI Expert Score: {ai_analysis.get('ai_score', '?')}/100")
        print(f"    → Summary: {ai_analysis.get('expert_summary', '')[:100]}...")

        row = build_row(seo_data, ai_analysis, audited_at)
        all_rows.append(row)

        # Respect Groq free tier rate limits between requests
        if i < len(WEBSITES) - 1:
            time.sleep(3)

    write_to_sheet(service, SPREADSHEET_ID, sheet_name, all_rows)
    print(f"\n🎉 All done! Check Google Sheet: SEO Audit {today} — appended {len(all_rows)} row(s)")


if __name__ == "__main__":
    main()
