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
sudo apt-get install -y libnspr4 libnss3 libasound2t64 fonts-noto-cjk ibglib2.0-0
./venv/bin/python -m playwright install chromium
```

Search Threads in the browser:

```bash
./venv/bin/python search_top_keyword.py "openai"
./venv/bin/python search_top_keyword.py "openai" --json
./venv/bin/python search_top_keyword.py "openai" --save-results
./venv/bin/python search_top_keyword.py "openai" --create-schedule --start-date 2026-03-19 --start-time 10:00 --interval-minutes 30
./venv/bin/python search_top_keyword.py "openai" --create-schedule --rewrite-with-llm --llm-model qwen3.5:4b --start-date 2026-03-19 --start-time 10:00 --interval-minutes 30
./venv/bin/python search_top_keyword.py "openai" --use-saved-results --create-schedule --rewrite-with-llm
./venv/bin/python search_top_keyword.py "openai" --results-file outputs/search_results/20260318_102353_openai.json --create-schedule
./venv/bin/python search_top_keyword.py "openai" --results-file outputs/search_results/20260318_102353_openai.json --create-schedule --rewrite-with-llm --llm-model qwen3:8b --llm-timeout 600
```

Output files:

- Search results JSON: `outputs/search_results/<timestamp>_<keyword>.json`
- Schedule CSV: `outputs/schedules/<timestamp>_<keyword>.csv`

The generated schedule CSV contains columns compatible with the spreadsheet format in the screenshot:

- `ID`, `投稿内容`, `予定日付`, `予定時刻`, `ステータス`, `投稿URL`, `ツリーID`, `投稿順序`

To rewrite each post before saving the CSV, start Ollama locally and pass `--rewrite-with-llm`.
You can override the endpoint with `OLLAMA_BASE_URL`, the default model with `OLLAMA_MODEL`, and the timeout with `OLLAMA_TIMEOUT`.
To skip a new Threads search, use `--use-saved-results` to load the latest JSON for that keyword, or `--results-file` to load a specific saved JSON file.
