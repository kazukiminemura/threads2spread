import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


THREADS_HOME_URL = "https://www.threads.com/"
THREADS_SEARCH_URL = "https://www.threads.com/search"
PROFILE_DIR = Path(".playwright-threads-profile")
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
    return None, None


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
        text = item.get("text", "")
        content = item.get("content", "")
        if "/@" not in link and "/post/" not in link:
            continue
        if "threads.com" not in link:
            continue
        if link in seen:
            continue
        seen.add(link)
        results.append(
            {
                "title": text,
                "content": content or text,
                "link": link,
            }
        )
        if len(results) >= limit:
            break

    return results


def search_threads_posts(keyword, limit):
    payload = {
        "keyword": keyword,
        "home_url": THREADS_HOME_URL,
        "search_url": THREADS_SEARCH_URL,
        "input_selector": None,
        "results": [],
    }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )
        context.set_extra_http_headers(EXTRA_HEADERS)
        page = context.new_page()
        page.goto(THREADS_HOME_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        page.goto(THREADS_SEARCH_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        search_input, selector = find_search_input(page)

        if search_input is None:
            raise RuntimeError(
                "Could not find the Threads search input after opening threads.com and waiting 5 seconds."
            )

        payload["input_selector"] = selector
        search_input.click()
        search_input.fill(keyword)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        payload["results"] = extract_results(page, limit)
        context.close()

    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Search Threads directly from threads.com/search using the browser."
    )
    parser.add_argument("keyword", help="Keyword to search")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of results to print",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON",
    )
    args = parser.parse_args()

    payload = search_threads_posts(args.keyword, args.limit)
    results = payload["results"]

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"keyword: {payload['keyword']}")
    print(f"home_url: {payload['home_url']}")
    print(f"search_url: {payload['search_url']}")
    print(f"input_selector: {payload['input_selector']}")
    print(f"results_count: {len(results)}")

    if not results:
        print("No matching Threads results found")
        return

    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result['content']}")


if __name__ == "__main__":
    main()
