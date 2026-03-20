import argparse
import csv
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


CSV_OUTPUT_DIR = Path("outputs/post_csv")
DEFAULT_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1ybZ7itDhxvhPItmxbtIITcXVA-PvKPTCsoQs8Cmx4SE/edit?pli=1&gid=1614552414#gid=1614552414"
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


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
    gid = (
        query.get("gid", [None])[0]
        or fragment_query.get("gid", [None])[0]
    )
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
    load_dotenv()
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


def build_sheets_service(service_account_file):
    credentials = Credentials.from_service_account_file(
        str(service_account_file),
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=credentials)


def resolve_sheet_title(service, spreadsheet_id, gid):
    response = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id)
        .execute()
    )
    for sheet in response.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("sheetId") == gid:
            return properties.get("title")

    raise RuntimeError(f"gid={gid} に対応するシートが見つかりませんでした。")


def append_rows(service, spreadsheet_id, sheet_title, rows):
    if not rows:
        return 0

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
    return updates.get("updatedRows", 0)


def main():
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
        default=DEFAULT_SPREADSHEET_URL,
        help="追記先のGoogleスプレッドシートURL",
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
    args = parser.parse_args()

    csv_file = args.csv_file or find_latest_csv_file()
    rows = load_csv_rows(csv_file, include_header=args.include_header)
    spreadsheet_id, gid = parse_spreadsheet_url(args.spreadsheet_url)
    service_account_file = get_service_account_file(args.service_account_file)
    service = build_sheets_service(service_account_file)
    sheet_title = resolve_sheet_title(service, spreadsheet_id, gid)
    updated_rows = append_rows(service, spreadsheet_id, sheet_title, rows)

    print(f"csv_file: {csv_file}")
    print(f"spreadsheet_id: {spreadsheet_id}")
    print(f"sheet_gid: {gid}")
    print(f"sheet_title: {sheet_title}")
    print(f"appended_rows: {updated_rows}")


if __name__ == "__main__":
    main()
