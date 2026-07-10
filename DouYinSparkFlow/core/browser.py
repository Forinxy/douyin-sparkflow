import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

from playwright.async_api import async_playwright
from rich.console import Console

from utils.config import DEBUG, Environment, get_environment


console = Console()
PLAYWRIGHT_BROWSERS_PATH = "../chrome"
DEFAULT_PROFILE_ROOT = "/opt/douyin-sparkflow/state/browser-profiles"


def _local_browser_bundle_path():
    return Path(__file__).resolve().parent / PLAYWRIGHT_BROWSERS_PATH


def configure_playwright_environment():
    if os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        return

    env = get_environment()
    if env == Environment.PACKED:
        bundle_path = Path(sys.executable).resolve().parent / PLAYWRIGHT_BROWSERS_PATH
    else:
        bundle_path = _local_browser_bundle_path()

    if bundle_path.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundle_path.resolve())


def _headless_for(GUI=False):
    headful_env = str(os.getenv("SPARKFLOW_BROWSER_HEADFUL") or "").strip().lower()
    if headful_env in {"1", "true", "yes", "on"}:
        return False

    headless = not GUI
    if get_environment() == Environment.LOCAL and DEBUG:
        headless = False
    return headless


def _browser_args():
    return [
        "--disable-dev-shm-usage",
        "--no-sandbox",
    ]


def sanitize_profile_name(value):
    raw = str(value or "").strip()
    if not raw:
        raw = "unknown"
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", raw)
    safe = safe.strip("._-") or "unknown"
    return safe[:80]


def browser_profile_root(root=None):
    configured = (
        root
        or os.getenv("SPARKFLOW_BROWSER_PROFILE_ROOT")
        or DEFAULT_PROFILE_ROOT
    )
    return Path(configured)


async def install_browser():
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        console.print("[bold green]Browser install completed. Please run the command again.[/bold green]")
    except subprocess.CalledProcessError as exc:
        console.print(f"[bold red]Browser install failed: {exc}[/bold red]")


async def get_browser(GUI=False):
    configure_playwright_environment()

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=_headless_for(GUI),
            args=_browser_args(),
        )
        return playwright, browser
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) and get_environment() != Environment.GITHUBACTION:
            console.print("[bold red]Playwright browser is missing.[/bold red]")
            await install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise


async def get_persistent_browser_context(profile_name, GUI=False, root=None):
    configure_playwright_environment()

    profile_dir = browser_profile_root(root) / sanitize_profile_name(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=_headless_for(GUI),
            viewport={"width": 1600, "height": 1000},
            args=_browser_args(),
        )
        return playwright, context, profile_dir
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) and get_environment() != Environment.GITHUBACTION:
            console.print("[bold red]Playwright browser is missing.[/bold red]")
            await install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise
