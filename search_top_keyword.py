import argparse
import json
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright


DEFAULT_SITES = ["threads.com", "threads.net"]
GOOGLE_SEARCH_URL = "https://www.google.com/search?q="


def build_query(keyword, sites):
    site_query = " OR ".join(f"site:{site}" for site in sites)
    return f"{site_query} {keyword}"


def search_threads_posts(keyword, sites, limit):
    query = build_query(keyword, sites)
    search_url = f"{GOOGLE_SEARCH_URL}{quote_plus(query)}"
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        anchors = page.locator("a[href]").evaluate_all(
            """
            (elements) => elements.map((a) => ({
              href: a.href || "",
              title: (a.innerText || "").trim()
            }))
            """
        )

        browser.close()

    seen = set()
    for item in anchors:
        link = item.get("href", "")
        title = item.get("title", "")
        if not any(site in link for site in sites):
            continue
        if link in seen:
            continue
        seen.add(link)

        results.append(
            {
                "title": title,
                "link": link,
            }
        )

        if len(results) >= limit:
            break

    return {
        "query": query,
        "search_url": search_url,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Search top Threads posts by operating a browser and reading Google results."
    )
    parser.add_argument("keyword", help="Keyword to search")
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        help="Site filter to use. Can be passed multiple times.",
    )
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

    sites = args.sites or DEFAULT_SITES
    payload = search_threads_posts(args.keyword, sites, args.limit)
    results = payload["results"]

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"query: {payload['query']}")
    print(f"search_url: {payload['search_url']}")
    print(f"results_count: {len(results)}")

    if not results:
        print("No matching Threads posts found")
        return

    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result['title']}")
        print(f"link: {result['link']}")


if __name__ == "__main__":
    main()
