import json
import re
import time
import hashlib
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


YOE_PATTERNS = [
    r"(\d+)\s*-\s*\d+\s*years? of experience",
    r"(\d+)\+?\s*(?:or more\s*)?years? of experience",
    r"(\d+)\+?\s*years? (?:of )?(?:relevant |professional |industry )?experience",
    r"minimum (?:of )?(\d+)\+?\s*years?",
    r"at least (\d+)\+?\s*years?",
]

# title keywords that indicate senior/lead/manager roles
SENIOR_TITLE_KEYWORDS = [
    "senior", "sr.", "sr ", "lead", "principal", "staff", "director",
    "manager", "head of", "vp ", "vice president", "distinguished",
    "architect", "fellow",
]

# US location keywords — job location must contain at least one of these
US_LOCATION_KEYWORDS = [
    'united states', 'california', 'new york', 'washington', 'texas',
    'illinois', 'florida', 'massachusetts', 'colorado', 'oregon',
    'nevada', 'arizona', 'georgia', 'north carolina', 'virginia',
    'seattle', 'san francisco', 'cupertino', 'austin', 'new york city',
    'boston', 'chicago', 'los angeles', 'san jose', 'sunnyvale',
    'santa clara', 'palo alto', 'menlo park', 'redwood city',
    ', ca', ', wa', ', tx', ', ny', ', ma', ', co', ', or', ', il',
    ', fl', ', ga', ', nc', ', va', ', az', ', nv',
]

# team/department keywords that indicate hardware roles
HARDWARE_TEAM_KEYWORDS = [
    "hardware", "hrdwr", "silicon", "chip", "asic", "fpga", "pcb",
    "electrical", "mechanical", "rf ", "antenna", "optics", "sensors",
    "camera hardware", "display hardware", "battery", "thermal",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def job_hash(pos_id, title):
    return hashlib.md5(f"{pos_id}{title}".encode()).hexdigest()


def extract_min_yoe(text):
    if not text:
        return None
    for pattern in YOE_PATTERNS:
        match = re.search(pattern, text.lower())
        if match:
            return int(match.group(1))
    return None


def is_senior(title):
    t = title.lower()
    return any(kw in t for kw in SENIOR_TITLE_KEYWORDS)


def is_hardware(team, url):
    combined = (team + " " + url).lower()
    return any(kw in combined for kw in HARDWARE_TEAM_KEYWORDS)


def is_too_old(posted_date, max_days):
    if not posted_date or not max_days:
        return False
    from datetime import date
    import re as _re
    s = posted_date.strip().lower()
    try:
        # absolute formats
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(s, fmt).date()
                return (date.today() - dt).days > max_days
            except ValueError:
                continue
        # relative: "X days ago", "X weeks ago", "X months ago"
        m = _re.search(r"(\d+)\s*(day|week|month)", s)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            days = n if unit == "day" else n * 7 if unit == "week" else n * 30
            return days > max_days
    except Exception:
        pass
    return False


def is_non_us(location):
    if not location:
        return True  # no location info, drop it to be safe
    loc = location.lower()
    return not any(kw in loc for kw in US_LOCATION_KEYWORDS)


def pre_filter(jobs, max_days=60):
    """Filter out senior, hardware, and old roles before hitting detail pages."""
    passed, dropped = [], []
    for j in jobs:
        if is_non_us(j.get("location", "")):
            dropped.append((j["title"], "non-US location"))
        elif is_senior(j["title"]):
            dropped.append((j["title"], "senior title"))
        elif is_hardware(j.get("team", ""), j.get("url", "")):
            dropped.append((j["title"], "hardware team"))
        elif is_too_old(j.get("posted_date", ""), max_days):
            dropped.append((j["title"], "too old"))
        else:
            passed.append(j)
    return passed, dropped


def apply_location_filter(page, location):
    try:
        loc_btn = page.query_selector("input[placeholder*='ocation'], input[aria-label*='ocation']")
        if not loc_btn:
            header = page.get_by_text(re.compile("location", re.I), exact=False).first
            if header:
                header.click()
                time.sleep(0.5)
            loc_btn = page.query_selector("input[placeholder*='ocation'], input[aria-label*='ocation']")

        if not loc_btn:
            print("[Filter] Location input not found, skipping")
            return False

        loc_btn.click()
        loc_btn.fill(location)
        time.sleep(1.5)

        suggestion = page.query_selector(
            "ul[role='listbox'] li, "
            ".typeahead-results li, "
            "[data-testid='typeahead-result'], "
            "ul.rc-typeahead-list li"
        )
        if suggestion:
            suggestion.click()
            page.wait_for_load_state("networkidle", timeout=12000)
            time.sleep(1)
            print(f"[Filter] Location set to: {location}")
            return True
        else:
            print(f"[Filter] No suggestion found for '{location}', proceeding without")
            return False
    except Exception as e:
        print(f"[Filter] Error: {e}")
        return False


def scrape_listing_page(page):
    jobs = []
    try:
        page.wait_for_selector("ul#search-job-list li", timeout=15000)
    except PWTimeout:
        print("[WARN] Job list not found")
        return jobs

    items = page.query_selector_all("ul#search-job-list li[role='listitem']")
    for item in items:
        try:
            a = item.query_selector("a.link-inline")
            if not a:
                continue

            title  = a.inner_text().strip()
            href   = a.get_attribute("href") or ""
            m      = re.search(r"/details/(\d+)", href)
            pos_id = m.group(1) if m else href.split("/")[-1]
            url    = f"https://jobs.apple.com{href}" if href.startswith("/") else href

            meta_els      = item.query_selector_all(".t-body-reduced, .job-list-item div")
            team_text     = ""
            location_text = ""
            for el in meta_els:
                txt = el.inner_text().strip()
                if txt and txt != title:
                    if not team_text:
                        team_text = txt
                    elif not location_text:
                        location_text = txt

            date_el = item.query_selector("time, [datetime]")
            posted  = date_el.get_attribute("datetime") or date_el.inner_text().strip() if date_el else ""

            jobs.append({
                "id":          pos_id,
                "title":       title,
                "team":        team_text,
                "location":    location_text,
                "posted_date": posted,
                "url":         url,
                "hash":        job_hash(pos_id, title),
                "min_yoe":     None,
            })
        except Exception:
            continue

    return jobs


def fetch_job_description(browser, url, delay):
    try:
        ctx  = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto(url, timeout=25000)
        page.wait_for_load_state("networkidle", timeout=20000)
        desc = ""
        for sel in ["#jd-job-summary", ".jd-job-summary", "[data-testid='job-description']", "#job-description", ".job-description", "main"]:
            el = page.query_selector(sel)
            if el:
                desc = el.inner_text()
                break
        ctx.close()
        time.sleep(delay)
        return desc
    except Exception as e:
        print(f"  [WARN] Description fetch failed: {e}")
        return ""


def scrape(role, location, max_results, delay, fetch_yoe, headless, max_days=60):
    jobs = []
    seen = set()
    pre_dropped = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx  = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        url = f"https://jobs.apple.com/en-US/search?search={quote(role)}&sort=newest"
        print(f"\n[Scraper] {url}")
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if location:
            apply_location_filter(page, location)

        current_page = 0
        # fetch more than max_results to account for pre-filter dropping some
        fetch_target = max_results * 3

        while len(jobs) < fetch_target:
            current_page += 1
            page_jobs = scrape_listing_page(page)

            if not page_jobs:
                print(f"[INFO] No jobs on page {current_page}, stopping")
                break

            for j in page_jobs:
                if j["hash"] not in seen:
                    seen.add(j["hash"])
                    jobs.append(j)
                if len(jobs) >= fetch_target:
                    break

            print(f"[Page {current_page}] scraped {len(page_jobs)} jobs  (buffer: {len(jobs)})")

            if len(jobs) >= fetch_target:
                break

            next_btn = page.query_selector("button:has-text('Next')")
            if not next_btn:
                next_btn = page.query_selector("a#next-paginate-link:not([aria-disabled='true'])")
            if not next_btn:
                print("[INFO] No next page, done")
                break

            next_btn.click()
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PWTimeout:
                print("[WARN] Next page timed out")
                break
            time.sleep(0.5)

        # pre-filter: drop senior + hardware before hitting detail pages
        jobs, pre_dropped = pre_filter(jobs, max_days)
        print(f"\n[Pre-filter] Kept {len(jobs)}  Dropped {len(pre_dropped)} (senior/hardware)")
        for title, reason in pre_dropped[:10]:
            print(f"  dropped: {title[:50]} ({reason})")
        if len(pre_dropped) > 10:
            print(f"  ... and {len(pre_dropped) - 10} more")

        # cap to max_results after pre-filter
        jobs = jobs[:max_results]

        # YoE enrichment
        if fetch_yoe:
            print(f"\n[YoE] Fetching descriptions for {len(jobs)} jobs...")
            for i, job in enumerate(jobs):
                desc           = fetch_job_description(browser, job["url"], delay)
                job["min_yoe"] = extract_min_yoe(desc)
                tag            = f"{job['min_yoe']}yr" if job["min_yoe"] is not None else "n/a"
                print(f"  [{i+1}/{len(jobs)}] {job['title'][:45]:<45} YoE: {tag}")

        browser.close()

    return jobs, pre_dropped


def filter_by_yoe(jobs, max_yoe):
    passed  = [j for j in jobs if j["min_yoe"] is None or j["min_yoe"] <= max_yoe]
    dropped = [j for j in jobs if j["min_yoe"] is not None and j["min_yoe"] > max_yoe]
    return passed, dropped


def load_existing(path):
    if not Path(path).exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return {j["hash"]: j for j in data.get("jobs", [])}


def save_results(jobs, path, meta):
    out = {
        "meta": {
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "filters":    meta,
            "total":      len(jobs),
        },
        "jobs": jobs,
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved {len(jobs)} jobs -> {path}")


def print_summary(jobs, new_ids, yoe_dropped, pre_dropped):
    print(f"\n{'='*65}")
    print(f"  Matched : {len(jobs)}")
    print(f"  New     : {len(new_ids)}")
    print(f"  Dropped (senior/hardware) : {len(pre_dropped)}")
    print(f"  Dropped (YoE too high)    : {len(yoe_dropped)}")
    print(f"{'='*65}")
    for j in sorted(jobs, key=lambda x: x.get("posted_date", ""), reverse=True)[:20]:
        yoe_tag = f"  {j['min_yoe']}yr" if j["min_yoe"] is not None else ""
        marker  = "  [NEW]" if j["hash"] in new_ids else ""
        print(f"  {j['title']:<46} {j['location'][:22]:<22}{yoe_tag}{marker}")
    if len(jobs) > 20:
        print(f"  ... and {len(jobs) - 20} more in output JSON")


def main():
    parser = argparse.ArgumentParser(description="Apple Careers Scraper")
    parser.add_argument("--role",     required=True,             help="Role to search e.g. 'ML Engineer'")
    parser.add_argument("--max-yoe",  type=int,  default=None,   help="Max YoE required")
    parser.add_argument("--location", default="United States",   help="Location filter (default: United States)")
    parser.add_argument("--max",      type=int,  default=100,    help="Max jobs after filtering (default: 100)")
    parser.add_argument("--output",   default="apple_jobs.json", help="Output JSON file")
    parser.add_argument("--delay",    type=float, default=0.8,   help="Delay between detail fetches (default: 0.8s)")
    parser.add_argument("--days",     type=int,  default=60,     help="Only jobs posted within N days (default: 60)")
    parser.add_argument("--no-dedup", action="store_true",       help="Overwrite instead of merging")
    parser.add_argument("--headless", action="store_true",       help="Hide browser window")
    args = parser.parse_args()

    existing  = {} if args.no_dedup else load_existing(args.output)
    fetch_yoe = args.max_yoe is not None

    raw_jobs, pre_dropped = scrape(args.role, args.location, args.max, args.delay, fetch_yoe, args.headless, args.days)

    yoe_dropped = []
    if fetch_yoe:
        raw_jobs, yoe_dropped = filter_by_yoe(raw_jobs, args.max_yoe)
        print(f"\n[YoE] Kept {len(raw_jobs)}  Dropped {len(yoe_dropped)} (>{args.max_yoe}yr required)")

    new_ids  = {j["hash"] for j in raw_jobs if j["hash"] not in existing}
    merged   = {**existing, **{j["hash"]: j for j in raw_jobs}}
    all_jobs = list(merged.values())

    meta = {"role": args.role, "max_yoe": args.max_yoe, "location": args.location}
    save_results(all_jobs, args.output, meta)
    print_summary(all_jobs, new_ids, yoe_dropped, pre_dropped)


if __name__ == "__main__":
    main()