import os

import requests
from dotenv import load_dotenv


load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "https://graph.threads.net/v1.0")


def get_user_id():
    url = f"{API_BASE_URL}/me"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers, timeout=30)

    if response.ok:
        return response.json().get("id")

    print(f"Failed to get user ID: {response.status_code} {response.text}")
    return None


def main():
    if not ACCESS_TOKEN:
        print("ACCESS_TOKEN is not set in .env")
        return

    user_id = get_user_id()

    if user_id:
        print(f"user_id: {user_id}")
    else:
        print("Could not retrieve user_id")


if __name__ == "__main__":
    main()
