from datetime import datetime
from pathlib import Path
import subprocess
import sys


PLAYWRIGHT_SETUP_MARKER = Path(".playwright-installed")
PLAYWRIGHT_BROWSER_MARKER = Path(".playwright-browser-installed")
PLAYWRIGHT_DEPS_MARKER = Path(".playwright-deps-installed")


def _write_marker(path):
    path.write_text(
        datetime.now().isoformat(timespec="seconds"),
        encoding="utf-8",
    )


def _run_playwright_command(*args):
    subprocess.run(
        [sys.executable, "-m", "playwright", *args],
        check=True,
    )


def ensure_playwright_runtime():
    legacy_marker_exists = PLAYWRIGHT_SETUP_MARKER.exists()

    if not PLAYWRIGHT_BROWSER_MARKER.exists():
        _run_playwright_command("install")
        _write_marker(PLAYWRIGHT_BROWSER_MARKER)

    if not PLAYWRIGHT_DEPS_MARKER.exists():
        try:
            _run_playwright_command("install-deps")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Playwright の依存パッケージ導入に失敗しました。"
                f" 対話可能なシェルで `sudo {sys.executable} -m playwright install-deps`"
                " を実行してから再試行してください。"
            ) from exc
        _write_marker(PLAYWRIGHT_DEPS_MARKER)

    if legacy_marker_exists or (
        PLAYWRIGHT_BROWSER_MARKER.exists() and PLAYWRIGHT_DEPS_MARKER.exists()
    ):
        _write_marker(PLAYWRIGHT_SETUP_MARKER)


def ensure_playwright_chromium():
    _run_playwright_command("install", "chromium")
    _write_marker(PLAYWRIGHT_BROWSER_MARKER)
