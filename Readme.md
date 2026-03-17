# Threads OAuth Test Script

`threads_oauth_authorize.py` generates a Threads OAuth authorization URL for quick manual testing.

Example:

```bash
export THREADS_APP_ID="your-app-id"
export REDIRECT_URI="https://example.com/callback"
export SCOPE="threads_basic"
export STATE="debug-state"

python3 threads_oauth_authorize.py
python3 threads_oauth_authorize.py --open
```

You can also pass values as flags:

```bash
python3 threads_oauth_authorize.py \
  --client-id "your-app-id" \
  --redirect-uri "https://example.com/callback" \
  --scope "threads_basic" \
  --state "debug-state" \
  --open
```

Setup for the browser-based Threads search script:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
sudo apt-get update
sudo apt-get install -y libnspr4 libnss3 libasound2t64 fonts-noto-cjk
./venv/bin/python -m playwright install chromium
```

Search Threads in the browser:

```bash
./venv/bin/python search_top_keyword.py "openai"
./venv/bin/python search_top_keyword.py "openai" --json
```
