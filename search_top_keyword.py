import argparse
import json
import os

import requests
from dotenv import load_dotenv


load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "https://graph.threads.net/v1.0")
DEFAULT_FIELDS = (
    "id,text,media_type,permalink,timestamp,username,has_replies,is_quote_post,is_reply"
)


def search_top_keyword(keyword):
    url = f"{API_BASE_URL}/keyword_search"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    params = {
        "q": keyword,
        "search_type": "TOP",
        "fields": DEFAULT_FIELDS,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)

    if not response.ok:
        print(f"Failed to search keyword: {response.status_code} {response.text}")
        return None

    data = response.json().get("data", [])
    if not data:
        return None

    return data[0]


def main():
    parser = argparse.ArgumentParser(
        description="Search Threads by keyword and return the top-ranked result."
    )
    parser.add_argument("keyword", help="Keyword to search")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the top result as JSON",
    )
    args = parser.parse_args()

    if not ACCESS_TOKEN:
        print("ACCESS_TOKEN is not set in .env")
        return

    result = search_top_keyword(args.keyword)

    if not result:
        print("No search results found")
        return

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"id: {result.get('id')}")
    print(f"username: {result.get('username')}")
    print(f"timestamp: {result.get('timestamp')}")
    print(f"permalink: {result.get('permalink')}")
    print(f"text: {result.get('text')}")


if __name__ == "__main__":
    main()
