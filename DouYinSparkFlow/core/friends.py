import asyncio
from pathlib import Path
import time

from core.browser import get_browser


CHAT_PAGE_URL = "https://creator.douyin.com/creator-micro/data/following/chat"
FRIENDS_TAB_SELECTORS = (
    'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]',
    'xpath=//*[@id="sub-app"]//*[self::div or self::span or self::button][contains(normalize-space(.), "朋友私信") and string-length(normalize-space(.)) <= 20]',
    'xpath=//*[@id="sub-app"]//*[self::div or self::span or self::button][normalize-space()="朋友"]',
)
FRIEND_NAME_SELECTOR = 'xpath=//*[@id="sub-app"]//span[contains(@class, "item-header-name-")]'
SCROLLABLE_FRIENDS_SELECTORS = (
    'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div',
    'xpath=//*[@id="sub-app"]//ul/div',
    'xpath=//*[@id="sub-app"]//ul',
)
NO_MORE_SELECTOR = 'xpath=//div[contains(@class, "no-more-tip-ftdJnu")]'
LOADING_SELECTOR = 'xpath=//div[contains(@class, "semi-spin")]'
LOGIN_MASK_SELECTORS = [".login-mask", ".login-guide-container", ".login-img-code-wrapper"]
EMPTY_LIST_KEYWORDS = ("暂无", "没有", "空空", "还没有")
LOGIN_KEYWORDS = ("扫码登录", "登录抖音", "请登录", "登录已过期", "重新登录")


def update_collection_progress(new_names_count, no_more_visible, scroll_moved, idle_rounds, stuck_rounds, idle_limit=5, stuck_limit=2):
    next_idle_rounds = 0 if new_names_count > 0 else idle_rounds + 1
    next_stuck_rounds = 0 if scroll_moved else stuck_rounds + 1
    should_stop = no_more_visible or next_idle_rounds >= idle_limit or next_stuck_rounds >= stuck_limit
    return should_stop, next_idle_rounds, next_stuck_rounds


async def _ensure_logged_in(page):
    current_url = page.url or ""
    if "login" in current_url or "passport" in current_url:
        raise RuntimeError("账号登录已失效，请重新扫码登录")

    for selector in LOGIN_MASK_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                raise RuntimeError("账号登录已失效，请重新扫码登录")
        except RuntimeError:
            raise
        except Exception:
            continue


async def _body_text(page, limit=600):
    try:
        text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


async def _page_diagnosis(page):
    await _ensure_logged_in(page)
    body_text = await _body_text(page)
    if any(keyword in body_text for keyword in LOGIN_KEYWORDS):
        raise RuntimeError("账号登录已失效或页面要求重新登录，请重新扫码登录")
    if any(keyword in body_text for keyword in EMPTY_LIST_KEYWORDS):
        return "页面提示当前没有可读取的朋友私信好友"
    return f"未等到好友列表。当前URL={page.url}，页面提示={body_text or '空'}"


async def _open_friends_tab(page):
    if await page.locator(FRIEND_NAME_SELECTOR).count() > 0:
        return

    last_error = None
    for selector in FRIENDS_TAB_SELECTORS:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=10000)
            await locator.click(timeout=5000)
            await asyncio.sleep(1.5)
            return
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"未找到“朋友私信”入口，可能页面结构变化或账号未登录。最后错误：{last_error}")


async def _wait_for_friend_name_or_empty(page, timeout_ms=45000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        await _ensure_logged_in(page)
        names = page.locator(FRIEND_NAME_SELECTOR)
        if await names.count() > 0:
            first = names.first
            try:
                if await first.is_visible():
                    return True
            except Exception:
                return True

        body_text = await _body_text(page, limit=300)
        if any(keyword in body_text for keyword in EMPTY_LIST_KEYWORDS):
            return False
        if any(keyword in body_text for keyword in LOGIN_KEYWORDS):
            raise RuntimeError("账号登录已失效或页面要求重新登录，请重新扫码登录")

        loading = page.locator(LOADING_SELECTOR).first
        if await loading.count() > 0 and await loading.is_visible():
            await asyncio.sleep(1.5)
        else:
            await asyncio.sleep(1)

    diagnosis = await _page_diagnosis(page)
    raise RuntimeError(diagnosis)


async def _collect_visible_friend_names(page):
    names = []
    for raw_name in await page.locator(FRIEND_NAME_SELECTOR).all_inner_texts():
        name = raw_name.strip()
        if name:
            names.append(name)
    return names


async def _find_scrollable_friends_element(page):
    for selector in SCROLLABLE_FRIENDS_SELECTORS:
        try:
            handle = await page.locator(selector).first.element_handle(timeout=2000)
            if handle:
                return handle
        except Exception:
            continue

    try:
        handle = await page.evaluate_handle(
            """() => {
                const firstName = document.querySelector('#sub-app span[class*="item-header-name-"]');
                let node = firstName;
                while (node && node !== document.body) {
                    const style = window.getComputedStyle(node);
                    const overflow = `${style.overflow} ${style.overflowY}`;
                    if (node.scrollHeight > node.clientHeight + 20 && /(auto|scroll|overlay)/.test(overflow)) {
                        return node;
                    }
                    node = node.parentElement;
                }
                return document.scrollingElement || document.documentElement;
            }"""
        )
        return handle.as_element()
    except Exception:
        return None


async def collect_friend_names(page):
    await _open_friends_tab(page)
    has_friends = await _wait_for_friend_name_or_empty(page)
    if not has_friends:
        return []
    await asyncio.sleep(2)

    found_names = []
    seen_names = set()
    idle_rounds = 0
    stuck_rounds = 0

    while True:
        new_names_count = 0
        for name in await _collect_visible_friend_names(page):
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

        scrollable_element = await _find_scrollable_friends_element(page)
        if not scrollable_element:
            if found_names:
                return found_names
            diagnosis = await _page_diagnosis(page)
            raise RuntimeError(f"未找到好友列表滚动容器；{diagnosis}")

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
