import argparse
from datetime import datetime
import json
import os
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_runtime import ensure_playwright_chromium, ensure_playwright_runtime


THREADS_HOME_URL = "https://www.threads.com/"
THREADS_SEARCH_URL = "https://www.threads.com/search"
PROFILE_DIR = Path(".playwright-threads-profile")
OUTPUT_DIR = Path("outputs/search_results")
DEFAULT_LIMIT = 10
BROWSER_LOCALE = "ja-JP"
BROWSER_TIMEZONE = "Asia/Tokyo"
EXTRA_HEADERS = {
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}
SEARCH_INPUT_SELECTORS = [
    'input[placeholder*="Search"]',
    'input[placeholder*="search"]',
    'input[aria-label*="Search"]',
    'input[aria-label*="search"]',
    'input[type="search"]',
    "input",
]


def should_run_headless():
    value = os.environ.get("THREADS_HEADLESS")
    if value is not None:
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return True


def clear_stale_profile_locks():
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = PROFILE_DIR / name
        if path.exists() or path.is_symlink():
            path.unlink()


def launch_threads_context(playwright):
    headless = should_run_headless()
    try:
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )
    except PlaywrightError as exc:
        error_text = str(exc).lower()
        if "processsingleton" in error_text or "profile directory is already in use" in error_text:
            clear_stale_profile_locks()
            return playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=headless,
                locale=BROWSER_LOCALE,
                timezone_id=BROWSER_TIMEZONE,
            )
        if "executable doesn't exist" not in error_text and "please run the following command" not in error_text:
            raise
        ensure_playwright_chromium()
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )


def find_search_input(page):
    for selector in SEARCH_INPUT_SELECTORS:
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue
        try:
            locator.wait_for(state="visible", timeout=3000)
            return locator, selector
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError("Threads の検索入力欄が見つかりませんでした。")


def submit_search_keyword(page, search_input, keyword):
    # Threads can show floating UI that intercepts pointer events, so avoid
    # relying on a mouse click to focus the search field.
    page.keyboard.press("Escape")
    search_input.scroll_into_view_if_needed()

    try:
        search_input.focus()
        search_input.fill("")
        search_input.type(keyword, delay=50)
    except PlaywrightTimeoutError:
        search_input.evaluate(
            """
            (element, value) => {
              element.focus();
              element.value = value;
              element.dispatchEvent(new Event("input", { bubbles: true }));
              element.dispatchEvent(new Event("change", { bubbles: true }));
            }
            """,
            keyword,
        )

    search_input.press("Enter")


def extract_results(page, limit):
    anchors = page.locator("a[href]").evaluate_all(
        """
        (elements) => elements.map((a) => ({
          href: a.href || "",
          text: (a.innerText || "").trim(),
          content: (() => {
            let node = a;
            for (let i = 0; i < 6 && node; i += 1) {
              const text = (node.innerText || "").trim();
              if (text && text.length >= 20) {
                return text;
              }
              node = node.parentElement;
            }
            return (a.innerText || "").trim();
          })()
        }))
        """
    )

    seen = set()
    results = []
    for item in anchors:
        link = item.get("href", "")
        title = item.get("text", "")
        content = (item.get("content") or title).strip()

        if "/post/" not in link or "threads.com" not in link:
            continue
        if link in seen or len(content) < 20:
            continue

        seen.add(link)
        results.append(
            {
                "title": title,
                "content": content,
                "link": link,
            }
        )
        if len(results) >= limit:
            break

    return results


def normalize_post_text(text):
    if not text:
        return ""

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if line.lower() in {"threads", "repost", "reply", "like", "share"}:
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def extract_post_text(detail_page):
    meta_selectors = [
        'meta[property="og:description"]',
        'meta[name="description"]',
    ]
    for selector in meta_selectors:
        locator = detail_page.locator(selector).first
        if locator.count() == 0:
            continue
        content = locator.get_attribute("content") or ""
        content = normalize_post_text(content)
        if content:
            return content

    candidates = detail_page.locator("article, main, [role='main']").evaluate_all(
        """
        (elements) => elements.map((element) => (element.innerText || "").trim())
        """
    )
    for candidate in candidates:
        content = normalize_post_text(candidate)
        if len(content) >= 20:
            return content

    return ""


def enrich_results_with_post_content(context, results):
    detail_page = context.new_page()
    enriched_results = []

    for result in results:
        detail_page.goto(result["link"], wait_until="domcontentloaded", timeout=60000)
        detail_page.wait_for_timeout(3000)
        post_content = extract_post_text(detail_page)
        enriched_results.append(
            {
                **result,
                "content": post_content or result["content"],
            }
        )

    detail_page.close()
    return enriched_results


def search_threads_posts(keyword, limit):
    with sync_playwright() as playwright:
        context = launch_threads_context(playwright)
        context.set_extra_http_headers(EXTRA_HEADERS)
        page = context.new_page()

        page.goto(THREADS_HOME_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        page.goto(THREADS_SEARCH_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        search_input, selector = find_search_input(page)
        submit_search_keyword(page, search_input, keyword)
        page.wait_for_timeout(5000)

        results = extract_results(page, limit)
        results = enrich_results_with_post_content(context, results)
        context.close()

    return {
        "keyword": keyword,
        "home_url": THREADS_HOME_URL,
        "search_url": THREADS_SEARCH_URL,
        "input_selector": selector,
        "results_count": len(results),
        "results": results,
    }


def build_output_path(keyword):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    return OUTPUT_DIR / f"{timestamp}_{safe_keyword}.json"


def save_results(payload):
    output_path = build_output_path(payload["keyword"])
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Search Threads by keyword and save the top results as JSON."
    )
    parser.add_argument("keyword", help="検索キーワード")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"保存する件数の上限 (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="保存したJSONの内容も標準出力に表示する",
    )
    args = parser.parse_args()

    ensure_playwright_runtime()
    payload = search_threads_posts(args.keyword, args.limit)
    output_path = save_results(payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"keyword: {payload['keyword']}")
        print(f"results_count: {payload['results_count']}")
        print(f"saved_results: {output_path}")


if __name__ == "__main__":
    main()
