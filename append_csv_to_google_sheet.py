import argparse
import csv
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_runtime import ensure_playwright_chromium, ensure_playwright_runtime

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
except ImportError:
    Credentials = None
    build = None


CSV_OUTPUT_DIR = Path("outputs/post_csv")
GOOGLE_SHEETS_PROFILE_DIR = Path(".playwright-google-sheets-profile")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def launch_google_sheets_context(playwright):
    try:
        context = playwright.chromium.launch_persistent_context(
            str(GOOGLE_SHEETS_PROFILE_DIR),
            headless=False,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
    except PlaywrightError as exc:
        error_text = str(exc).lower()
        if "executable doesn't exist" not in error_text and "please run the following command" not in error_text:
            raise
        ensure_playwright_chromium()
        context = playwright.chromium.launch_persistent_context(
            str(GOOGLE_SHEETS_PROFILE_DIR),
            headless=False,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )

    context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin="https://docs.google.com",
    )
    return context


def find_latest_csv_file():
    CSV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = list(CSV_OUTPUT_DIR.glob("*.csv"))
    if not candidates:
        raise RuntimeError(f"{CSV_OUTPUT_DIR} にCSVファイルが見つかりませんでした。")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def parse_spreadsheet_url(spreadsheet_url):
    parsed = urlparse(spreadsheet_url)
    path_parts = [part for part in parsed.path.split("/") if part]

    try:
        doc_index = path_parts.index("d")
        spreadsheet_id = path_parts[doc_index + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"スプレッドシートURLから spreadsheet id を取得できませんでした: {spreadsheet_url}") from exc

    query = parse_qs(parsed.query)
    fragment_query = parse_qs(parsed.fragment)
    gid = query.get("gid", [None])[0] or fragment_query.get("gid", [None])[0]
    if gid is None:
        raise RuntimeError(f"スプレッドシートURLから gid を取得できませんでした: {spreadsheet_url}")

    return spreadsheet_id, int(gid)


def load_csv_rows(csv_file, include_header):
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise RuntimeError(f"CSVファイルが空です: {csv_file}")

    if include_header:
        return rows
    return rows[1:]


def get_service_account_file(explicit_path):
    if explicit_path:
        path = Path(explicit_path).expanduser()
    else:
        env_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if not env_path:
            raise RuntimeError(
                "Google service account のJSONファイルが指定されていません。"
                " --service-account-file または GOOGLE_SERVICE_ACCOUNT_FILE を設定してください。"
            )
        path = Path(env_path).expanduser()

    if not path.exists():
        raise RuntimeError(f"service account ファイルが見つかりませんでした: {path}")
    return path


def resolve_spreadsheet_url(explicit_url):
    if explicit_url:
        return explicit_url

    env_url = os.getenv("GOOGLE_SHEETS_URL")
    if env_url:
        return env_url

    raise RuntimeError(
        "Google Sheets のURLが指定されていません。"
        " --spreadsheet-url または .env の GOOGLE_SHEETS_URL を設定してください。"
    )


def build_sheets_service(service_account_file):
    if Credentials is None or build is None:
        raise RuntimeError(
            "Google API 依存パッケージが見つかりませんでした。"
            " `./venv/bin/pip install -r requirements.txt` を実行してください。"
        )

    credentials = Credentials.from_service_account_file(
        str(service_account_file),
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=credentials)


def resolve_sheet_title(service, spreadsheet_id, gid):
    response = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in response.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("sheetId") == gid:
            return properties.get("title")

    raise RuntimeError(f"gid={gid} に対応するシートが見つかりませんでした。")


def append_rows_via_api(spreadsheet_url, rows, service_account_file):
    spreadsheet_id, gid = parse_spreadsheet_url(spreadsheet_url)
    service = build_sheets_service(service_account_file)
    sheet_title = resolve_sheet_title(service, spreadsheet_id, gid)

    if not rows:
        return spreadsheet_id, gid, sheet_title, 0

    append_range = f"'{sheet_title}'!A:S"
    response = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=append_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )
    updates = response.get("updates", {})
    return spreadsheet_id, gid, sheet_title, updates.get("updatedRows", 0)


def wait_for_sheet_ready(page, timeout_ms):
    try:
        page.locator("div[role='grid']").first.wait_for(state="visible", timeout=timeout_ms)
        return
    except PlaywrightTimeoutError:
        pass

    sign_in_markers = [
        "text=ログイン",
        "text=Sign in",
        "input[type='email']",
    ]
    for marker in sign_in_markers:
        locator = page.locator(marker).first
        if locator.count() and locator.is_visible():
            raise RuntimeError(
                "Google Sheets に未ログインです。"
                " 開いたブラウザでログインしてから、もう一度スクリプトを実行してください。"
            )

    raise RuntimeError("Google Sheets の表を読み込めませんでした。ブラウザで画面状態を確認してください。")


def rows_to_tsv(rows):
    return "\n".join("\t".join(value for value in row) for row in rows)


def append_rows_via_browser(spreadsheet_url, rows, timeout_ms):
    if not rows:
        spreadsheet_id, gid = parse_spreadsheet_url(spreadsheet_url)
        return spreadsheet_id, gid, 0

    ensure_playwright_runtime()
    spreadsheet_id, gid = parse_spreadsheet_url(spreadsheet_url)
    tsv_text = rows_to_tsv(rows)

    with sync_playwright() as playwright:
        context = launch_google_sheets_context(playwright)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(spreadsheet_url, wait_until="domcontentloaded", timeout=timeout_ms)
        wait_for_sheet_ready(page, timeout_ms)
        page.wait_for_timeout(3000)

        page.evaluate(
            """async (text) => {
                await navigator.clipboard.writeText(text);
            }""",
            tsv_text,
        )

        grid = page.locator("div[role='grid']").first
        grid.click()
        page.keyboard.press("Control+Home")
        page.wait_for_timeout(500)
        page.keyboard.press("Control+ArrowDown")
        page.wait_for_timeout(500)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(500)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(3000)

        context.close()

    return spreadsheet_id, gid, len(rows)


def resolve_mode(mode, service_account_file):
    if mode != "auto":
        return mode

    if service_account_file:
        return "api"

    load_dotenv()
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"):
        return "api"
    return "browser"


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="CSVファイルの内容をGoogleスプレッドシートに追記する"
    )
    parser.add_argument(
        "--csv-file",
        type=Path,
        help="追記するCSVファイル。省略時は outputs/post_csv の最新ファイルを使う",
    )
    parser.add_argument(
        "--spreadsheet-url",
        help="追記先のGoogleスプレッドシートURL",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "browser", "api"],
        default="auto",
        help="追記方法。auto は service account があれば API、なければブラウザ操作を使う",
    )
    parser.add_argument(
        "--service-account-file",
        help="Google service account JSON ファイルのパス",
    )
    parser.add_argument(
        "--include-header",
        action="store_true",
        help="CSVヘッダー行も一緒に追記する",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="ブラウザ操作のタイムアウト秒数",
    )
    args = parser.parse_args()

    csv_file = args.csv_file or find_latest_csv_file()
    spreadsheet_url = resolve_spreadsheet_url(args.spreadsheet_url)
    rows = load_csv_rows(csv_file, include_header=args.include_header)
    mode = resolve_mode(args.mode, args.service_account_file)

    if mode == "api":
        service_account_file = get_service_account_file(args.service_account_file)
        spreadsheet_id, gid, sheet_title, updated_rows = append_rows_via_api(
            spreadsheet_url,
            rows,
            service_account_file,
        )
        print(f"mode: {mode}")
        print(f"csv_file: {csv_file}")
        print(f"spreadsheet_id: {spreadsheet_id}")
        print(f"sheet_gid: {gid}")
        print(f"sheet_title: {sheet_title}")
        print(f"appended_rows: {updated_rows}")
        return

    spreadsheet_id, gid, updated_rows = append_rows_via_browser(
        spreadsheet_url,
        rows,
        timeout_ms=args.timeout_seconds * 1000,
    )
    print(f"mode: {mode}")
    print(f"csv_file: {csv_file}")
    print(f"spreadsheet_id: {spreadsheet_id}")
    print(f"sheet_gid: {gid}")
    print(f"appended_rows: {updated_rows}")


if __name__ == "__main__":
    main()
