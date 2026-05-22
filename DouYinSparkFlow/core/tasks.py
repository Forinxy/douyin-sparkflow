import asyncio
import hashlib
import logging
import os
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.browser import get_browser
from core.msg_builder import build_message
from core.protocol_dispatch import run_protocol_tasks
from utils.config import get_config, get_userData, normalize_unique_id, save_userData
from utils.logger import setup_logger


config = get_config()
user_data = get_userData()
logger = setup_logger(level=logging.DEBUG)
debug_artifacts_dir = Path("logs/debug_artifacts")
debug_artifacts_dir.mkdir(parents=True, exist_ok=True)


async def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    for attempt in range(retries):
        try:
            return await operation(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                logger.warning("%s failed, retry %s/%s: %s", name, attempt + 1, retries, exc)
                await asyncio.sleep(delay)
            else:
                logger.error("%s failed after %s attempts: %s", name, retries, exc)
                raise


def _safe_name(value):
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)[:80]


def _normalize_target_name(value):
    raw = unicodedata.normalize("NFKC", str(value or ""))
    for token in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        raw = raw.replace(token, "")
    raw = raw.replace("\xa0", " ")
    return " ".join(raw.split()).strip()


def _current_run_mode():
    return "manual" if _is_manual_run() else "scheduled"


async def save_debug_artifacts(page, account_name, target_name, stage):
    if not get_config(force_reload=True).get("saveDebugArtifacts", False):
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{timestamp}-{_safe_name(account_name)}-{_safe_name(target_name)}-{stage}"
    screenshot_path = debug_artifacts_dir / f"{stem}.png"
    html_path = debug_artifacts_dir / f"{stem}.html"

    await page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(await page.content(), encoding="utf-8")
    logger.info("Saved debug artifacts at stage=%s for %s/%s", stage, account_name, target_name)


async def locate_chat_input(page):
    selectors = [
        "xpath=//div[contains(@class, 'chat-input-dccKiL')]//div[@contenteditable='true']",
        "xpath=//div[@contenteditable='true' and @role='textbox']",
        "xpath=(//div[@contenteditable='true'])[last()]",
    ]

    last_error = None
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=10000)
            await locator.click(timeout=5000)
            return locator, selector
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Unable to locate chat input, last error: {last_error}")


async def read_chat_input_text(chat_input):
    try:
        return await chat_input.evaluate(
            """(node) => {
                const raw = node.innerText ?? node.textContent ?? "";
                return raw.trim();
            }"""
        )
    except Exception:
        return ""


async def confirm_message_sent(page, chat_input, message):
    await asyncio.sleep(2)

    input_text = await read_chat_input_text(chat_input)
    if not input_text:
        return True, "chat input cleared"

    first_line = message.split("\n")[0].strip()
    if first_line:
        try:
            bubble = page.locator(f"text={first_line}").last
            if await bubble.count() > 0:
                return True, "message bubble located"
        except Exception:
            pass

    return False, f"chat input still contains: {input_text!r}"


async def detect_message_already_sent(page, chat_input, message):
    try:
        if chat_input is not None:
            sent_ok, detail = await confirm_message_sent(page, chat_input, message)
            if sent_ok:
                return True, detail
    except Exception:
        pass

    first_line = message.split("\n")[0].strip()
    if not first_line:
        return False, ""

    try:
        bubble = page.locator(f"text={first_line}").last
        if await bubble.count() > 0:
            return True, "message bubble located after failure"
    except Exception:
        pass

    return False, ""


def classify_browser_failure(stage, exc):
    detail = str(exc or "")
    lowered = detail.lower()

    if "page crashed" in lowered or "target page, context or browser has been closed" in lowered:
        return "page_crashed"
    if "timeout" in lowered:
        if stage in {"open_creator_home", "open_chat_page"}:
            return "navigation_timeout"
        if stage == "locate_chat_input":
            return "chat_input_timeout"
        if stage == "friend_list":
            return "friend_list_timeout"
        return "timeout"
    if "unable to locate chat input" in lowered:
        return "chat_input_not_found"
    if "could not find the friend list scroll container" in lowered:
        return "friend_list_container_missing"
    if "chat input still contains" in lowered:
        return "send_unconfirmed"
    if "missing targets" in lowered:
        return "friend_not_found"
    if stage in {"open_creator_home", "open_chat_page"}:
        return "navigation_failed"
    return "unknown"


async def scroll_and_select_user(page, account_name, targets):
    friends_tab_selector = 'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
    target_selector = (
        'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]'
        '//div[contains(@class, "semi-list-item-body semi-list-item-body-flex-start")]'
    )
    scrollable_friends_selector = (
        'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div'
    )
    no_more_selector = 'xpath=//div[contains(@class, "no-more-tip-ftdJnu")]'
    loading_selector = 'xpath=//div[contains(@class, "semi-spin")]'
    first_friend_selector = (
        'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div'
    )

    logger.debug("Account %s is opening the friends tab", account_name)
    await page.wait_for_selector(friends_tab_selector)
    await page.locator(friends_tab_selector).click()

    await page.wait_for_selector(first_friend_selector)
    await page.locator(first_friend_selector).click()
    await asyncio.sleep(2)

    normalized_targets = {
        _normalize_target_name(target): str(target)
        for target in targets
        if _normalize_target_name(target)
    }
    found_usernames = set()
    remaining_targets = set(normalized_targets)

    while True:
        target_elements = await page.locator(target_selector).all()

        for element in target_elements:
            try:
                span = element.locator("""xpath=.//span[contains(@class, "item-header-name-")]""")
                target_name = await span.inner_text()
            except Exception:
                continue

            normalized_target_name = _normalize_target_name(target_name)
            if not normalized_target_name:
                continue

            if normalized_target_name in found_usernames:
                continue
            found_usernames.add(normalized_target_name)
            logger.debug("Account %s found friend entry %s", account_name, target_name)

            matched_target_name = normalized_targets.get(normalized_target_name)
            if matched_target_name:
                await element.click()
                logger.info("Account %s selected target friend %s", account_name, target_name)
                if matched_target_name != target_name:
                    logger.info(
                        "Account %s normalized target %r matched visible friend %r",
                        account_name,
                        matched_target_name,
                        target_name,
                    )
                yield matched_target_name

                remaining_targets.discard(normalized_target_name)
                if not remaining_targets:
                    logger.info("Account %s found all target friends", account_name)
                    return
                break
        else:
            if await page.locator(no_more_selector).count() > 0:
                logger.warning(
                    "Account %s reached the end of the friend list. Missing targets: %s",
                    account_name,
                    sorted(normalized_targets[item] for item in remaining_targets),
                )
                return

            if await page.locator(loading_selector).count() > 0:
                logger.debug("Account %s is waiting for more friends to load", account_name)
                await asyncio.sleep(1.5)

            scrollable_element = await page.locator(scrollable_friends_selector).element_handle()
            if not scrollable_element:
                raise RuntimeError(f"Account {account_name} could not find the friend list scroll container")

            await page.evaluate("(element) => element.scrollTop += 800", scrollable_element)
            await asyncio.sleep(1.5)


def _is_manual_run():
    return os.getenv("SPARKFLOW_MANUAL_RUN") == "1"


def _schedule_timezone():
    timezone_name = (
        str(os.getenv("SPARKFLOW_TIMEZONE") or "").strip()
        or str(os.getenv("TZ") or "").strip()
        or "Asia/Shanghai"
    )
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        if timezone_name == "Asia/Shanghai":
            logger.warning("Falling back to fixed UTC+8 because %r is unavailable", timezone_name)
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        logger.warning("Falling back to system timezone because %r is unavailable", timezone_name)
        return datetime.now().astimezone().tzinfo


def _normalize_send_window(config):
    raw = config.get("dailySendWindow", {}) or {}
    normalized = {
        "enabled": bool(raw.get("enabled", False)),
        "startHour": int(raw.get("startHour", 10)),
        "endHour": int(raw.get("endHour", 18)),
        "scheduleIntervalMinutes": max(1, int(raw.get("scheduleIntervalMinutes", 10))),
    }
    if normalized["startHour"] < 0 or normalized["startHour"] > 23:
        normalized["enabled"] = False
    if normalized["endHour"] < 1 or normalized["endHour"] > 24:
        normalized["enabled"] = False
    if normalized["endHour"] <= normalized["startHour"]:
        normalized["enabled"] = False
    if bool(raw.get("enabled", False)) and not normalized["enabled"]:
        logger.warning("Invalid dailySendWindow=%s, disabling windowed sending for this run", raw)
    return normalized


def _account_identity(user):
    return str(user.get("unique_id") or user.get("username") or "unknown").strip()


def _parse_sent_at(raw_value, local_tz):
    if not raw_value:
        return None
    raw = str(raw_value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz)


def _manual_run_failed_only():
    return _is_manual_run() and os.getenv("SPARKFLOW_MANUAL_FAILED_ONLY") == "1"


def _target_sent_today(user, target_name, now):
    history = dict(user.get("message_history") or {})
    entry = history.get(target_name) or {}
    sent_at = _parse_sent_at(entry.get("sentAt"), now.tzinfo)
    return bool(sent_at and sent_at.date() == now.date())


def _target_failed_today(user, target_name, now):
    queue = dict(user.get("failure_queue") or {})
    entry = queue.get(target_name) or {}
    last_attempt_at = _parse_sent_at(entry.get("lastAttemptAt"), now.tzinfo)
    return bool(last_attempt_at and last_attempt_at.date() == now.date())


def _pending_failed_targets(user, now):
    queue = dict(user.get("failure_queue") or {})
    targets = []
    for target_name in user.get("targets") or []:
        if _target_sent_today(user, target_name, now):
            continue
        if target_name in queue and _target_failed_today(user, target_name, now):
            targets.append(target_name)
    return targets


def _scheduled_send_time(user, target_name, send_window, now):
    window_minutes = (send_window["endHour"] - send_window["startHour"]) * 60
    start_of_window = now.replace(
        hour=send_window["startHour"],
        minute=0,
        second=0,
        microsecond=0,
    )
    seed = f"{now.date().isoformat()}|{_account_identity(user)}|{target_name}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    offset_minutes = int.from_bytes(digest[:8], "big") % window_minutes
    return start_of_window + timedelta(minutes=offset_minutes)


def _select_due_targets(user, send_window, now):
    targets = list(user.get("targets") or [])
    if not send_window.get("enabled") or _is_manual_run():
        return targets, [], [], []

    window_start = now.replace(
        hour=send_window["startHour"],
        minute=0,
        second=0,
        microsecond=0,
    )
    window_end = now.replace(
        hour=send_window["endHour"],
        minute=0,
        second=0,
        microsecond=0,
    )
    if now < window_start or now > window_end:
        already_sent = []
        pending_targets = []
        queued_failures = []
        for target_name in targets:
            if _target_sent_today(user, target_name, now):
                already_sent.append(target_name)
                continue
            if _target_failed_today(user, target_name, now):
                queued_failures.append(target_name)
                continue
            pending_targets.append((target_name, _scheduled_send_time(user, target_name, send_window, now)))
        return [], already_sent, pending_targets, queued_failures

    due_targets = []
    already_sent = []
    pending_targets = []
    queued_failures = []
    for target_name in targets:
        if _target_sent_today(user, target_name, now):
            already_sent.append(target_name)
            continue
        if _target_failed_today(user, target_name, now):
            queued_failures.append(target_name)
            continue
        scheduled_at = _scheduled_send_time(user, target_name, send_window, now)
        if now >= scheduled_at:
            due_targets.append(target_name)
        else:
            pending_targets.append((target_name, scheduled_at))
    return due_targets, already_sent, pending_targets, queued_failures


def _prepare_active_users_for_run(active_config, active_user_data):
    schedule_tz = _schedule_timezone()
    now = datetime.now(schedule_tz)

    if _manual_run_failed_only():
        logger.info("SPARKFLOW_MANUAL_RUN=1, retrying queued failures only")
        runnable_users = []
        for user in active_user_data:
            retry_targets = _pending_failed_targets(user, now)
            already_sent = [target for target in user.get("targets") or [] if _target_sent_today(user, target, now)]
            logger.info(
                "manual-retry user=%s retryTargets=%s alreadySentToday=%s",
                user.get("username", "unknown"),
                retry_targets,
                already_sent,
            )
            if retry_targets:
                runnable_user = dict(user)
                runnable_user["targets"] = retry_targets
                runnable_users.append(runnable_user)
        if not runnable_users:
            logger.info("No queued failures are pending for manual retry")
        return runnable_users

    if _is_manual_run():
        logger.info("SPARKFLOW_MANUAL_RUN=1, bypassing daily send window")
        return [dict(user, targets=list(user.get("targets") or [])) for user in active_user_data]

    send_window = _normalize_send_window(active_config)
    if not send_window.get("enabled"):
        return [dict(user, targets=list(user.get("targets") or [])) for user in active_user_data]

    logger.info(
        "dailySendWindow enabled startHour=%s endHour=%s intervalMinutes=%s timezone=%s now=%s",
        send_window["startHour"],
        send_window["endHour"],
        send_window["scheduleIntervalMinutes"],
        getattr(schedule_tz, "key", str(schedule_tz)),
        now.isoformat(timespec="seconds"),
    )

    runnable_users = []
    for user in active_user_data:
        due_targets, already_sent, pending_targets, queued_failures = _select_due_targets(user, send_window, now)
        pending_preview = [
            f"{target_name}@{scheduled_at.strftime('%H:%M')}"
            for target_name, scheduled_at in pending_targets[:5]
        ]
        logger.info(
            "windowed user=%s dueTargets=%s alreadySentToday=%s pendingTargets=%s queuedFailures=%s",
            user.get("username", "unknown"),
            due_targets,
            already_sent,
            pending_preview,
            queued_failures,
        )
        if due_targets:
            runnable_user = dict(user)
            runnable_user["targets"] = due_targets
            runnable_users.append(runnable_user)

    if not runnable_users:
        logger.info("No targets are due for the current windowed run")
    return runnable_users


def _account_match_tokens(user):
    tokens = set()
    username = str(user.get("username") or "").strip()
    unique_id = str(user.get("unique_id") or "").strip()
    normalized_unique_id = normalize_unique_id(unique_id)
    if username:
        tokens.add(username.lower())
    if unique_id:
        tokens.add(unique_id.lower())
    if normalized_unique_id:
        tokens.add(normalized_unique_id.lower())
    return tokens


def _find_matching_account(accounts, user):
    target_username = str(user.get("username") or "").strip()
    target_unique_id = normalize_unique_id(user.get("unique_id"))
    if not target_username and not target_unique_id:
        return None

    for account in accounts:
        account_username = str(account.get("username") or "").strip()
        account_unique_id = normalize_unique_id(account.get("unique_id"))
        if target_unique_id and account_unique_id == target_unique_id:
            return account
        if target_username and account_username == target_username:
            return account
    return None


def _persist_browser_send_failure(user, target_name, message, category, reason, attempted_at):
    accounts = get_userData(force_reload=True)
    matched_account = _find_matching_account(accounts, user)
    if matched_account is None:
        logger.warning(
            "Could not find account to persist browser send failure for user=%s target=%s",
            user.get("username", "unknown"),
            target_name,
        )
        return

    queue = dict(matched_account.get("failure_queue") or {})
    existing_entry = dict(queue.get(target_name) or {})
    queue[target_name] = {
        "category": category,
        "reason": reason,
        "message": message,
        "firstAttemptAt": existing_entry.get("firstAttemptAt") or attempted_at,
        "lastAttemptAt": attempted_at,
        "attemptCount": int(existing_entry.get("attemptCount") or 0) + 1,
        "lastRunMode": _current_run_mode(),
    }
    matched_account["failure_queue"] = queue
    save_userData(accounts)

    user_queue = dict(user.get("failure_queue") or {})
    user_queue[target_name] = dict(queue[target_name])
    user["failure_queue"] = user_queue

    logger.warning(
        "Queued failed browser send for %s/%s category=%s reason=%s",
        matched_account.get("username", "unknown"),
        target_name,
        category,
        reason,
    )


def _persist_browser_send_success(user, target_name, message, sent_at):
    accounts = get_userData(force_reload=True)
    matched_account = _find_matching_account(accounts, user)
    if matched_account is None:
        logger.warning(
            "Could not find account to persist browser send history for user=%s target=%s",
            user.get("username", "unknown"),
            target_name,
        )
        return

    history = dict(matched_account.get("message_history") or {})
    history[target_name] = {
        "message": message,
        "sentAt": sent_at,
    }
    matched_account["message_history"] = history
    queue = dict(matched_account.get("failure_queue") or {})
    queue.pop(target_name, None)
    if queue:
        matched_account["failure_queue"] = queue
    else:
        matched_account.pop("failure_queue", None)
    save_userData(accounts)

    user_history = dict(user.get("message_history") or {})
    user_history[target_name] = {
        "message": message,
        "sentAt": sent_at,
    }
    user["message_history"] = user_history
    user_queue = dict(user.get("failure_queue") or {})
    user_queue.pop(target_name, None)
    if user_queue:
        user["failure_queue"] = user_queue
    else:
        user.pop("failure_queue", None)

    logger.info(
        "Persisted browser send history for %s/%s at %s",
        matched_account.get("username", "unknown"),
        target_name,
        sent_at,
    )


def _split_sender_modes(active_config, runnable_user_data):
    if not active_config.get("useProtocolSender", True):
        return [], runnable_user_data

    browser_sender_accounts = {
        str(item).strip().lower()
        for item in (active_config.get("browserSenderAccounts") or [])
        if str(item).strip()
    }
    if not browser_sender_accounts:
        return runnable_user_data, []

    protocol_users = []
    browser_users = []
    for user in runnable_user_data:
        if _account_match_tokens(user) & browser_sender_accounts:
            browser_users.append(user)
        else:
            protocol_users.append(user)
    return protocol_users, browser_users


async def run_browser_tasks(active_config, browser_user_data):
    if not browser_user_data:
        return

    playwright, browser = await get_browser()
    try:
        semaphore = asyncio.Semaphore(active_config["taskCount"] if active_config["multiTask"] else 1)
        tasks = []
        for user in browser_user_data:
            logger.info("Using browser sender for user=%s targets=%s", user.get("username", "unknown"), user["targets"])
            tasks.append(do_user_task(browser, user, semaphore))

        await asyncio.gather(*tasks)
    finally:
        await playwright.stop()
        await browser.close()


async def do_user_task(browser, user, semaphore):
    async with semaphore:
        account_name = user.get("username", "unknown")
        cookies = user["cookies"]
        targets = user["targets"]
        context = await browser.new_context()
        context.set_default_navigation_timeout(120000)
        context.set_default_timeout(120000)
        yielded_targets = set()

        try:
            page = await context.new_page()
            try:
                await retry_operation(
                    "open creator home",
                    page.goto,
                    retries=3,
                    delay=5,
                    url="https://creator.douyin.com/",
                )
                await context.add_cookies(cookies)
                await retry_operation(
                    "open chat page",
                    page.goto,
                    retries=3,
                    delay=5,
                    url="https://creator.douyin.com/creator-micro/data/following/chat",
                )
            except Exception as exc:
                attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                category = classify_browser_failure("open_chat_page", exc)
                reason = str(exc)
                logger.exception("Account %s failed before target delivery", account_name)
                for target_name in targets:
                    _persist_browser_send_failure(user, target_name, "", category, reason, attempted_at)
                return

            logger.info("Account %s started the message flow", account_name)
            try:
                async for target_name in scroll_and_select_user(page, account_name, targets):
                    yielded_targets.add(target_name)
                    message = ""
                    chat_input = None
                    try:
                        await save_debug_artifacts(page, account_name, target_name, "selected-friend")
                        chat_input, selector_used = await locate_chat_input(page)
                        logger.info("Using chat input selector %s for %s/%s", selector_used, account_name, target_name)

                        message = build_message()
                        logger.info("Prepared message for %s/%s: %r", account_name, target_name, message)

                        lines = message.split("\n")
                        for index, line in enumerate(lines):
                            await chat_input.type(line, delay=50)
                            if index < len(lines) - 1:
                                await chat_input.press("Shift+Enter")

                        await save_debug_artifacts(page, account_name, target_name, "typed-message")

                        logger.info("Pressing Enter to send message for %s/%s", account_name, target_name)
                        await chat_input.press("Enter")

                        sent_ok, detail = await confirm_message_sent(page, chat_input, message)
                        await save_debug_artifacts(page, account_name, target_name, "after-send")

                        if not sent_ok:
                            raise RuntimeError(detail)

                        logger.info("Message send confirmed for %s/%s: %s", account_name, target_name, detail)
                        _persist_browser_send_success(
                            user,
                            target_name,
                            message,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        )
                    except Exception as exc:
                        sent_ok = False
                        detail = ""
                        if message:
                            sent_ok, detail = await detect_message_already_sent(page, chat_input, message)
                        if sent_ok:
                            logger.warning(
                                "Recovered send outcome for %s/%s after failure: %s",
                                account_name,
                                target_name,
                                detail,
                            )
                            _persist_browser_send_success(
                                user,
                                target_name,
                                message,
                                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            )
                            continue

                        logger.exception("Send flow failed for %s/%s", account_name, target_name)
                        await save_debug_artifacts(page, account_name, target_name, "send-error")
                        _persist_browser_send_failure(
                            user,
                            target_name,
                            message,
                            classify_browser_failure("send_flow", exc),
                            str(exc),
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        )
            except Exception as exc:
                remaining_targets = [target for target in targets if target not in yielded_targets]
                attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                category = classify_browser_failure("friend_list", exc)
                reason = str(exc)
                logger.exception("Target selection failed for %s", account_name)
                for target_name in remaining_targets:
                    _persist_browser_send_failure(user, target_name, "", category, reason, attempted_at)
                return

            missing_targets = [target for target in targets if target not in yielded_targets]
            if missing_targets:
                attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for target_name in missing_targets:
                    _persist_browser_send_failure(
                        user,
                        target_name,
                        "",
                        "friend_not_found",
                        "target not selected from friend list",
                        attempted_at,
                    )
        finally:
            await context.close()


async def runTasks():
    active_config = get_config(force_reload=True)
    all_user_data = get_userData(force_reload=True)
    active_user_data = [user for user in all_user_data if user.get("enabled", True)]
    disabled_user_data = [user for user in all_user_data if not user.get("enabled", True)]

    logger.info("Starting tasks with config")
    logger.info("multiTask=%s taskCount=%s", active_config["multiTask"], active_config["taskCount"])
    logger.info("messageTemplate=%s", active_config["messageTemplate"])
    logger.info("sendStrategy=%s", active_config.get("sendStrategy", {}))
    logger.info("hitokotoTypes=%s", active_config["hitokotoTypes"])
    logger.info("enabledUsers=%s disabledUsers=%s", len(active_user_data), len(disabled_user_data))
    for user in active_user_data:
        logger.info("user=%s targets=%s", user.get("username", "unknown"), user["targets"])
    for user in disabled_user_data:
        logger.info("skipping disabled user=%s", user.get("username", "unknown"))

    if not active_user_data:
        logger.warning("No enabled accounts are available for the task run")
        return

    runnable_user_data = _prepare_active_users_for_run(active_config, active_user_data)
    if not runnable_user_data:
        return

    with task_run_lock():
        protocol_user_data, browser_user_data = _split_sender_modes(active_config, runnable_user_data)
        if protocol_user_data:
            await run_protocol_tasks(active_config, protocol_user_data, build_message)
        await run_browser_tasks(active_config, browser_user_data)


@contextmanager
def task_run_lock():
    lock_path = Path("logs/task.run.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _lock_owner_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    while True:
        try:
            handle = lock_path.open("x", encoding="utf-8")
            break
        except FileExistsError as exc:
            raw_pid = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
            stale_pid = None
            try:
                stale_pid = int(raw_pid)
            except (TypeError, ValueError):
                stale_pid = None

            if stale_pid is not None and not _lock_owner_is_alive(stale_pid):
                logger.warning("Removing stale task lock owned by missing pid=%s", stale_pid)
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue

            if stale_pid is None:
                logger.warning("Removing unreadable stale task lock with contents=%r", raw_pid)
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue

            raise RuntimeError("another task run is already in progress") from exc

    try:
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield
    finally:
        handle.close()
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
