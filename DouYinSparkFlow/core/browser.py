import os
import subprocess
import sys
import traceback
from pathlib import Path

from playwright.async_api import async_playwright
from rich.console import Console

from utils.config import DEBUG, Environment, get_environment


console = Console()
PLAYWRIGHT_BROWSERS_PATH = "../chrome"


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


async def install_browser():
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        console.print("[bold green]Browser install completed. Please run the command again.[/bold green]")
    except subprocess.CalledProcessError as exc:
        console.print(f"[bold red]Browser install failed: {exc}[/bold red]")


async def get_browser(GUI=False):
    configure_playwright_environment()

    headless = not GUI
    if get_environment() == Environment.LOCAL and DEBUG:
        headless = False

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage"],
        )
        return playwright, browser
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) and get_environment() != Environment.GITHUBACTION:
            console.print("[bold red]Playwright browser is missing.[/bold red]")
            await install_browser()
            sys.exit(1)
        traceback.print_exc()
        raise
