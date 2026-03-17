#!/usr/bin/env python3

import argparse
import os
import sys
import webbrowser
from urllib.parse import urlencode


AUTHORIZE_URL = "https://threads.net/oauth/authorize"


def env_or_value(value: str | None, env_name: str) -> str | None:
    return value if value else os.getenv(env_name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Threads OAuth authorize URL for quick testing."
    )
    parser.add_argument("--client-id", help="Threads app ID. Fallback: THREADS_APP_ID")
    parser.add_argument(
        "--redirect-uri",
        help="OAuth redirect URI. Fallback: REDIRECT_URI",
    )
    parser.add_argument(
        "--scope",
        help="OAuth scope string. Fallback: SCOPE",
    )
    parser.add_argument(
        "--state",
        help="Optional OAuth state value. Fallback: STATE",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated URL in the default browser.",
    )

    args = parser.parse_args()

    client_id = env_or_value(args.client_id, "THREADS_APP_ID")
    redirect_uri = env_or_value(args.redirect_uri, "REDIRECT_URI")
    scope = env_or_value(args.scope, "SCOPE")
    state = env_or_value(args.state, "STATE")

    missing = [
        name
        for name, value in [
            ("client_id / THREADS_APP_ID", client_id),
            ("redirect_uri / REDIRECT_URI", redirect_uri),
            ("scope / SCOPE", scope),
        ]
        if not value
    ]
    if missing:
        print("Missing required values:", ", ".join(missing), file=sys.stderr)
        return 1

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
    }
    if state:
        params["state"] = state

    url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    print(url)

    if args.open:
        webbrowser.open(url)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
