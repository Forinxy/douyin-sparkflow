import asyncio
from pathlib import Path

from core.browser import get_browser


CHAT_PAGE_URL = "https://creator.douyin.com/creator-micro/data/following/chat"
FRIENDS_TAB_SELECTOR = 'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
TARGET_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]'
    '//div[contains(@class, "semi-list-item-body semi-list-item-body-flex-start")]'
)
SCROLLABLE_FRIENDS_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div'
)
NO_MORE_SELECTOR = 'xpath=//div[contains(@class, "no-more-tip-ftdJnu")]'
LOADING_SELECTOR = 'xpath=//div[contains(@class, "semi-spin")]'
FIRST_FRIEND_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div'
)
FRIEND_NAME_SELECTOR = """xpath=.//span[contains(@class, "item-header-name-")]"""
LOGIN_MASK_SELECTORS = [".login-mask", ".login-guide-container", ".login-img-code-wrapper"]


def update_collection_progress(new_names_count, no_more_visible, scroll_moved, idle_rounds, stuck_rounds, idle_limit=5, stuck_limit=2):
    next_idle_rounds = 0 if new_names_count > 0 else idle_rounds + 1
    next_stuck_rounds = 0 if scroll_moved else stuck_rounds + 1
    should_stop = no_more_visible or next_idle_rounds >= idle_limit or next_stuck_rounds >= stuck_limit
    return should_stop, next_idle_rounds, next_stuck_rounds


async def _ensure_logged_in(page):
    for selector in LOGIN_MASK_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                raise RuntimeError("账号登录已失效，请重新扫码登录")
        except RuntimeError:
            raise
        except Exception:
            continue


async def collect_friend_names(page):
    await page.wait_for_selector(FRIENDS_TAB_SELECTOR, timeout=30000)
    await page.locator(FRIENDS_TAB_SELECTOR).click()

    await page.wait_for_selector(FIRST_FRIEND_SELECTOR, timeout=30000)
    await page.locator(FIRST_FRIEND_SELECTOR).click()
    await asyncio.sleep(2)

    found_names = []
    seen_names = set()
    idle_rounds = 0
    stuck_rounds = 0

    while True:
        target_elements = await page.locator(TARGET_SELECTOR).all()
        new_names_count = 0
        for element in target_elements:
            try:
                name = (await element.locator(FRIEND_NAME_SELECTOR).inner_text()).strip()
            except Exception:
                continue
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            found_names.append(name)
            new_names_count += 1

        no_more = page.locator(NO_MORE_SELECTOR).first
        if await no_more.count() > 0 and await no_more.is_visible():
            return found_names

        loading = page.locator(LOADING_SELECTOR).first
        if await loading.count() > 0 and await loading.is_visible():
            await asyncio.sleep(1.5)

        scrollable_element = await page.locator(SCROLLABLE_FRIENDS_SELECTOR).element_handle()
        if not scrollable_element:
            if found_names:
                return found_names
            raise RuntimeError("未找到好友列表滚动容器")

        before_top = await page.evaluate("(element) => element.scrollTop", scrollable_element)
        await page.evaluate("(element) => element.scrollTop += 800", scrollable_element)
        await asyncio.sleep(1.5)
        after_top = await page.evaluate("(element) => element.scrollTop", scrollable_element)

        should_stop, idle_rounds, stuck_rounds = update_collection_progress(
            new_names_count=new_names_count,
            no_more_visible=False,
            scroll_moved=after_top > before_top,
            idle_rounds=idle_rounds,
            stuck_rounds=stuck_rounds,
        )
        if should_stop:
            return found_names


async def fetch_account_friends(account):
    cookies = list(account.get("cookies") or [])
    if not cookies:
        raise RuntimeError("账号没有可用 cookies，请重新扫码登录")

    playwright = browser = context = page = None
    try:
        playwright, browser = await get_browser(GUI=False)
        context = await browser.new_context()
        context.set_default_navigation_timeout(120000)
        context.set_default_timeout(120000)
        page = await context.new_page()

        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        await context.add_cookies(cookies)
        await page.goto(CHAT_PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        await _ensure_logged_in(page)
        friends = await collect_friend_names(page)
        return friends
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"刷新好友列表失败，请重试：{exc}") from exc
    finally:
        if page:
            await page.close()
        if context:
            await context.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
