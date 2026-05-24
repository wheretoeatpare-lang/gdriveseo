import os
import json
import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ─────────────────────────────────────────────
# CONFIG — loaded from GitHub Secrets
# ─────────────────────────────────────────────

CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SPREADSHEET_ID   = os.environ["SPREADSHEET_ID"]
WEBSITES_RAW     = os.environ["WEBSITES"]  # comma-separated URLs

WEBSITES = [url.strip() for url in WEBSITES_RAW.split(",") if url.strip()]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

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
# SEO AUDIT LOGIC
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SEOAuditBot/1.0; +https://github.com)"
    )
}


def fetch_page(url: str):
    """Fetch a page and return (response, BeautifulSoup) or (None, None)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        return resp, soup
    except Exception as e:
        print(f"  ⚠️  Could not fetch {url}: {e}")
        return None, None


def audit_page(url: str) -> dict:
    """Run basic SEO checks on a single URL."""
    result = {
        "url": url,
        "status_code": "ERROR",
        "title": "",
        "title_length": 0,
        "title_issue": "",
        "meta_description": "",
        "meta_desc_length": 0,
        "meta_desc_issue": "",
        "h1_count": 0,
        "h1_text": "",
        "h1_issue": "",
        "h2_count": 0,
        "images_total": 0,
        "images_missing_alt": 0,
        "images_issue": "",
        "canonical": "",
        "canonical_issue": "",
        "robots_meta": "",
        "open_graph": "",
        "overall_score": 0,
        "notes": "",
    }

    resp, soup = fetch_page(url)
    if resp is None:
        result["notes"] = "Page could not be fetched"
        return result

    result["status_code"] = resp.status_code

    # ── TITLE ──────────────────────────────
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    result["title"] = title
    result["title_length"] = len(title)
    if not title:
        result["title_issue"] = "❌ Missing title tag"
    elif len(title) < 30:
        result["title_issue"] = "⚠️ Title too short (< 30 chars)"
    elif len(title) > 60:
        result["title_issue"] = "⚠️ Title too long (> 60 chars)"
    else:
        result["title_issue"] = "✅ OK"

    # ── META DESCRIPTION ───────────────────
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else ""
    result["meta_description"] = meta_desc[:200]  # truncate for sheet
    result["meta_desc_length"] = len(meta_desc)
    if not meta_desc:
        result["meta_desc_issue"] = "❌ Missing meta description"
    elif len(meta_desc) < 70:
        result["meta_desc_issue"] = "⚠️ Meta desc too short (< 70 chars)"
    elif len(meta_desc) > 160:
        result["meta_desc_issue"] = "⚠️ Meta desc too long (> 160 chars)"
    else:
        result["meta_desc_issue"] = "✅ OK"

    # ── H1 TAGS ────────────────────────────
    h1_tags = soup.find_all("h1")
    result["h1_count"] = len(h1_tags)
    result["h1_text"] = " | ".join(h.get_text(strip=True) for h in h1_tags)[:200]
    if len(h1_tags) == 0:
        result["h1_issue"] = "❌ No H1 found"
    elif len(h1_tags) > 1:
        result["h1_issue"] = f"⚠️ Multiple H1s ({len(h1_tags)})"
    else:
        result["h1_issue"] = "✅ OK"

    # ── H2 TAGS ────────────────────────────
    result["h2_count"] = len(soup.find_all("h2"))

    # ── IMAGES ─────────────────────────────
    images = soup.find_all("img")
    missing_alt = [img for img in images if not img.get("alt")]
    result["images_total"] = len(images)
    result["images_missing_alt"] = len(missing_alt)
    if missing_alt:
        result["images_issue"] = f"⚠️ {len(missing_alt)} image(s) missing alt text"
    else:
        result["images_issue"] = "✅ OK" if images else "➖ No images"

    # ── CANONICAL ──────────────────────────
    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    canonical = canonical_tag["href"].strip() if canonical_tag and canonical_tag.get("href") else ""
    result["canonical"] = canonical
    result["canonical_issue"] = "✅ OK" if canonical else "⚠️ No canonical tag"

    # ── ROBOTS META ────────────────────────
    robots_tag = soup.find("meta", attrs={"name": "robots"})
    robots = robots_tag["content"].strip() if robots_tag and robots_tag.get("content") else "not set"
    result["robots_meta"] = robots
    if "noindex" in robots.lower():
        result["robots_meta"] = f"❌ NOINDEX — {robots}"

    # ── OPEN GRAPH ─────────────────────────
    og_title = soup.find("meta", property="og:title")
    og_desc  = soup.find("meta", property="og:description")
    og_image = soup.find("meta", property="og:image")
    og_parts = []
    if og_title: og_parts.append("title")
    if og_desc:  og_parts.append("desc")
    if og_image: og_parts.append("image")
    result["open_graph"] = f"✅ ({', '.join(og_parts)})" if og_parts else "⚠️ Missing OG tags"

    # ── SCORE ──────────────────────────────
    score = 100
    if not title:                    score -= 20
    elif "⚠️" in result["title_issue"]: score -= 5
    if not meta_desc:                score -= 20
    elif "⚠️" in result["meta_desc_issue"]: score -= 5
    if result["h1_count"] == 0:      score -= 15
    elif result["h1_count"] > 1:     score -= 5
    if result["images_missing_alt"]: score -= 10
    if not canonical:                score -= 5
    if not og_parts:                 score -= 5
    if "noindex" in robots.lower():  score -= 20
    result["overall_score"] = max(score, 0)

    return result


# ─────────────────────────────────────────────
# GOOGLE SHEETS WRITER
# ─────────────────────────────────────────────

COLUMN_HEADERS = [
    "URL",
    "Status Code",
    "Title",
    "Title Length",
    "Title Issue",
    "Meta Description",
    "Meta Desc Length",
    "Meta Desc Issue",
    "H1 Count",
    "H1 Text",
    "H1 Issue",
    "H2 Count",
    "Images Total",
    "Images Missing Alt",
    "Images Issue",
    "Canonical URL",
    "Canonical Issue",
    "Robots Meta",
    "Open Graph",
    "SEO Score (/100)",
    "Notes",
]


def result_to_row(r: dict) -> list:
    return [
        r["url"],
        r["status_code"],
        r["title"],
        r["title_length"],
        r["title_issue"],
        r["meta_description"],
        r["meta_desc_length"],
        r["meta_desc_issue"],
        r["h1_count"],
        r["h1_text"],
        r["h1_issue"],
        r["h2_count"],
        r["images_total"],
        r["images_missing_alt"],
        r["images_issue"],
        r["canonical"],
        r["canonical_issue"],
        r["robots_meta"],
        r["open_graph"],
        r["overall_score"],
        r["notes"],
    ]


def create_or_get_sheet(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Create a new sheet tab if it doesn't exist. Returns the sheet ID."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in spreadsheet["sheets"]}

    if sheet_name in existing:
        print(f"  Sheet '{sheet_name}' already exists, using it.")
        return existing[sheet_name]

    body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()
    new_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    print(f"  Created new sheet: '{sheet_name}'")
    return new_id


def write_to_sheet(service, spreadsheet_id: str, sheet_name: str, rows: list):
    """Write header + data rows to the sheet."""
    values = [COLUMN_HEADERS] + rows

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    # Bold the header row
    sheet_id = create_or_get_sheet(service, spreadsheet_id, sheet_name)
    fmt_requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(COLUMN_HEADERS),
                }
            }
        },
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": fmt_requests},
    ).execute()

    print(f"  ✅ Wrote {len(rows)} row(s) to sheet '{sheet_name}'")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    today = datetime.date.today().strftime("%Y-%m-%d")
    sheet_name = f"SEO Audit {today}"

    print(f"\n🦴 SEO AUDIT STARTING — {today}")
    print(f"   Websites : {WEBSITES}")
    print(f"   Sheet    : {sheet_name}\n")

    service = get_sheets_service()
    create_or_get_sheet(service, SPREADSHEET_ID, sheet_name)

    all_rows = []
    for url in WEBSITES:
        print(f"  Auditing: {url}")
        result = audit_page(url)
        row = result_to_row(result)
        all_rows.append(row)
        score = result["overall_score"]
        print(f"    Score: {score}/100")

    write_to_sheet(service, SPREADSHEET_ID, sheet_name, all_rows)
    print(f"\n🎉 Done! Check your Google Sheet.")


if __name__ == "__main__":
    main()
