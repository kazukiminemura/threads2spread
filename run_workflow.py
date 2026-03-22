import argparse
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
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
LOGGER = logging.getLogger("threads2spread.workflow")


@dataclass(frozen=True)
class GenerateConfig:
    count: int = 5
    content_length: str = "medium"
    timeout_seconds: int = 600
    max_chars: int | None = None


@dataclass(frozen=True)
class SheetsConfig:
    append: bool = True
    mode: str = "api"
    dedupe: bool = True
    spreadsheet_url: str | None = None
    service_account_file: str | None = None


@dataclass(frozen=True)
class WorkflowConfig:
    keywords: list[str]
    generate: GenerateConfig = field(default_factory=GenerateConfig)
    sheets: SheetsConfig = field(default_factory=SheetsConfig)
    retry_count: int = 0


@dataclass(frozen=True)
class WorkflowCliOptions:
    config_path: Path
    env_file: Path
    state_file: Path
    keywords: list[str] | None
    skip_sheets: bool
    scheduled_date: str | None
    scheduled_time: str | None
    log_level: str


@dataclass
class RuntimeState:
    sheet_append_count: int = 0
    sheet_append_limit: int = 1

    def can_append_sheet(self) -> bool:
        return self.sheet_append_count < self.sheet_append_limit

    def mark_sheet_appended(self) -> None:
        self.sheet_append_count += 1


@dataclass
class KeywordRunSummary:
    keyword: str
    started_at: str
    search_json: str | None = None
    generated_json: str | None = None
    csv_file: str | None = None
    sheet_append: dict[str, Any] | None = None
    posts_signature: str | None = None
    status: str = "running"
    finished_at: str | None = None
    recovered_after_retry: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "keyword": self.keyword,
            "started_at": self.started_at,
            "search_json": self.search_json,
            "generated_json": self.generated_json,
            "csv_file": self.csv_file,
            "sheet_append": self.sheet_append,
            "posts_signature": self.posts_signature,
            "status": self.status,
            "finished_at": self.finished_at,
        }
        if self.recovered_after_retry is not None:
            payload["recovered_after_retry"] = self.recovered_after_retry
        return payload


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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


class ConfigLoader:
    def load(self, config_path: Path) -> WorkflowConfig:
        if not config_path.exists():
            raise RuntimeError(f"config file not found: {config_path}")

        raw_config = self._load_raw_config(config_path)
        keywords = [str(keyword) for keyword in raw_config.get("keywords", [])]
        generate_raw = raw_config.get("generate", {})
        sheets_raw = raw_config.get("sheets", {})
        workflow_raw = raw_config.get("workflow", {})
        return WorkflowConfig(
            keywords=keywords,
            generate=GenerateConfig(
                count=int(generate_raw.get("count", 5) or 5),
                content_length=str(generate_raw.get("content_length", "medium") or "medium"),
                timeout_seconds=int(generate_raw.get("timeout_seconds", 600) or 600),
                max_chars=(
                    int(generate_raw["max_chars"])
                    if generate_raw.get("max_chars") not in {None, ""}
                    else None
                ),
            ),
            sheets=SheetsConfig(
                append=bool(sheets_raw.get("append", True)),
                mode=str(sheets_raw.get("mode", "api") or "api"),
                dedupe=bool(sheets_raw.get("dedupe", True)),
                spreadsheet_url=self._normalize_optional_string(sheets_raw.get("spreadsheet_url")),
                service_account_file=self._normalize_optional_string(sheets_raw.get("service_account_file")),
            ),
            retry_count=int(workflow_raw.get("retry_count", 0) or 0),
        )

    def _load_raw_config(self, config_path: Path) -> dict[str, Any]:
        text = config_path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return self._parse_simple_config(text)

    def _parse_simple_config(self, text: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "keywords": [],
            "generate": {},
            "sheets": {},
            "schedule": {},
            "workflow": {},
        }
        current_section: str | None = None
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

    def _normalize_optional_string(self, value: Any) -> str | None:
        if value in {None, ""}:
            return None
        return str(value)


class StateRepository:
    def load(self, state_path: Path) -> dict[str, Any]:
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        return {"runs": {}}

    def save(self, state_path: Path, state: dict[str, Any]) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class CommandRunner:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def run(self, command: list[str], step_name: str) -> CommandResult:
        LOGGER.info("step=%s status=start command=%s", step_name, " ".join(command))
        result = subprocess.run(
            command,
            cwd=self.cwd,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            LOGGER.info("step=%s stdout:\n%s", step_name, stdout)
        if stderr:
            LOGGER.warning("step=%s stderr:\n%s", step_name, stderr)
        if result.returncode != 0:
            self._raise_command_error(command, result.returncode, result.stdout, result.stderr)
        LOGGER.info("step=%s status=done", step_name)
        return CommandResult(stdout=stdout, stderr=stderr, returncode=result.returncode)

    def _raise_command_error(
        self,
        command: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        if "libnspr4.so" in stderr:
            raise RuntimeError(
                "Playwright dependency libnspr4.so is missing. "
                "Run `python -m playwright install-deps` in the execution environment."
            )
        raise RuntimeError(
            f"Command failed ({returncode}): {' '.join(command)}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )


class WorkflowFiles:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def find_latest(self, directory: Path, pattern: str) -> Path:
        candidates = list(directory.glob(pattern))
        if not candidates:
            raise RuntimeError(f"No files found in {directory} matching {pattern}")
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def build_posts_signature(self, generated_file: Path) -> str:
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
        return hashlib.sha256(
            json.dumps(signature_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def relative_to_base(self, path: Path) -> str:
        return str(path.relative_to(self.base_dir))


class WorkflowRunner:
    def __init__(
        self,
        base_dir: Path,
        config: WorkflowConfig,
        cli_options: WorkflowCliOptions,
        state: dict[str, Any],
        runtime_state: RuntimeState,
        command_runner: CommandRunner,
        files: WorkflowFiles,
    ) -> None:
        self.base_dir = base_dir
        self.config = config
        self.cli_options = cli_options
        self.state = state
        self.runtime_state = runtime_state
        self.command_runner = command_runner
        self.files = files
        self.python_executable = self._resolve_python_executable()

    def run(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        keywords = self.cli_options.keywords or self.config.keywords
        if not keywords:
            raise RuntimeError("No keywords configured. Set keywords in config.yaml or pass --keyword.")

        summaries: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for keyword in keywords:
            summary, failure = self._run_keyword_with_retry(str(keyword))
            summaries.append(summary)
            if failure is not None:
                failures.append(failure)

        return summaries, failures

    def _run_keyword_with_retry(self, keyword: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        last_error: Exception | None = None

        for attempt in range(self.config.retry_count + 1):
            try:
                LOGGER.info("keyword=%s attempt=%s/%s", keyword, attempt + 1, self.config.retry_count + 1)
                summary = self._process_keyword(keyword)
                if attempt:
                    summary.recovered_after_retry = attempt
                payload = summary.to_dict()
                self._store_last_run(keyword, payload)
                return payload, None
            except Exception as exc:
                last_error = exc
                LOGGER.exception("keyword=%s attempt=%s failed", keyword, attempt + 1)

        assert last_error is not None
        failure = {
            "keyword": keyword,
            "status": "error",
            "error": str(last_error),
            "failed_at": datetime.now().isoformat(timespec="seconds"),
            "retry_count": self.config.retry_count,
        }
        self._store_last_run(keyword, failure)
        return failure, failure

    def _process_keyword(self, keyword: str) -> KeywordRunSummary:
        summary = KeywordRunSummary(
            keyword=keyword,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        LOGGER.info("keyword=%s status=start", keyword)

        search_file = self._run_search_step(keyword)
        summary.search_json = self.files.relative_to_base(search_file)
        LOGGER.info("keyword=%s search_json=%s", keyword, summary.search_json)

        generated_file = self._run_generate_step(keyword, search_file)
        summary.generated_json = self.files.relative_to_base(generated_file)
        summary.posts_signature = self.files.build_posts_signature(generated_file)
        LOGGER.info("keyword=%s generated_json=%s", keyword, summary.generated_json)

        csv_file = self._run_export_step(keyword, generated_file)
        summary.csv_file = self.files.relative_to_base(csv_file)
        LOGGER.info("keyword=%s csv_file=%s", keyword, summary.csv_file)

        summary.sheet_append = self._maybe_append_to_sheet(keyword, csv_file, summary.posts_signature)
        summary.status = "ok"
        summary.finished_at = datetime.now().isoformat(timespec="seconds")
        LOGGER.info("keyword=%s status=done", keyword)
        return summary

    def _run_search_step(self, keyword: str) -> Path:
        command = [self.python_executable, DEFAULT_SCRIPTS["search"], keyword]
        self.command_runner.run(command, f"{keyword}.search")
        return self.files.find_latest(self.base_dir / "outputs/search_results", "*.json")

    def _run_generate_step(self, keyword: str, search_file: Path) -> Path:
        command = [
            self.python_executable,
            DEFAULT_SCRIPTS["generate"],
            "--results-file",
            str(search_file),
            "--count",
            str(self.config.generate.count),
            "--content-length",
            self.config.generate.content_length,
            "--timeout-seconds",
            str(self.config.generate.timeout_seconds),
        ]
        if self.config.generate.max_chars is not None:
            command.extend(["--max-chars", str(self.config.generate.max_chars)])
        self.command_runner.run(command, f"{keyword}.generate")
        return self.files.find_latest(
            self.base_dir / "outputs/generated_posts",
            f"*_{keyword}_threads_posts.json",
        )

    def _run_export_step(self, keyword: str, generated_file: Path) -> Path:
        command = [
            self.python_executable,
            DEFAULT_SCRIPTS["export"],
            "--input-file",
            str(generated_file),
        ]
        if self.cli_options.scheduled_date:
            command.extend(["--scheduled-date", self.cli_options.scheduled_date])
        if self.cli_options.scheduled_time:
            command.extend(["--scheduled-time", self.cli_options.scheduled_time])
        self.command_runner.run(command, f"{keyword}.export")
        return self.base_dir / "outputs/post_csv" / f"{generated_file.stem}.csv"

    def _maybe_append_to_sheet(
        self,
        keyword: str,
        csv_file: Path,
        posts_signature: str | None,
    ) -> dict[str, Any] | None:
        if not self._sheet_append_enabled():
            return None

        csv_key = str(csv_file.resolve())
        keyword_state = self.state.setdefault("runs", {}).setdefault(keyword, {})

        if not self.runtime_state.can_append_sheet():
            LOGGER.info("keyword=%s sheet_append=skipped_append_limit", keyword)
            return {
                "status": "skipped_append_limit",
                "reason": (
                    f"sheet append already executed {self.runtime_state.sheet_append_count} "
                    "time(s) in this workflow run"
                ),
                "csv_file": csv_key,
            }

        if self.config.sheets.dedupe and keyword_state.get("last_appended_posts_signature") == posts_signature:
            LOGGER.info("keyword=%s sheet_append=skipped_duplicate", keyword)
            return {
                "status": "skipped_duplicate",
                "reason": "same_posts_signature",
                "csv_file": csv_key,
            }

        command = self._build_append_command(csv_file)
        result = self.command_runner.run(command, f"{keyword}.append")
        self.runtime_state.mark_sheet_appended()
        keyword_state["last_appended_csv"] = csv_key
        keyword_state["last_appended_posts_signature"] = posts_signature
        LOGGER.info("keyword=%s sheet_append=ok count=%s", keyword, self.runtime_state.sheet_append_count)
        return {"status": "ok", "output": result.stdout}

    def _sheet_append_enabled(self) -> bool:
        if self.cli_options.skip_sheets:
            return False
        return self.config.sheets.append

    def _build_append_command(self, csv_file: Path) -> list[str]:
        command = [
            self.python_executable,
            DEFAULT_SCRIPTS["append"],
            "--mode",
            self.config.sheets.mode,
            "--csv-file",
            str(csv_file),
            "--log-level",
            self.cli_options.log_level,
        ]
        spreadsheet_url = self.config.sheets.spreadsheet_url or os.getenv("GOOGLE_SHEETS_URL")
        service_account_file = self.config.sheets.service_account_file or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if spreadsheet_url:
            command.extend(["--spreadsheet-url", spreadsheet_url])
        if service_account_file and self.config.sheets.mode == "api":
            command.extend(["--service-account-file", service_account_file])
        return command

    def _store_last_run(self, keyword: str, payload: dict[str, Any]) -> None:
        self.state.setdefault("runs", {}).setdefault(keyword, {})["last_run"] = payload

    def _resolve_python_executable(self) -> str:
        candidates = [
            self.base_dir / "venv" / "Scripts" / "python.exe",
            self.base_dir / "venv" / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return sys.executable


def parse_args() -> WorkflowCliOptions:
    parser = argparse.ArgumentParser(description="threads2spread workflow runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--keyword", action="append", help="Run only specific keyword(s). Can be repeated.")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip Google Sheets append step")
    parser.add_argument("--scheduled-date", help="Override CSV scheduled date (YYYY/MM/DD)")
    parser.add_argument("--scheduled-time", help="Override CSV scheduled time (HH:MM)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level",
    )
    args = parser.parse_args()
    return WorkflowCliOptions(
        config_path=args.config,
        env_file=args.env_file,
        state_file=args.state_file,
        keywords=args.keyword,
        skip_sheets=args.skip_sheets,
        scheduled_date=args.scheduled_date,
        scheduled_time=args.scheduled_time,
        log_level=args.log_level,
    )


def main() -> None:
    options = parse_args()
    setup_logging(options.log_level)
    LOGGER.info("workflow status=start")

    load_env_file(options.env_file)
    config = ConfigLoader().load(options.config_path)
    state_repository = StateRepository()
    state = state_repository.load(options.state_file)
    runtime_state = RuntimeState()

    runner = WorkflowRunner(
        base_dir=BASE_DIR,
        config=config,
        cli_options=options,
        state=state,
        runtime_state=runtime_state,
        command_runner=CommandRunner(BASE_DIR),
        files=WorkflowFiles(BASE_DIR),
    )
    summaries, failures = runner.run()

    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    state_repository.save(options.state_file, state)

    LOGGER.info(
        "workflow status=done sheet_append_count=%s failures=%s",
        runtime_state.sheet_append_count,
        len(failures),
    )
    print(json.dumps({"results": summaries, "failures": failures}, ensure_ascii=False, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
