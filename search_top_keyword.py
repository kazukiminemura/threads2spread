import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import webbrowser

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
import requests


load_dotenv()

THREADS_HOME_URL = "https://www.threads.com/"
THREADS_SEARCH_URL = "https://www.threads.com/search"
PROFILE_DIR = Path(".playwright-threads-profile")
CHATGPT_PROFILE_DIR = Path(".playwright-chatgpt-profile")
OUTPUT_DIR = Path("outputs")
SEARCH_RESULTS_DIR = OUTPUT_DIR / "search_results"
MANUAL_REWRITE_DIR = OUTPUT_DIR / "manual_rewrite"
CHATGPT_URL = os.getenv("CHATGPT_URL", "https://chatgpt.com/")
CHATGPT_TIMEOUT_MS = int(os.getenv("CHATGPT_TIMEOUT_MS", "600000"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
REMOTE_LLM_BASE_URL = os.getenv("REMOTE_LLM_BASE_URL", "https://api.openai.com/v1")
REMOTE_LLM_MODEL = os.getenv("REMOTE_LLM_MODEL", "gpt-4o-mini")
REMOTE_LLM_AUTH_TOKEN = (
    os.getenv("REMOTE_LLM_AUTH_TOKEN")
    or os.getenv("REMOTE_LLM_API_KEY")
    or os.getenv("OPENAI_API_KEY")
)
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
CHATGPT_INPUT_SELECTORS = [
    "textarea",
    "div[contenteditable='true'][data-placeholder]",
    "div[contenteditable='true']",
]
CHATGPT_ASSISTANT_MESSAGE_SELECTORS = [
    "[data-message-author-role='assistant']",
    "article[data-testid*='conversation-turn']",
    "main article",
]
REWRITE_PROMPT = """You rewrite Threads search results into concise Japanese Threads posts.

Requirements:
- Output only the rewritten post text.
- Keep the meaning aligned with the source, but make it cleaner and more natural.
- Do not mention that it was rewritten.
- Do not add hashtags unless they are clearly useful.
- Keep it under 140 Japanese characters when possible.
- Avoid emojis unless they are strongly justified by the source.
"""
MANUAL_REWRITE_SYSTEM_PROMPT = """Rewrite each source post into polished Japanese for Threads.

Rules:
- Keep the meaning aligned with the source.
- Make each post natural, concise, and clean.
- Keep each post under 140 Japanese characters when possible.
- Do not add explanations.
- Do not add numbering in the rewritten output itself.
- Return exactly one rewritten post for each source item.
"""


def ensure_playwright_chromium():
    install_command = [sys.executable, "-m", "playwright", "install", "chromium"]
    print("Playwright Chromium is not installed yet. Running auto-setup...")
    subprocess.run(install_command, check=True)


def launch_threads_context(playwright):
    try:
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )
    except PlaywrightError as exc:
        error_text = str(exc).lower()
        if "executable doesn't exist" not in error_text and "please run the following command" not in error_text:
            raise
        ensure_playwright_chromium()
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )


def launch_chatgpt_context(playwright):
    try:
        return playwright.chromium.launch_persistent_context(
            str(CHATGPT_PROFILE_DIR),
            headless=False,
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
        )
    except PlaywrightError as exc:
        error_text = str(exc).lower()
        if "executable doesn't exist" not in error_text and "please run the following command" not in error_text:
            raise
        ensure_playwright_chromium()
        return playwright.chromium.launch_persistent_context(
            str(CHATGPT_PROFILE_DIR),
            headless=False,
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
    return None, None


def find_visible_locator(page, selectors, timeout=3000):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout)
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
        if "/post/" not in link:
            continue
        if "threads.com" not in link:
            continue
        if link in seen:
            continue
        normalized_content = (content or text).strip()
        if len(normalized_content) < 20:
            continue
        non_empty_lines = [line.strip() for line in normalized_content.splitlines() if line.strip()]
        if len(non_empty_lines) < 3:
            continue
        if len("".join(non_empty_lines[2:]).strip()) < 10:
            continue
        seen.add(link)
        results.append(
            {
                "title": text,
                "content": normalized_content,
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
        context = launch_threads_context(p)
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
    MANUAL_REWRITE_DIR.mkdir(parents=True, exist_ok=True)


def build_output_stem(keyword):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    return f"{timestamp}_{safe_keyword}"


def parse_output_stem_from_path(path):
    name = path.name
    suffixes = [
        "_chatgpt_prompt.txt",
        "_rewritten.txt",
        ".json",
        ".csv",
    ]
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


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


def build_search_results_path_from_stem(output_stem):
    return SEARCH_RESULTS_DIR / f"{output_stem}.json"


def find_latest_search_results_file(keyword):
    ensure_output_dirs()
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    candidates = sorted(SEARCH_RESULTS_DIR.glob(f"*_{safe_keyword}.json"))
    if not candidates:
        return None
    return candidates[-1]


def infer_results_file_from_manual_rewrites(rewrites_file):
    output_stem = parse_output_stem_from_path(rewrites_file)
    results_file = build_search_results_path_from_stem(output_stem)
    if not results_file.exists():
        raise RuntimeError(
            f"Could not find matching search results JSON for {rewrites_file}. Expected {results_file}."
        )
    return results_file, output_stem


def build_manual_rewrite_prompt(keyword, results):
    lines = [
        MANUAL_REWRITE_SYSTEM_PROMPT,
        "",
        f"Keyword: {keyword}",
        "",
        "Source posts:",
    ]

    for index, result in enumerate(results, start=1):
        source_text = result.get("content") or result.get("title") or ""
        lines.extend(
            [
                f"[{index}]",
                source_text,
                "",
            ]
        )

    lines.extend(
        [
            "Output format:",
            "Return plain text only.",
            "Use one line per rewritten post.",
            "Keep the same order as the source posts.",
        ]
    )
    return "\n".join(lines)


def save_manual_rewrite_files(payload, output_stem):
    ensure_output_dirs()
    prompt_path = MANUAL_REWRITE_DIR / f"{output_stem}_chatgpt_prompt.txt"
    response_template_path = MANUAL_REWRITE_DIR / f"{output_stem}_rewritten.txt"

    prompt_path.write_text(
        build_manual_rewrite_prompt(payload["keyword"], payload["results"]),
        encoding="utf-8",
    )

    template_lines = []
    for index, _ in enumerate(payload["results"], start=1):
        template_lines.append(f"{index}. ")
    response_template_path.write_text("\n".join(template_lines) + "\n", encoding="utf-8")

    return prompt_path, response_template_path


def open_url_in_browser(url):
    if webbrowser.open(url):
        return

    commands = []
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        commands.append(["xdg-open", url])
    elif sys.platform == "darwin" and shutil.which("open"):
        commands.append(["open", url])

    for command in commands:
        try:
            subprocess.Popen(command)
            return
        except OSError:
            continue

    raise RuntimeError(f"Could not open browser automatically for {url}")


def open_chatgpt_browser(prompt_path):
    open_url_in_browser(CHATGPT_URL)
    open_url_in_browser(prompt_path.resolve().as_uri())


def normalize_chatgpt_output_lines(text):
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        match = re.match(r"^(\d+)[\.\)]\s*(.*)$", line)
        if match:
            line = match.group(2).strip()
        if not line:
            continue
        lines.append(line)
    return lines


def extract_chatgpt_response_lines(page, expected_count):
    for selector in CHATGPT_ASSISTANT_MESSAGE_SELECTORS:
        locator = page.locator(selector)
        count = locator.count()
        if count == 0:
            continue
        for index in range(count - 1, -1, -1):
            text = locator.nth(index).inner_text().strip()
            if not text:
                continue
            lines = normalize_chatgpt_output_lines(text)
            if len(lines) >= expected_count:
                return lines[:expected_count]
    return []


def rewrite_with_chatgpt_browser(prompt_text, response_template_path, expected_count, timeout_ms):
    with sync_playwright() as p:
        context = launch_chatgpt_context(p)
        page = context.new_page()
        page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)

        input_box, selector = find_visible_locator(page, CHATGPT_INPUT_SELECTORS, timeout=timeout_ms)
        if input_box is None:
            context.close()
            raise RuntimeError(
                "ChatGPT input box was not found. If ChatGPT is asking you to log in, complete the login in the opened browser and run the command again."
            )

        input_box.click()
        if selector == "textarea":
            input_box.fill(prompt_text)
        else:
            page.keyboard.insert_text(prompt_text)
        page.keyboard.press("Enter")

        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        collected_lines = []
        while datetime.now().timestamp() < deadline:
            page.wait_for_timeout(3000)
            collected_lines = extract_chatgpt_response_lines(page, expected_count)
            if len(collected_lines) >= expected_count:
                break

        context.close()

    if len(collected_lines) < expected_count:
        raise RuntimeError(
            f"ChatGPT browser rewrite did not return enough lines. Expected {expected_count}, got {len(collected_lines)}."
        )

    response_template_path.write_text(
        "\n".join(f"{index}. {line}" for index, line in enumerate(collected_lines, start=1)) + "\n",
        encoding="utf-8",
    )
    return collected_lines


def load_manual_rewrites(rewrites_file):
    lines = rewrites_file.read_text(encoding="utf-8").splitlines()
    rewrites = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\.\s*(.*)$", line)
        if match:
            line = match.group(2).strip()
        if not line:
            continue
        rewrites.append(line)
    return rewrites


def apply_manual_rewrites(payload, rewrites):
    results = payload["results"]
    if len(rewrites) != len(results):
        raise RuntimeError(
            f"Manual rewrite count mismatch: expected {len(results)} lines, got {len(rewrites)}."
        )

    rewritten_results = []
    for result, rewritten_text in zip(results, rewrites):
        source_text = result.get("content") or result.get("title") or ""
        rewritten_results.append(
            {
                **result,
                "original_content": source_text,
                "content": rewritten_text or source_text,
            }
        )

    return rewritten_results


def build_rewrite_prompt(keyword, result):
    return (
        f"Keyword: {keyword}\n"
        f"Search result title: {result.get('title', '')}\n"
        f"Source post: {result.get('content') or result.get('title') or ''}\n"
        "Rewrite this into a polished Japanese Threads post."
    )


def list_ollama_models(timeout):
    base_url = OLLAMA_BASE_URL.rstrip("/")
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        return []

    models = response.json().get("models", [])
    names = []
    for model in models:
        name = model.get("name")
        if name:
            names.append(name)
    return names


def choose_ollama_model(requested_model, timeout):
    available_models = list_ollama_models(timeout)
    if requested_model in available_models:
        return requested_model
    if requested_model == OLLAMA_MODEL and available_models:
        return available_models[0]
    return requested_model


def generate_with_ollama(model, keyword, result, timeout):
    prompt = build_rewrite_prompt(keyword, result)
    model_to_use = choose_ollama_model(model, timeout)
    base_url = OLLAMA_BASE_URL.rstrip("/")
    errors = []

    generate_response = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model_to_use,
            "system": REWRITE_PROMPT,
            "prompt": prompt,
            "stream": False,
        },
        timeout=timeout,
    )
    if generate_response.ok:
        return (generate_response.json().get("response") or "").strip(), model_to_use
    errors.append(
        f"/api/generate ({model_to_use}) -> {generate_response.status_code} {generate_response.text[:200]}"
    )

    chat_response = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model_to_use,
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
        return (message.get("content") or "").strip(), model_to_use
    errors.append(
        f"/v1/chat/completions ({model_to_use}) -> {chat_response.status_code} {chat_response.text[:200]}"
    )

    raise RuntimeError(
        "Could not rewrite with the local Ollama server. Tried "
        + ", ".join(errors)
        + f". Check OLLAMA_BASE_URL={base_url}, confirm a local model is installed, or increase the timeout (current: {timeout}s)."
    )


def generate_with_remote_llm(model, keyword, result, timeout):
    if not REMOTE_LLM_AUTH_TOKEN:
        raise RuntimeError(
            "Remote LLM is not configured. Set REMOTE_LLM_AUTH_TOKEN, REMOTE_LLM_API_KEY, or OPENAI_API_KEY."
        )

    prompt = build_rewrite_prompt(keyword, result)
    base_url = REMOTE_LLM_BASE_URL.rstrip("/")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {REMOTE_LLM_AUTH_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Could not rewrite with remote LLM {model}. "
            f"HTTP {response.status_code}: {response.text[:300]}"
        )

    message = response.json().get("choices", [{}])[0].get("message", {})
    return (message.get("content") or "").strip(), model


def generate_rewritten_text(provider, model, remote_model, keyword, result, timeout):
    errors = []

    if provider in {"auto", "remote"}:
        try:
            text, used_model = generate_with_remote_llm(remote_model, keyword, result, timeout)
            return text, "remote", used_model
        except (RuntimeError, requests.RequestException) as exc:
            errors.append(f"remote: {exc}")
            if provider == "remote":
                raise RuntimeError(errors[-1]) from exc

    if provider in {"auto", "ollama"}:
        try:
            text, used_model = generate_with_ollama(model, keyword, result, timeout)
            return text, "ollama", used_model
        except (RuntimeError, requests.RequestException) as exc:
            errors.append(f"ollama: {exc}")
            raise RuntimeError(" | ".join(errors)) from exc

    raise RuntimeError(f"Unsupported LLM provider: {provider}")


def rewrite_post_texts(results, keyword, provider, model, remote_model, timeout):
    rewritten_results = []
    last_provider = None
    last_model = None

    for result in results:
        source_text = result.get("content") or result.get("title") or ""
        rewritten_text, used_provider, used_model = generate_rewritten_text(
            provider,
            model,
            remote_model,
            keyword,
            result,
            timeout,
        )
        last_provider = used_provider
        last_model = used_model
        rewritten_results.append(
            {
                **result,
                "original_content": source_text,
                "content": rewritten_text or source_text,
            }
        )

    return rewritten_results, last_provider, last_model


def has_explicit_ai_flags(argv):
    ai_flags = {
        "--llm-provider",
        "--llm-model",
        "--remote-llm-model",
        "--llm-timeout",
    }
    return any(arg in ai_flags for arg in argv)


def main():
    parser = argparse.ArgumentParser(
        description="Search Threads by keyword and prepare rewritten post text."
    )
    parser.add_argument("keyword", nargs="?", help="Keyword to search")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of results to use",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full payload as JSON too",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI rewriting and use the original post text",
    )
    parser.add_argument(
        "--manual-browser",
        action="store_true",
        help="Create ChatGPT/browser prompt files. This is now the default flow.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not automatically open ChatGPT and the generated prompt file in the browser",
    )
    parser.add_argument(
        "--chatgpt-timeout-ms",
        type=int,
        default=CHATGPT_TIMEOUT_MS,
        help="Timeout in milliseconds for the ChatGPT browser rewrite flow",
    )
    parser.add_argument(
        "--manual-rewrites-file",
        type=Path,
        help="Load rewritten posts from a text file created after the browser ChatGPT step",
    )
    parser.add_argument(
        "--llm-provider",
        default=LLM_PROVIDER,
        choices=["auto", "ollama", "remote"],
        help="Choose rewrite provider: auto, ollama, or remote",
    )
    parser.add_argument(
        "--llm-model",
        default=OLLAMA_MODEL,
        help="Model to use for rewriting. In auto/ollama mode this is the local model name.",
    )
    parser.add_argument(
        "--remote-llm-model",
        default=REMOTE_LLM_MODEL,
        help="Remote model to use when the remote provider is selected or auto falls back",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=OLLAMA_TIMEOUT,
        help="Timeout in seconds for each Ollama rewrite request",
    )
    parser.add_argument(
        "--use-saved",
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
    output_stem = None

    if args.manual_rewrites_file and not args.results_file and not args.use_saved:
        loaded_results_path, output_stem = infer_results_file_from_manual_rewrites(
            args.manual_rewrites_file
        )
    elif args.results_file:
        loaded_results_path = args.results_file
        output_stem = parse_output_stem_from_path(args.results_file)
    elif args.use_saved:
        if not args.keyword:
            raise RuntimeError("Keyword is required when using --use-saved.")
        loaded_results_path = find_latest_search_results_file(args.keyword)
        if loaded_results_path is None:
            raise RuntimeError(
                f"No saved search results found for keyword '{args.keyword}' in {SEARCH_RESULTS_DIR}"
            )
        output_stem = parse_output_stem_from_path(loaded_results_path)

    if loaded_results_path:
        payload = load_search_results(loaded_results_path)
    else:
        if not args.keyword:
            raise RuntimeError("Keyword is required unless --manual-rewrites-file points to a saved run.")
        payload = search_threads_posts(args.keyword, args.limit)

    use_manual_browser = (
        args.manual_browser
        or (
            not args.no_ai
            and not args.manual_rewrites_file
            and not has_explicit_ai_flags(sys.argv[1:])
        )
    )

    if args.no_ai and use_manual_browser:
        raise RuntimeError("Choose either --no-ai or --manual-browser, not both.")

    if use_manual_browser and args.manual_rewrites_file:
        raise RuntimeError(
            "Choose either --manual-browser to create prompt files or --manual-rewrites-file to import finished rewrites."
        )

    rewrite_provider_used = None
    rewrite_model_used = None
    manual_prompt_path = None
    manual_response_template_path = None
    manual_browser_error = None
    if output_stem is None:
        output_stem = build_output_stem(payload["keyword"])

    if args.manual_rewrites_file:
        payload["results"] = apply_manual_rewrites(
            payload,
            load_manual_rewrites(args.manual_rewrites_file),
        )
        payload["rewrite_provider"] = "manual_browser"
        payload["rewrite_model"] = "chatgpt_browser"
        rewrite_provider_used = payload["rewrite_provider"]
        rewrite_model_used = payload["rewrite_model"]
    elif use_manual_browser:
        manual_prompt_path, manual_response_template_path = save_manual_rewrite_files(
            payload,
            output_stem,
        )
        if not args.no_open_browser:
            try:
                prompt_text = manual_prompt_path.read_text(encoding="utf-8")
                rewritten_lines = rewrite_with_chatgpt_browser(
                    prompt_text,
                    manual_response_template_path,
                    len(payload["results"]),
                    args.chatgpt_timeout_ms,
                )
                payload["results"] = apply_manual_rewrites(payload, rewritten_lines)
                payload["rewrite_provider"] = "chatgpt_browser"
                payload["rewrite_model"] = "chatgpt_browser"
                rewrite_provider_used = payload["rewrite_provider"]
                rewrite_model_used = payload["rewrite_model"]
            except RuntimeError as exc:
                manual_browser_error = str(exc)
    elif not args.no_ai:
        payload["results"], rewrite_provider_used, rewrite_model_used = rewrite_post_texts(
            payload["results"],
            args.keyword,
            args.llm_provider,
            args.llm_model,
            args.remote_llm_model,
            args.llm_timeout,
        )
        payload["rewrite_provider"] = rewrite_provider_used
        payload["rewrite_model"] = rewrite_model_used
    results = payload["results"]

    search_results_path = None

    search_results_path = save_search_results(payload, output_stem)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if search_results_path:
            print(f"\nsaved_results: {search_results_path}")
        if manual_prompt_path:
            print(f"manual_prompt: {manual_prompt_path}")
        if manual_response_template_path:
            print(f"manual_rewrites_template: {manual_response_template_path}")
        if manual_browser_error:
            print(f"manual_browser_error: {manual_browser_error}")
        return

    print(f"keyword: {payload['keyword']}")
    print(f"home_url: {payload['home_url']}")
    print(f"search_url: {payload['search_url']}")
    print(f"input_selector: {payload['input_selector']}")
    print(f"results_count: {len(results)}")
    if rewrite_provider_used:
        print(f"rewrite_provider: {rewrite_provider_used}")
    if rewrite_model_used:
        print(f"rewrite_model: {rewrite_model_used}")
    if loaded_results_path:
        print(f"loaded_results: {loaded_results_path}")
    if search_results_path:
        print(f"saved_results: {search_results_path}")
    if manual_prompt_path:
        print(f"manual_prompt: {manual_prompt_path}")
    if manual_response_template_path:
        print(f"manual_rewrites_template: {manual_response_template_path}")
    if manual_browser_error:
        print(f"manual_browser_error: {manual_browser_error}")

    if not results:
        print("No matching Threads results found")
        return

    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result['content']}")


if __name__ == "__main__":
    main()
