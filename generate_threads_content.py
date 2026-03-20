import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess


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
        " この環境では ~/.npm-global/bin/openclaw への導入を想定しています。"
    )


def build_openclaw_env():
    env = os.environ.copy()
    extra_paths = [
        str(Path.home() / ".local/node/current/bin"),
        str(Path.home() / ".npm-global/bin"),
    ]
    env["PATH"] = ":".join(extra_paths + [env.get("PATH", "")])

    # OpenClaw の Ollama プロバイダは auth チェックでキーを要求する場合があるため、
    # ローカル利用時のダミー値を補います。
    env.setdefault("OLLAMA_API_KEY", "dummy")
    return env


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
        raise RuntimeError("応答からJSONを見つけられませんでした。")
    return stripped[start : end + 1]


def extract_response_text(raw_output):
    stripped = raw_output.strip()
    if not stripped:
        raise RuntimeError("OpenClaw から空の応答が返されました。")

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped

    if isinstance(payload, dict):
        for key in ("reply", "text", "message", "content", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload, ensure_ascii=False)

    return stripped


def run_openclaw_command(command, env):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"OpenClaw の実行に失敗しました。\n{error_text}")
    return result.stdout.strip()


def generate_posts_via_openclaw(prompt_text, model, timeout_seconds):
    env = build_openclaw_env()
    openclaw_bin = resolve_openclaw_bin()

    run_openclaw_command(
        [openclaw_bin, "models", "set", model],
        env,
    )
    raw_output = run_openclaw_command(
        [
            openclaw_bin,
            "agent",
            "--local",
            "--agent",
            "main",
            "--json",
            "--timeout",
            str(timeout_seconds),
            "--message",
            prompt_text,
        ],
        env,
    )

    response_text = extract_response_text(raw_output)
    response_payload = json.loads(extract_json_block(response_text))
    return response_payload, raw_output


def build_output_path(keyword):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(char if char.isalnum() else "_" for char in keyword).strip("_")
    safe_keyword = safe_keyword or "keyword"
    return OUTPUT_DIR / f"{timestamp}_{safe_keyword}_threads_posts.json"


def save_output(source_file, response_payload, raw_response, count, content_length, max_chars, model):
    keyword = response_payload.get("keyword") or "keyword"
    output_path = build_output_path(keyword)
    output = {
        "source_file": str(source_file),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "openclaw",
        "model": model,
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
        description="Send the latest Threads search JSON to OpenClaw and generate Threads post drafts."
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
        "--openclaw-model",
        default="ollama/qwen3.5:4b",
        help="OpenClaw で使うモデル名",
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
    response_payload, raw_response = generate_posts_via_openclaw(
        prompt_text,
        args.openclaw_model,
        args.timeout_seconds,
    )
    output_path, output = save_output(
        results_file,
        response_payload,
        raw_response,
        args.count,
        args.content_length,
        args.max_chars,
        args.openclaw_model,
    )

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"source_file: {results_file}")
        print(f"keyword: {output['keyword']}")
        print(f"generator: {output['generator']}")
        print(f"model: {output['model']}")
        print(f"posts_count: {len(output['posts'])}")
        print(f"saved_results: {output_path}")


if __name__ == "__main__":
    main()
