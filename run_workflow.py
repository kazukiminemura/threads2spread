import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"
DEFAULT_STATE_PATH = STATE_DIR / "last_runs.json"
DEFAULT_ENV_PATH = BASE_DIR / ".env"
DEFAULT_SCRIPTS = {
    "search": "search_threads_top_keyword.py",
    "generate": "generate_threads_content.py",
    "export": "export_threads_csv.py",
    "append": "append_csv_to_google_sheet.py",
}


def load_env_file(env_path: Path) -> None:
    if load_dotenv is not None and env_path.exists():
        load_dotenv(env_path)


def parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]

        if line.startswith("- "):
            if not isinstance(parent, list):
                raise RuntimeError(f"YAML list parse error near: {raw_line}")
            parent.append(parse_scalar(line[2:]))
            continue

        if ":" not in line:
            raise RuntimeError(f"YAML parse error near: {raw_line}")

        key, remainder = line.split(":", 1)
        key = key.strip()
        remainder = remainder.strip()

        if remainder:
            if not isinstance(parent, dict):
                raise RuntimeError(f"YAML mapping parse error near: {raw_line}")
            parent[key] = parse_scalar(remainder)
            continue

        # Peek next meaningful line to infer container type.
        child: Any = {}
        next_lines = path.read_text(encoding="utf-8").splitlines()
        # fallback inference happens below by scanning from current point externally impossible here;
        # child may be corrected later if first nested item is a list entry.
        if not isinstance(parent, dict):
            raise RuntimeError(f"YAML mapping parse error near: {raw_line}")
        parent[key] = child
        stack.append((indent, child))

    # Second pass to convert empty dicts that should be lists is unnecessary for bundled config shape.
    return root


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise RuntimeError(f"config file not found: {config_path}")

    text = config_path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    config = {"keywords": [], "generate": {}, "sheets": {}, "schedule": {}, "workflow": {}}
    current_section = None
    in_keywords = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("keywords:"):
            current_section = "keywords"
            in_keywords = True
            continue
        if in_keywords and raw_line.lstrip().startswith("- "):
            config["keywords"].append(parse_scalar(raw_line.lstrip()[2:]))
            continue
        in_keywords = False

        if raw_line.strip().endswith(":") and not raw_line.lstrip().startswith("- "):
            section = raw_line.strip()[:-1]
            if section in config:
                current_section = section
                continue

        if current_section in {"generate", "sheets", "schedule", "workflow"} and ":" in raw_line:
            key, value = raw_line.strip().split(":", 1)
            config[current_section][key.strip()] = parse_scalar(value)
            continue

        raise RuntimeError(f"Unsupported config.yaml line: {raw_line}")

    return config


def load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"runs": {}}


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "libnspr4.so" in stderr:
            raise RuntimeError(
                "Playwright の実行に必要な共有ライブラリが不足しています (libnspr4.so)。\n"
                "OpenClaw Docker コンテナ内で `./venv/bin/python -m playwright install-deps` を実行してから再試行してください。"
            )
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout.strip()


def find_latest_file(directory: Path, pattern: str) -> Path:
    candidates = list(directory.glob(pattern))
    if not candidates:
        raise RuntimeError(f"No files found in {directory} matching {pattern}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def build_posts_signature(generated_file: Path) -> str:
    payload = json.loads(generated_file.read_text(encoding="utf-8"))
    normalized_posts = []
    for post in payload.get("posts", []):
        if not isinstance(post, dict):
            normalized_posts.append(post)
            continue

        normalized_post = {}
        for key in sorted(post.keys()):
            if key in {"scheduled_date", "scheduled_time", "status"}:
                continue
            normalized_post[key] = post[key]
        normalized_posts.append(normalized_post)

    signature_source = {
        "keyword": payload.get("keyword"),
        "posts": normalized_posts,
    }
    digest = hashlib.sha256(
        json.dumps(signature_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest


def append_to_sheet_enabled(sheets_config: dict[str, Any], cli_skip: bool) -> bool:
    if cli_skip:
        return False
    return bool(sheets_config.get("append", True))


def process_keyword(keyword: str, config: dict[str, Any], args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    venv_python = str(BASE_DIR / "venv/bin/python")
    generate_cfg = config.get("generate", {})
    sheets_cfg = config.get("sheets", {})

    run_summary: dict[str, Any] = {
        "keyword": keyword,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "search_json": None,
        "generated_json": None,
        "csv_file": None,
        "sheet_append": None,
        "status": "running",
    }

    search_cmd = [venv_python, DEFAULT_SCRIPTS["search"], keyword]
    run_command(search_cmd, BASE_DIR)
    search_file = find_latest_file(BASE_DIR / "outputs/search_results", "*.json")
    run_summary["search_json"] = str(search_file.relative_to(BASE_DIR))

    generate_cmd = [
        venv_python,
        DEFAULT_SCRIPTS["generate"],
        "--results-file",
        str(search_file),
        "--count",
        str(generate_cfg.get("count", 5)),
        "--content-length",
        str(generate_cfg.get("content_length", "medium")),
        "--timeout-seconds",
        str(generate_cfg.get("timeout_seconds", 600)),
    ]
    max_chars = generate_cfg.get("max_chars")
    if max_chars:
        generate_cmd.extend(["--max-chars", str(max_chars)])
    run_command(generate_cmd, BASE_DIR)
    generated_file = find_latest_file(BASE_DIR / "outputs/generated_posts", f"*_{keyword}_threads_posts.json")
    run_summary["generated_json"] = str(generated_file.relative_to(BASE_DIR))
    posts_signature = build_posts_signature(generated_file)
    run_summary["posts_signature"] = posts_signature

    export_cmd = [
        venv_python,
        DEFAULT_SCRIPTS["export"],
        "--input-file",
        str(generated_file),
    ]
    if args.scheduled_date:
        export_cmd.extend(["--scheduled-date", args.scheduled_date])
    if args.scheduled_time:
        export_cmd.extend(["--scheduled-time", args.scheduled_time])
    run_command(export_cmd, BASE_DIR)
    csv_file = BASE_DIR / "outputs/post_csv" / f"{generated_file.stem}.csv"
    run_summary["csv_file"] = str(csv_file.relative_to(BASE_DIR))

    if append_to_sheet_enabled(sheets_cfg, args.skip_sheets):
        csv_key = str(csv_file.resolve())
        keyword_state = state.setdefault("runs", {}).setdefault(keyword, {})
        if sheets_cfg.get("dedupe", True) and keyword_state.get("last_appended_posts_signature") == posts_signature:
            run_summary["sheet_append"] = {
                "status": "skipped_duplicate",
                "reason": "same_posts_signature",
                "csv_file": csv_key,
            }
        else:
            append_cmd = [
                venv_python,
                DEFAULT_SCRIPTS["append"],
                "--mode",
                str(sheets_cfg.get("mode", "api")),
                "--csv-file",
                str(csv_file),
            ]
            spreadsheet_url = sheets_cfg.get("spreadsheet_url") or os.getenv("GOOGLE_SHEETS_URL")
            service_account_file = sheets_cfg.get("service_account_file") or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            if spreadsheet_url:
                append_cmd.extend(["--spreadsheet-url", str(spreadsheet_url)])
            if service_account_file and str(sheets_cfg.get("mode", "api")) == "api":
                append_cmd.extend(["--service-account-file", str(service_account_file)])
            append_stdout = run_command(append_cmd, BASE_DIR)
            run_summary["sheet_append"] = {"status": "ok", "output": append_stdout}
            keyword_state["last_appended_csv"] = csv_key
            keyword_state["last_appended_posts_signature"] = posts_signature

    run_summary["status"] = "ok"
    run_summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state.setdefault("runs", {}).setdefault(keyword, {})["last_run"] = run_summary
    return run_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="threads2spread workflow runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--keyword", action="append", help="Run only specific keyword(s). Can be repeated.")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip Google Sheets append step")
    parser.add_argument("--scheduled-date", help="Override CSV scheduled date (YYYY/MM/DD)")
    parser.add_argument("--scheduled-time", help="Override CSV scheduled time (HH:MM)")
    args = parser.parse_args()

    load_env_file(args.env_file)
    config = load_config(args.config)
    state = load_state(args.state_file)

    keywords = args.keyword or config.get("keywords") or []
    if not keywords:
        raise RuntimeError("No keywords configured. Set keywords in config.yaml or pass --keyword.")

    summaries = []
    failures = []
    retry_count = int(config.get("workflow", {}).get("retry_count", 0) or 0)
    for keyword in keywords:
        last_error = None
        for attempt in range(retry_count + 1):
            try:
                summary = process_keyword(str(keyword), config, args, state)
                if attempt:
                    summary["recovered_after_retry"] = attempt
                summaries.append(summary)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            failure = {
                "keyword": str(keyword),
                "status": "error",
                "error": str(last_error),
                "failed_at": datetime.now().isoformat(timespec="seconds"),
                "retry_count": retry_count,
            }
            summaries.append(failure)
            failures.append(failure)
            state.setdefault("runs", {}).setdefault(str(keyword), {})["last_run"] = failure

    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_state(args.state_file, state)

    print(json.dumps({"results": summaries, "failures": failures}, ensure_ascii=False, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
