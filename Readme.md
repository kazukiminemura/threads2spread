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
