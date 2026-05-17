import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Run a headless Douyin relogin worker.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--account-index", type=int, required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--screenshot-path", required=True)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser.parse_args()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def write_state(path: Path, **payload):
    base = {"updated_at": now_iso(), **payload}
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")


async def capture_login_screenshot(page, screenshot_path: Path, prefer_verification=False):
    verification_selectors = [
        ".pc-login-verification-modal",
        ".semi-modal-content",
        ".semi-modal",
        'div[role="dialog"]',
    ]
    qr_selectors = [
        ".login-img-code-wrapper",
        'div[class*="qrcode"]',
        "canvas",
        ".login-mask",
        ".login-guide-container",
    ]
    selectors = verification_selectors + qr_selectors if prefer_verification else qr_selectors + verification_selectors
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                await locator.scroll_into_view_if_needed()
                await locator.screenshot(path=str(screenshot_path))
                return selector
        except Exception:
            continue
    await page.screenshot(path=str(screenshot_path), full_page=True)
    return "page"


async def is_verification_step(page):
    modal_selectors = [
        ".pc-login-verification-modal",
        ".semi-modal-content",
        ".semi-modal",
        'div[role="dialog"]',
    ]
    for selector in modal_selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return True
        except Exception:
            continue

    verification_texts = [
        "身份验证",
        "以确保为本人操作",
        "短信验证码",
        "安全验证",
    ]
    for text in verification_texts:
        locator = page.get_by_text(text, exact=False).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return True
        except Exception:
            continue
    return False


async def refresh_expired_qr_if_needed(page):
    refresh_texts = ["点击刷新", "刷新", "刷新二维码"]
    expired_texts = ["二维码失效", "二维码已失效"]

    for text in refresh_texts:
        locator = page.get_by_text(text, exact=False).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                await locator.scroll_into_view_if_needed()
                await locator.click(force=True, timeout=10000)
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    for text in expired_texts:
        locator = page.get_by_text(text, exact=False).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                await locator.scroll_into_view_if_needed()
                await locator.click(force=True, timeout=10000)
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue
    return False


async def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    state_file = Path(args.state_file).resolve()
    screenshot_path = Path(args.screenshot_path).resolve()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    import sys

    sys.path.insert(0, str(repo_root))

    from core.browser import get_browser
    from core.login import collect_login_result
    from utils.config import get_userData, save_userData

    accounts = get_userData(force_reload=True)
    account = accounts[args.account_index]
    write_state(
        state_file,
        status="starting",
        message=f"Preparing relogin session for {account.get('username', 'unknown')}",
        account_index=args.account_index,
        username=account.get("username", ""),
        screenshot_path=str(screenshot_path),
    )

    playwright = browser = context = page = None
    started_at = asyncio.get_running_loop().time()
    timeout_seconds = max(args.timeout_seconds, 60)

    try:
        playwright, browser = await get_browser(GUI=False)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1200},
            device_scale_factor=2,
        )
        page = await context.new_page()

        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        selectors = [
            "canvas",
            ".login-img-code-wrapper",
            'div[class*="qrcode"]',
            ".login-mask",
            ".login-guide-container",
            ".pc-login-verification-modal",
            ".semi-modal-content",
            ".semi-modal",
            'div[role="dialog"]',
        ]

        while True:
            if asyncio.get_running_loop().time() - started_at > timeout_seconds:
                write_state(
                    state_file,
                    status="timeout",
                    message="Login session timed out before authentication completed",
                    account_index=args.account_index,
                    username=account.get("username", ""),
                    screenshot_path=str(screenshot_path),
                )
                return

            await refresh_expired_qr_if_needed(page)

            unique_id_locator = page.locator(
                'xpath=//*[contains(@id, "garfish_app_for_douyin_creator_pc_home")]'
                '/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[3]'
            ).first
            name_locator = page.locator(
                'xpath=//*[contains(@id, "garfish_app_for_douyin_creator_pc_home")]'
                '/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[1]/div[1]'
            ).first

            if await unique_id_locator.count() > 0 and await name_locator.count() > 0:
                result = await collect_login_result(page, context, timeout_ms=5000)
                refreshed_accounts = get_userData(force_reload=True)
                refreshed_accounts[args.account_index]["unique_id"] = result["unique_id"]
                refreshed_accounts[args.account_index]["username"] = result["username"]
                refreshed_accounts[args.account_index]["cookies"] = result["cookies"]
                save_userData(refreshed_accounts)
                await page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
                write_state(
                    state_file,
                    status="authenticated",
                    message=f"Authenticated as {result['username']}",
                    account_index=args.account_index,
                    username=result["username"],
                    unique_id=result["unique_id"],
                    screenshot_path=str(screenshot_path),
                )
                return

            verification = await is_verification_step(page)
            await capture_login_screenshot(page, screenshot_path, prefer_verification=verification)

            write_state(
                state_file,
                status="waiting_verify" if verification else "awaiting_scan",
                message="Identity verification is required" if verification else "Scan the QR code with the Douyin app",
                account_index=args.account_index,
                username=account.get("username", ""),
                screenshot_path=str(screenshot_path),
            )
            await asyncio.sleep(args.poll_interval)
    except Exception as exc:
        write_state(
            state_file,
            status="error",
            message=str(exc),
            account_index=args.account_index,
            username=account.get("username", ""),
            screenshot_path=str(screenshot_path),
        )
        raise
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AttributeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
