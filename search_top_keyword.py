import argparse
import csv
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
import requests


load_dotenv()

THREADS_HOME_URL = "https://www.threads.com/"
THREADS_SEARCH_URL = "https://www.threads.com/search"
PROFILE_DIR = Path(".playwright-threads-profile")
OUTPUT_DIR = Path("outputs")
SEARCH_RESULTS_DIR = OUTPUT_DIR / "search_results"
SCHEDULES_DIR = OUTPUT_DIR / "schedules"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))
BROWSER_LOCALE = "ja-JP"
BROWSER_TIMEZONE = "Asia/Tokyo"
EXTRA_HEADERS = {
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}
SCHEDULE_HEADERS = [
    "ID",
    "投稿内容",
    "予定日付",
    "予定時刻",
    "ステータス",
    "投稿URL",
    "ツリーID",
    "投稿順序",
]
SEARCH_INPUT_SELECTORS = [
    'input[placeholder*="Search"]',
    'input[placeholder*="search"]',
    'input[aria-label*="Search"]',
    'input[aria-label*="search"]',
    'input[type="search"]',
    "input",
]
REWRITE_PROMPT = """You rewrite Threads search results into concise Japanese scheduled posts.

Requirements:
- Output only the rewritten post text.
- Keep the meaning aligned with the source, but make it cleaner and more natural.
- Do not mention that it was rewritten.
- Do not add hashtags unless they are clearly useful.
- Keep it under 140 Japanese characters when possible.
- Avoid emojis unless they are strongly justified by the source.
"""


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


def ensure_output_dirs():
    SEARCH_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)


def build_output_stem(keyword):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    return f"{timestamp}_{safe_keyword}"


def save_search_results(payload, output_stem):
    ensure_output_dirs()
    output_path = SEARCH_RESULTS_DIR / f"{output_stem}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def load_search_results(results_file):
    return json.loads(results_file.read_text(encoding="utf-8"))


def find_latest_search_results_file(keyword):
    ensure_output_dirs()
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    candidates = sorted(SEARCH_RESULTS_DIR.glob(f"*_{safe_keyword}.json"))
    if not candidates:
        return None
    return candidates[-1]


def parse_start_datetime(start_date, start_time):
    return datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")


def build_schedule_rows(results, start_at, interval_minutes, status):
    rows = []
    for index, result in enumerate(results, start=1):
        scheduled_at = start_at + timedelta(minutes=interval_minutes * (index - 1))
        rows.append(
            [
                index,
                result.get("content", ""),
                scheduled_at.strftime("%Y/%m/%d"),
                scheduled_at.strftime("%H:%M"),
                status,
                result.get("link", ""),
                "",
                "",
            ]
        )
    return rows


def generate_with_ollama(model, keyword, result, timeout):
    prompt = (
        f"Keyword: {keyword}\n"
        f"Search result title: {result.get('title', '')}\n"
        f"Source post: {result.get('content') or result.get('title') or ''}\n"
        "Rewrite this into a polished Japanese Threads post suitable for scheduled posting."
    )
    base_url = OLLAMA_BASE_URL.rstrip("/")
    errors = []

    generate_response = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "system": REWRITE_PROMPT,
            "prompt": prompt,
            "stream": False,
        },
        timeout=timeout,
    )
    if generate_response.ok:
        return (generate_response.json().get("response") or "").strip()
    errors.append(f"/api/generate -> {generate_response.status_code} {generate_response.text[:200]}")

    chat_response = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        },
        timeout=timeout,
    )
    if chat_response.ok:
        message = chat_response.json().get("choices", [{}])[0].get("message", {})
        return (message.get("content") or "").strip()
    errors.append(f"/v1/chat/completions -> {chat_response.status_code} {chat_response.text[:200]}")

    raise RuntimeError(
        "Could not rewrite with the local Ollama server. Tried "
        + ", ".join(errors)
        + f". Check OLLAMA_BASE_URL={base_url}, confirm the model '{model}' is available, or increase the timeout (current: {timeout}s)."
    )


def rewrite_post_texts(results, keyword, model, timeout):
    rewritten_results = []

    for result in results:
        source_text = result.get("content") or result.get("title") or ""
        rewritten_text = generate_with_ollama(model, keyword, result, timeout)
        rewritten_results.append(
            {
                **result,
                "original_content": source_text,
                "content": rewritten_text or source_text,
            }
        )

    return rewritten_results


def write_csv(output_path, headers, rows):
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def create_schedule_csv(payload, output_stem, start_date, start_time, interval_minutes, status):
    ensure_output_dirs()
    start_at = parse_start_datetime(start_date, start_time)
    output_path = SCHEDULES_DIR / f"{output_stem}.csv"
    rows = build_schedule_rows(payload["results"], start_at, interval_minutes, status)
    write_csv(output_path, SCHEDULE_HEADERS, rows)
    return output_path


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
    parser.add_argument(
        "--save-results",
        action="store_true",
        help="Save search results as JSON under outputs/search_results/",
    )
    parser.add_argument(
        "--create-schedule",
        action="store_true",
        help="Create a .csv schedule under outputs/schedules/ from the search results",
    )
    parser.add_argument(
        "--start-date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Schedule start date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--start-time",
        default="10:00",
        help="Schedule start time in HH:MM format",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Minutes between each scheduled post",
    )
    parser.add_argument(
        "--status",
        default="投稿予約中",
        help="Status text to write into the schedule sheet",
    )
    parser.add_argument(
        "--rewrite-with-llm",
        action="store_true",
        help="Rewrite each post with the local Ollama API before saving the schedule CSV",
    )
    parser.add_argument(
        "--llm-model",
        default=OLLAMA_MODEL,
        help="Ollama model to use for rewriting posts",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=OLLAMA_TIMEOUT,
        help="Timeout in seconds for each Ollama rewrite request",
    )
    parser.add_argument(
        "--use-saved-results",
        action="store_true",
        help="Skip Threads search and use the latest saved JSON for the keyword from outputs/search_results/",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        help="Skip Threads search and use this saved results JSON file",
    )
    args = parser.parse_args()

    loaded_results_path = None
    if args.results_file:
        loaded_results_path = args.results_file
    elif args.use_saved_results:
        loaded_results_path = find_latest_search_results_file(args.keyword)
        if loaded_results_path is None:
            raise RuntimeError(
                f"No saved search results found for keyword '{args.keyword}' in {SEARCH_RESULTS_DIR}"
            )

    if loaded_results_path:
        payload = load_search_results(loaded_results_path)
    else:
        payload = search_threads_posts(args.keyword, args.limit)

    if args.rewrite_with_llm:
        payload["results"] = rewrite_post_texts(
            payload["results"],
            args.keyword,
            args.llm_model,
            args.llm_timeout,
        )
    results = payload["results"]
    output_stem = build_output_stem(args.keyword)

    search_results_path = None
    schedule_path = None

    if args.save_results or args.create_schedule:
        search_results_path = save_search_results(payload, output_stem)

    if args.create_schedule:
        schedule_path = create_schedule_csv(
            payload,
            output_stem,
            args.start_date,
            args.start_time,
            args.interval_minutes,
            args.status,
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if search_results_path:
            print(f"\nsaved_results: {search_results_path}")
        if schedule_path:
            print(f"saved_schedule: {schedule_path}")
        return

    print(f"keyword: {payload['keyword']}")
    print(f"home_url: {payload['home_url']}")
    print(f"search_url: {payload['search_url']}")
    print(f"input_selector: {payload['input_selector']}")
    print(f"results_count: {len(results)}")
    if loaded_results_path:
        print(f"loaded_results: {loaded_results_path}")
    if search_results_path:
        print(f"saved_results: {search_results_path}")
    if schedule_path:
        print(f"saved_schedule: {schedule_path}")

    if not results:
        print("No matching Threads results found")
        return

    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result['content']}")


if __name__ == "__main__":
    main()
