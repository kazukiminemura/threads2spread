# Threads OAuth Test Script

`threads_oauth_authorize.py` now supports a full local OAuth login flow for Threads.

Example:

```bash
export THREADS_APP_ID="your-app-id"
export THREADS_APP_SECRET="your-app-secret"
export REDIRECT_URI="http://127.0.0.1:8787/callback"
export SCOPE="threads_basic"

python3 threads_oauth_authorize.py
```

That flow:

- opens the OAuth page in your browser
- listens on your local redirect URI
- exchanges the code for a token
- upgrades to a long-lived token by default
- saves `ACCESS_TOKEN` into `.env`

If you only want the raw authorize URL:

```bash
export REDIRECT_URI="https://example.com/callback"
export SCOPE="threads_basic"
export STATE="debug-state"

python3 threads_oauth_authorize.py --url-only
```

You can also pass values as flags:

```bash
python3 threads_oauth_authorize.py \
  --client-id "your-app-id" \
  --client-secret "your-app-secret" \
  --redirect-uri "https://example.com/callback" \
  --scope "threads_basic" \
  --state "debug-state" \
  --url-only
```

Setup for the browser-based Threads search script:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
sudo apt-get update
sudo apt-get install -y libnspr4 libnss3 libasound2t64 fonts-noto-cjk ibglib2.0-0
npx playwright install --with-deps chromium
apt-get install -y xvfb
sudo apt install language-pack-ja-base language-pack-ja
```

If Chromium has not been installed yet, `search_top_keyword.py` now tries to run `python -m playwright install chromium` automatically on first use.

Search Threads in the browser:

```bash
./venv/bin/python search_top_keyword.py "openai"
./venv/bin/python search_top_keyword.py "openai" --json
./venv/bin/python search_top_keyword.py "openai" --use-saved --manual-rewrites-file outputs/manual_rewrite/20260320_120000_openai_rewritten.txt
./venv/bin/python search_top_keyword.py "openai" --no-ai
./venv/bin/python search_top_keyword.py "openai" --llm-provider auto
./venv/bin/python search_top_keyword.py "openai" --llm-provider ollama --llm-model llama3.2:3b
./venv/bin/python search_top_keyword.py "openai" --llm-provider remote --remote-llm-model gpt-4o-mini
./venv/bin/python search_top_keyword.py "openai" --start-date 2026-03-19 --start-time 10:00 --interval-minutes 30
./venv/bin/python search_top_keyword.py "openai" --use-saved
./venv/bin/python search_top_keyword.py "openai" --results-file outputs/search_results/20260318_102353_openai.json
./venv/bin/python search_top_keyword.py "openai" --llm-model qwen3:8b --remote-llm-model gpt-4o-mini --llm-timeout 600
```

Output files:

- Search results JSON: `outputs/search_results/<timestamp>_<keyword>.json`
- Schedule CSV: `outputs/schedules/<timestamp>_<keyword>.csv`
- Manual browser prompt: `outputs/manual_rewrite/<timestamp>_<keyword>_chatgpt_prompt.txt`
- Manual browser response template: `outputs/manual_rewrite/<timestamp>_<keyword>_rewritten.txt`

The default behavior is simple:

- You only need to pass a keyword.
- The script searches Threads.
- It creates ChatGPT/browser prompt files by default.
- It saves the raw search results automatically.
- It waits for you to import rewritten lines before creating the final CSV.

The generated schedule CSV contains columns compatible with the spreadsheet format in the screenshot:

- `ID`, `投稿内容`, `予定日付`, `予定時刻`, `ステータス`, `投稿URL`, `ツリーID`, `投稿順序`

Manual browser ChatGPT workflow:

1. Run `search_top_keyword.py "keyword"`
2. Open the generated `*_chatgpt_prompt.txt` file and paste it into ChatGPT in your browser
3. Put ChatGPT's rewritten lines into the generated `*_rewritten.txt` file, one line per post
4. Run `search_top_keyword.py "keyword" --use-saved --manual-rewrites-file <that rewritten file>`

This browser/manual flow is now the default. The script saves the search results and prompt files first, and it creates the final CSV only after you import the rewritten lines.

AI rewrite modes:

- `auto`: try a remote OpenAI-compatible API first, then fall back to local Ollama
- `ollama`: only use local Ollama
- `remote`: only use the remote API

Remote API setup:

```bash
export REMOTE_LLM_AUTH_TOKEN="your-oauth-or-bearer-token"
# optional overrides
export REMOTE_LLM_BASE_URL="https://api.openai.com/v1"
export REMOTE_LLM_MODEL="gpt-4o-mini"
```

`REMOTE_LLM_AUTH_TOKEN` is the preferred setting. `REMOTE_LLM_API_KEY` and `OPENAI_API_KEY` still work as fallbacks.

Local Ollama setup:

```bash
ollama pull llama3.2:3b
export OLLAMA_MODEL="llama3.2:3b"
```

You can override the local endpoint with `OLLAMA_BASE_URL`, the default provider with `LLM_PROVIDER`, and the timeout with `OLLAMA_TIMEOUT`.
To skip AI rewriting, pass `--no-ai`.
To skip a new Threads search, use `--use-saved` to load the latest JSON for that keyword, or `--results-file` to load a specific saved JSON file.
