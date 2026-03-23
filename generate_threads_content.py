import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time


SEARCH_RESULTS_DIR = Path("outputs/search_results")
OUTPUT_DIR = Path("outputs/generated_posts")
DEFAULT_COUNT = 5
DEFAULT_TIMEOUT_SECONDS = 600
OPENCLAW_BIN_CANDIDATES = [
    Path.home() / ".npm-global/bin/openclaw",
    Path.home() / ".local/bin/openclaw",
]
CONTENT_LENGTH_PRESETS = {
    "short": "1投稿あたり60〜100文字程度",
    "medium": "1投稿あたり100〜160文字程度",
    "long": "1投稿あたり160〜240文字程度",
}
ACP_PROTOCOL_VERSION = 1


def find_latest_search_results_file():
    SEARCH_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    candidates = sorted(SEARCH_RESULTS_DIR.glob("*.json"))
    if not candidates:
        raise RuntimeError(f"{SEARCH_RESULTS_DIR} に検索結果JSONが見つかりませんでした。")
    return candidates[-1]


def load_search_results(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_length_instruction(content_length, max_chars):
    if max_chars:
        return f"1投稿あたり最大{max_chars}文字"
    return CONTENT_LENGTH_PRESETS[content_length]


def build_prompt(payload, count, content_length, max_chars):
    length_instruction = build_length_instruction(content_length, max_chars)
    source_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""以下の検索結果JSONをもとに、Threads投稿用の日本語コンテンツを{count}個作成してください。

条件:
- 読みやすく自然な日本語にする
- 元の検索結果の内容や雰囲気を参考にする
- 誇張しすぎない
- 各投稿は独立した完成文にする
- {length_instruction}
- ハッシュタグは必要なときだけ最小限
- 結果はJSONのみ返す
- JSON形式は次の通り:
{{
  "keyword": "検索キーワード",
  "posts": [
    {{
      "index": 1,
      "content": "投稿本文"
    }}
  ]
}}

検索結果JSON:
{source_json}
"""


def resolve_openclaw_bin():
    command_path = shutil.which("openclaw")
    if command_path:
        return command_path

    for candidate in OPENCLAW_BIN_CANDIDATES:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "openclaw コマンドが見つかりませんでした。"
        " OpenClaw Docker コンテナ内で実行され、コンテナ内 PATH または ~/.npm-global/bin/openclaw / ~/.local/bin/openclaw に導入されている前提です。"
    )


def build_openclaw_env():
    env = os.environ.copy()
    extra_paths = [
        str(Path.home() / ".local/node/current/bin"),
        str(Path.home() / ".npm-global/bin"),
    ]
    env["PATH"] = ":".join(extra_paths + [env.get("PATH", "")])
    return env


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def extract_json_block(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("????JSON?????????????")
    return stripped[start : end + 1]


def iter_text_candidates(value):
    if value is None:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            yield stripped
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_text_candidates(item)
        return
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            if key in value:
                yield from iter_text_candidates(value[key])
        for item in value.values():
            yield from iter_text_candidates(item)


def parse_response_payload(response_text, prompt_result):
    candidates = []
    seen = set()

    for candidate in [response_text, *iter_text_candidates(prompt_result)]:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)

    for candidate in candidates:
        try:
            payload = json.loads(extract_json_block(candidate))
        except (json.JSONDecodeError, RuntimeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("posts"), list):
            return payload

    preview = candidates[0][:500] if candidates else ""
    raise RuntimeError(f"???? posts ???JSON????????????? preview={preview}")


class ACPClient:
    def __init__(self, process):
        self.process = process
        self.messages = queue.Queue()
        self._next_id = 1
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self):
        for line in self.process.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            self.messages.put(payload)

    def _drain_stderr(self):
        for _ in self.process.stderr:
            pass

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def send_request(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        return request_id

    def send_response(self, request_id, result):
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )

    def _send(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def wait_for_response(self, request_id, timeout_seconds, on_notification=None):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                payload = self.messages.get(timeout=remaining)
            except queue.Empty:
                continue

            if payload.get("id") == request_id:
                if "error" in payload:
                    raise RuntimeError(f"ACP request failed: {json.dumps(payload['error'], ensure_ascii=False)}")
                return payload.get("result")

            if "method" in payload and on_notification is not None:
                on_notification(payload)

        raise RuntimeError(f"ACP response timeout for request id {request_id}.")


def run_openclaw_command(command, env, timeout_seconds=30):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"OpenClaw の実行に失敗しました。\n{error_text}")
    return result.stdout.strip()


def ensure_gateway_ready(openclaw_bin, env, port, token, timeout_seconds):
    ws_url = f"ws://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            openclaw_bin,
            "gateway",
            "run",
            "--auth",
            "token",
            "--token",
            token,
            "--allow-unconfigured",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            stderr_text = process.stderr.read().strip()
            raise RuntimeError(f"OpenClaw gateway の起動に失敗しました。\n{stderr_text}")
        try:
            run_openclaw_command(
                [
                    openclaw_bin,
                    "gateway",
                    "call",
                    "health",
                    "--url",
                    ws_url,
                    "--token",
                    token,
                    "--json",
                    "--timeout",
                    "2000",
                ],
                env,
                timeout_seconds=5,
            )
            return process, ws_url
        except RuntimeError:
            time.sleep(1)

    process.terminate()
    raise RuntimeError("OpenClaw gateway の起動待ちがタイムアウトしました。")


def start_acp_client(openclaw_bin, env, ws_url, token):
    process = subprocess.Popen(
        [
            openclaw_bin,
            "acp",
            "--url",
            ws_url,
            "--token",
            token,
            "--session",
            "agent:main:main",
            "--reset-session",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return ACPClient(process)


def initialize_acp(client, timeout_seconds):
    request_id = client.send_request(
        "initialize",
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
            "clientInfo": {
                "name": "threads2spread",
                "version": "1.0.0",
            },
        },
    )
    return client.wait_for_response(request_id, timeout_seconds)


def create_session(client, cwd, timeout_seconds):
    request_id = client.send_request(
        "session/new",
        {
            "cwd": str(cwd),
            "mcpServers": [],
        },
    )
    result = client.wait_for_response(request_id, timeout_seconds)
    session_id = result.get("sessionId")
    if not session_id:
        raise RuntimeError("ACP session/new から sessionId を取得できませんでした。")
    return session_id


def prompt_session(client, session_id, prompt_text, timeout_seconds):
    chunks = []

    def on_notification(payload):
        method = payload.get("method")
        params = payload.get("params", {})

        if method == "session/request_permission":
            client.send_response(
                payload["id"],
                {
                    "outcome": {
                        "outcome": "cancelled",
                    },
                },
            )
            return

        if method != "session/update":
            return

        update = params.get("update", {})
        if update.get("sessionUpdate") != "agent_message_chunk":
            return

        content = update.get("content", {})
        if content.get("type") == "text":
            chunks.append(content.get("text", ""))

    request_id = client.send_request(
        "session/prompt",
        {
            "sessionId": session_id,
            "prompt": [
                {
                    "type": "text",
                    "text": prompt_text,
                }
            ],
        },
    )
    result = client.wait_for_response(request_id, timeout_seconds, on_notification=on_notification)
    return "".join(chunks).strip(), result


def generate_posts_via_acp_runtime(prompt_text, timeout_seconds):
    env = build_openclaw_env()
    openclaw_bin = resolve_openclaw_bin()

    gateway_port = find_free_port()
    gateway_token = secrets.token_urlsafe(24)
    gateway_process, ws_url = ensure_gateway_ready(
        openclaw_bin,
        env,
        gateway_port,
        gateway_token,
        min(timeout_seconds, 60),
    )
    client = None

    try:
        client = start_acp_client(openclaw_bin, env, ws_url, gateway_token)
        initialize_acp(client, min(timeout_seconds, 30))
        session_id = create_session(client, Path.cwd().resolve(), min(timeout_seconds, 30))
        response_text, prompt_result = prompt_session(client, session_id, prompt_text, timeout_seconds)

        if not response_text and not prompt_result:
            raise RuntimeError("ACP runtime backend ????????????????????")

        response_payload = parse_response_payload(response_text, prompt_result)
        raw_response = json.dumps(
            {
                "response_text": response_text,
                "prompt_result": prompt_result,
                "session_id": session_id,
                "backend": "acp-runtime",
                "gateway_url": ws_url,
            },
            ensure_ascii=False,
            indent=2,
        )
        return response_payload, raw_response
    finally:
        if client is not None:
            client.close()
        if gateway_process.poll() is None:
            gateway_process.terminate()
            try:
                gateway_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gateway_process.kill()


def build_output_path(keyword):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    return OUTPUT_DIR / f"{timestamp}_{safe_keyword}_threads_posts.json"


def save_output(source_file, response_payload, raw_response, count, content_length, max_chars):
    keyword = response_payload.get("keyword") or "keyword"
    output_path = build_output_path(keyword)
    output = {
        "source_file": str(source_file),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "openclaw",
        "backend": "acp-runtime",
        "model": response_payload.get("model"),
        "model_source": "openclaw-config",
        "count": count,
        "content_length": content_length,
        "max_chars": max_chars,
        "keyword": keyword,
        "posts": response_payload.get("posts", []),
        "raw_response": raw_response,
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path, output


def main():
    parser = argparse.ArgumentParser(
        description="Send the latest Threads search JSON to OpenClaw via ACP runtime backend and generate Threads post drafts."
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        help="入力に使う検索結果JSON。省略時は outputs/search_results の最新ファイルを使う",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help="生成する投稿数",
    )
    parser.add_argument(
        "--content-length",
        choices=["short", "medium", "long"],
        default="medium",
        help="各投稿の長さプリセット",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        help="各投稿の最大文字数。指定した場合は --content-length より優先",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="OpenClaw 応答待ちタイムアウト",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="保存内容を標準出力にも表示する",
    )
    args = parser.parse_args()

    if args.count < 1:
        raise RuntimeError("--count は 1 以上で指定してください。")
    if args.max_chars is not None and args.max_chars < 1:
        raise RuntimeError("--max-chars は 1 以上で指定してください。")

    results_file = args.results_file or find_latest_search_results_file()
    source_payload = load_search_results(results_file)
    prompt_text = build_prompt(source_payload, args.count, args.content_length, args.max_chars)
    response_payload, raw_response = generate_posts_via_acp_runtime(
        prompt_text,
        args.timeout_seconds,
    )
    output_path, output = save_output(
        results_file,
        response_payload,
        raw_response,
        args.count,
        args.content_length,
        args.max_chars,
    )

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"source_file: {results_file}")
        print(f"keyword: {output['keyword']}")
        print(f"generator: {output['generator']}")
        print(f"backend: {output['backend']}")
        print(f"model: {output['model']}")
        print(f"posts_count: {len(output['posts'])}")
        print(f"saved_results: {output_path}")


if __name__ == "__main__":
    main()
