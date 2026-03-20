import argparse
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path


GENERATED_POSTS_DIR = Path("outputs/generated_posts")
CSV_OUTPUT_DIR = Path("outputs/post_csv")
DEFAULT_STATUS = "投稿予約中"
CSV_HEADERS = [
    "ID",
    "投稿内容",
    "予定日付",
    "予定時刻",
    "ステータス",
    "投稿URL",
    "ツリーID",
    "投稿順序",
    "動画URL",
    "画像URL_1枚目",
    "画像URL_2枚目",
    "画像URL_3枚目",
    "画像URL_4枚目",
    "画像URL_5枚目",
    "画像URL_6枚目",
    "画像URL_7枚目",
    "画像URL_8枚目",
    "画像URL_9枚目",
    "画像URL_10枚目",
]
DEFAULT_SLOT_MINUTES = [15, 45]
DEFAULT_SLOT_HOURS = [9, 12, 15, 19]
DEFAULT_START_DELAY_MINUTES = 20


def find_latest_generated_posts_file():
    GENERATED_POSTS_DIR.mkdir(parents=True, exist_ok=True)
    candidates = list(GENERATED_POSTS_DIR.glob("*.json"))
    if not candidates:
        raise RuntimeError(f"{GENERATED_POSTS_DIR} に生成済みJSONが見つかりませんでした。")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_generated_posts(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_output_path(source_path):
    CSV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return CSV_OUTPUT_DIR / f"{source_path.stem}.csv"


def normalize_image_urls(post):
    image_urls = post.get("image_urls")
    if isinstance(image_urls, list):
        normalized = [str(url) for url in image_urls[:10]]
        return normalized + [""] * (10 - len(normalized))

    single_values = []
    for index in range(1, 11):
        value = post.get(f"image_url_{index}")
        single_values.append("" if value is None else str(value))
    return single_values


def parse_schedule_start(scheduled_date, scheduled_time):
    now = datetime.now()
    base_date = scheduled_date or now.strftime("%Y/%m/%d")
    base_time = scheduled_time or now.strftime("%H:%M")

    try:
        start_at = datetime.strptime(f"{base_date} {base_time}", "%Y/%m/%d %H:%M")
    except ValueError as exc:
        raise RuntimeError(
            "--scheduled-date は YYYY/MM/DD、--scheduled-time は HH:MM 形式で指定してください。"
        ) from exc

    if scheduled_date is None and scheduled_time is None:
        start_at = now + timedelta(minutes=DEFAULT_START_DELAY_MINUTES)

    return start_at


def round_up_to_next_slot(base_datetime):
    candidate = base_datetime.replace(second=0, microsecond=0)
    for day_offset in range(14):
        current_day = candidate.date() + timedelta(days=day_offset)
        for hour in DEFAULT_SLOT_HOURS:
            for minute in DEFAULT_SLOT_MINUTES:
                slot = datetime.combine(current_day, datetime.min.time()).replace(hour=hour, minute=minute)
                if slot >= candidate:
                    return slot
    raise RuntimeError("予約枠を計算できませんでした。")


def build_safe_schedule(posts_count, scheduled_date, scheduled_time):
    start_at = parse_schedule_start(scheduled_date, scheduled_time)
    first_slot = round_up_to_next_slot(start_at)
    slots = []
    current_slot = first_slot

    while len(slots) < posts_count:
        slots.append(current_slot)
        current_slot = round_up_to_next_slot(current_slot + timedelta(minutes=1))

    return slots


def post_to_row(post, row_id, scheduled_date, scheduled_time, default_status):
    image_urls = normalize_image_urls(post)
    row = {
        "ID": row_id,
        "投稿内容": post.get("content", ""),
        "予定日付": post.get("scheduled_date") or scheduled_date or "",
        "予定時刻": post.get("scheduled_time") or scheduled_time or "",
        "ステータス": post.get("status") or default_status,
        "投稿URL": post.get("post_url", ""),
        "ツリーID": post.get("thread_id", ""),
        "投稿順序": post.get("post_order", ""),
        "動画URL": post.get("video_url", ""),
    }

    for index, image_url in enumerate(image_urls, start=1):
        row[f"画像URL_{index}枚目"] = image_url

    return row


def export_posts_to_csv(source_payload, output_path, scheduled_date, scheduled_time, default_status):
    posts = source_payload.get("posts", [])
    schedule_slots = build_safe_schedule(len(posts), scheduled_date, scheduled_time)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row_id, (post, slot) in enumerate(zip(posts, schedule_slots), start=1):
            writer.writerow(
                post_to_row(
                    post,
                    row_id=row_id,
                    scheduled_date=slot.strftime("%Y/%m/%d"),
                    scheduled_time=slot.strftime("%H:%M"),
                    default_status=default_status,
                )
            )


def main():
    parser = argparse.ArgumentParser(
        description="generate_threads_content.py の出力JSONを投稿予約用CSVに変換する"
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="入力に使う generated_posts JSON。省略時は outputs/generated_posts の最新ファイルを使う",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="出力CSVパス。省略時は outputs/post_csv/<input_filename>.csv",
    )
    parser.add_argument(
        "--scheduled-date",
        help="全投稿の予定日付を一括指定する (例: 2126/03/19)",
    )
    parser.add_argument(
        "--scheduled-time",
        help="全投稿の予定時刻を一括指定する (例: 23:45)",
    )
    parser.add_argument(
        "--status",
        default=DEFAULT_STATUS,
        help=f"全投稿のデフォルトステータス (default: {DEFAULT_STATUS})",
    )
    args = parser.parse_args()

    input_file = args.input_file or find_latest_generated_posts_file()
    source_payload = load_generated_posts(input_file)
    output_file = args.output_file or build_output_path(input_file)
    export_posts_to_csv(
        source_payload,
        output_file,
        scheduled_date=args.scheduled_date,
        scheduled_time=args.scheduled_time,
        default_status=args.status,
    )

    print(f"source_file: {input_file}")
    print(f"posts_count: {len(source_payload.get('posts', []))}")
    print(f"saved_csv: {output_file}")


if __name__ == "__main__":
    main()
