import asyncio

import contextlib

import math

import secrets

from dataclasses import asdict, dataclass, field

from datetime import datetime

from pathlib import Path



from core.browser import get_browser

from core.login import XPATHS, collect_login_result

from utils.config import (
    get_app_settings,
    get_userData,
    normalize_unique_id,
    repo_root,
    save_userData,
    upsert_user_account,
)
import logging

import traceback



logger = logging.getLogger(__name__)



@dataclass

class LoginSessionState:
    session_id: str

    status: str = "idle"

    message: str = ""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    screenshot_path: str = ""
    screenshot_updated_at: str = ""
    unique_id: str = ""
    username: str = ""
    cookies: list = field(default_factory=list)
    pending_command: str = field(default=None)
    last_command: str = ""
    relogin_unique_id: str = ""
    relogin_username: str = ""
    default_targets: list = field(default_factory=list)


    def touch(self, status=None, message=None):

        self.updated_at = datetime.now().isoformat(timespec="seconds")

        if status:

            self.status = status

        if message is not None:

            self.message = message

    def mark_screenshot_updated(self):
        self.screenshot_updated_at = datetime.now().isoformat(timespec="seconds")





class LoginSessionManager:
    def __init__(self):

        self._lock = asyncio.Lock()

        self._state = None

        self._task = None

        self._cancel_event = None

        self._background_tasks = set()

        self._artifact_dir = repo_root() / "logs" / "login_sessions"

        self._artifact_dir.mkdir(parents=True, exist_ok=True)



    def get_public_state(self):
        if not self._state:

            return None

        state = asdict(self._state)

        state["has_cookies"] = bool(self._state.cookies)
        state.pop("cookies", None)
        return state

    def _find_account_by_unique_id(self, accounts, unique_id):
        normalized = normalize_unique_id(unique_id)
        for account in accounts:
            if normalize_unique_id(account.get("unique_id")) == normalized:
                return account
        return None


    async def send_command(self, cmd: str):

        async with self._lock:

            if self._state:
                logger.info("Queued login session command for %s: %s", self._state.session_id, cmd)

                self._state.pending_command = cmd
                self._state.last_command = str(cmd or "").strip()
                normalized_cmd = self._normalize_code_command(cmd)
                if normalized_cmd.startswith("click:"):
                    display = normalized_cmd.split(":", 1)[1].strip() or normalized_cmd
                    self._state.touch(message=f"已提交命令：{display}，等待远端页面执行…")
                else:
                    self._state.touch(status="submitting_code", message="验证码已提交到远端浏览器，正在输入并验证…")



    def _session_screenshot_path(self, session_id):
        return self._artifact_dir / f"{session_id}.png"

    async def _capture_login_screenshot(self, page, screenshot_path, prefer_verification=False):
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
        best_locator = None
        best_area = -1
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.count() > 0 and await locator.is_visible():
                    box = await locator.bounding_box()
                    area = (box["width"] * box["height"]) if box else 0
                    if area > best_area:
                        best_area = area
                        best_locator = (locator, selector)
            except Exception:
                continue
        if best_locator:
            locator, selector = best_locator
            await locator.scroll_into_view_if_needed()
            await locator.screenshot(path=screenshot_path)
            return selector
        await page.screenshot(path=screenshot_path, full_page=True)
        return "page"

    async def _is_verification_step(self, page):
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

    async def _is_verification_method_selection_step(self, page, scope=None):
        actual_scope = scope or page
        option_texts = [
            "接收短信验证码",
            "手机刷脸验证",
            "验证登录密码",
            "发送短信验证",
        ]
        visible_matches = 0
        for text in option_texts:
            locator = actual_scope.get_by_text(text, exact=False).first
            try:
                if await locator.count() > 0 and await locator.is_visible():
                    visible_matches += 1
            except Exception:
                continue
        return visible_matches >= 2

    async def _enter_sms_verification_flow(self, page, scope, state):
        sms_target = await self._find_best_verification_flow_target(
            page,
            scope,
            ["接收短信验证码", "发送短信验证"],
        )
        if not sms_target:
            return False

        try:
            await sms_target.scroll_into_view_if_needed()
            await sms_target.click(force=True, timeout=15000)
            state.touch(status="awaiting_sms_request", message="已进入短信验证码验证，正在打开发码页面…")
            await asyncio.sleep(1.0)
            return True
        except Exception as exc:
            logger.warning("Failed to enter SMS verification flow: %s", exc)
            return False

    async def _refresh_expired_qr_if_needed(self, page, state):
        refresh_target = await self._find_best_visible_text_target(
            page,
            page,
            ["点击刷新", "刷新", "刷新二维码"],
        )
        expired_target = await self._find_best_visible_text_target(
            page,
            page,
            ["二维码失效", "二维码已失效"],
        )
        if not refresh_target and not expired_target:
            return False

        target = refresh_target or expired_target
        try:
            await target.scroll_into_view_if_needed()
            await target.click(force=True, timeout=10000)
            state.touch(message="QR code expired and was refreshed automatically")
            await asyncio.sleep(1.5)
            return True
        except Exception as exc:
            logger.warning("Failed to auto-refresh expired QR: %s", exc)
            return False


    def _track_background_task(self, task):

        self._background_tasks.add(task)

        task.add_done_callback(self._background_tasks.discard)

        return task



    async def _finish_cancelled_task(self, task, session_id):

        try:

            await asyncio.wait_for(asyncio.shield(task), timeout=5)

        except asyncio.TimeoutError:

            logger.warning("Login session %s did not stop in time; cancelling task", session_id)

            task.cancel()

            with contextlib.suppress(asyncio.CancelledError, Exception):

                await task

        except asyncio.CancelledError:

            raise

        except Exception as exc:

            logger.warning("Login session %s cleanup ended with error: %s", session_id, exc)



    async def _resolve_interaction_scope(self, page):

        modal_selectors = [

            ".pc-login-verification-modal",

            ".semi-modal-content",

            ".semi-modal",

            'div[role="dialog"]',

        ]

        for selector in modal_selectors:

            modal = page.locator(selector).first

            if await modal.count() > 0 and await modal.is_visible():

                logger.info("Operating within verification modal scope: %s", selector)

                return modal

        return page



    async def _find_first_visible_text_target(self, scope, texts):

        for text in texts:

            candidate = scope.get_by_text(text, exact=False).first

            if await candidate.count() > 0 and await candidate.is_visible():

                return candidate

        return None



    async def _find_first_visible_locator(self, scope, selectors):

        for selector in selectors:

            group = scope.locator(selector)

            count = await group.count()

            for index in range(count):

                candidate = group.nth(index)

                if await candidate.is_visible():

                    return candidate

        return None



    async def _find_visible_locators(self, scope, selectors):

        for selector in selectors:

            group = scope.locator(selector)

            count = await group.count()

            visible = []

            for index in range(count):

                candidate = group.nth(index)

                if await candidate.is_visible():

                    visible.append(candidate)

            if visible:

                return visible

        return []

    async def _visible_text_snapshot(self, scope, limit=20):
        snippets = []
        candidates = await scope.locator("body, div, span, button, label").all()
        for candidate in candidates[:200]:
            try:
                if not await candidate.is_visible():
                    continue
                text = (await candidate.inner_text()).strip()
                if not text:
                    continue
                if text not in snippets:
                    snippets.append(text[:80])
                if len(snippets) >= limit:
                    break
            except Exception:
                continue
        return snippets

    def _normalize_code_command(self, cmd):
        digits = "".join(ch for ch in str(cmd or "") if ch.isdigit())
        if 4 <= len(digits) <= 8:
            return digits
        return str(cmd or "").strip()

    async def _find_verification_code_input(self, page, scope=None):
        actual_scope = scope or await self._resolve_interaction_scope(page)
        return await self._find_best_visible_locator(
            page,
            actual_scope,
            [
                '.semi-input-number input',
                'input[placeholder*="验证码"]',
                'input[class*="code"]',
                'input[name*="code"]',
                'input[inputmode="numeric"]',
                'input[type="tel"]',
                'input[type="number"]',
                'input[type="text"]',
                'textarea',
                '[contenteditable="true"]',
                '[role="textbox"]',
            ],
        )

    async def _read_verification_code_value(self, page, scope=None):
        actual_scope = scope or await self._resolve_interaction_scope(page)
        digit_inputs = await self._find_visible_locators(
            actual_scope,
            [
                '.semi-input-number input',
                'input[type="number"]',
                'input[inputmode="numeric"]',
                'input[type="tel"]',
            ],
        )
        if len(digit_inputs) > 1:
            values = []
            for item in digit_inputs:
                try:
                    value = (await item.input_value()).strip()
                except Exception:
                    value = ""
                values.append(value)
            joined = "".join(values).strip()
            if joined:
                return joined

        target_input = await self._find_verification_code_input(page, actual_scope)
        if not target_input:
            return ""

        for reader in (
            lambda: target_input.input_value(),
            lambda: target_input.inner_text(),
            lambda: target_input.text_content(),
        ):
            try:
                value = (await reader() or "").strip()
                if value:
                    return value
            except Exception:
                continue
        return ""

    async def _click_action_button_dom_fallback(self, page, labels):
        return await page.evaluate(
            """(buttonLabels) => {
                const labels = buttonLabels.map((item) => String(item || "").trim()).filter(Boolean);
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
                };
                const candidates = Array.from(document.querySelectorAll("*"))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const text = (el.innerText || el.textContent || el.value || "").trim();
                        if (!text || !labels.some((label) => text === label || text.includes(label))) {
                            return null;
                        }
                        const rect = el.getBoundingClientRect();
                        return { el, text, rect };
                    })
                    .filter(Boolean)
                    .sort((left, right) => {
                        const topDiff = right.rect.top - left.rect.top;
                        if (Math.abs(topDiff) > 1) return topDiff;
                        return (right.rect.width * right.rect.height) - (left.rect.width * left.rect.height);
                    });
                const clickable = (el) => el.closest('button,[role="button"],input[type="button"],input[type="submit"],a,[class*="button"],[class*="btn"]') || el;
                if (!candidates.length) {
                    return { clicked: false, reason: "no-candidate" };
                }
                const target = clickable(candidates[0].el);
                target.click();
                target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                return { clicked: true, text: candidates[0].text };
            }""",
            labels,
        )

    async def _click_verify_button_if_ready(self, page, scope, state):
        code_value = "".join(ch for ch in await self._read_verification_code_value(page, scope) if ch.isdigit())
        if len(code_value) < 4:
            return False

        confirm_btn = await self._find_best_visible_action_target(
            page,
            scope,
            ["验证", "确定", "登录", "提交", "下一步"],
        )
        if not confirm_btn:
            return False

        try:
            await confirm_btn.scroll_into_view_if_needed()
            await confirm_btn.click(force=True, timeout=10000)
            with contextlib.suppress(Exception):
                await confirm_btn.evaluate(
                    """(el) => {
                        el.click();
                        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                    }""",
                )
            with contextlib.suppress(Exception):
                dom_click_result = await self._click_action_button_dom_fallback(page, ["验证", "确定", "登录", "提交", "下一步"])
                logger.info("DOM verify-click fallback result: %s", dom_click_result)
            with contextlib.suppress(Exception):
                await page.keyboard.press("Enter")
            state.touch(status="submitting_code", message=f"验证码 {code_value} 已提交，正在验证…")
            return True
        except Exception as exc:
            logger.warning("Failed to click verify button after code entry: %s", exc)
            return False

    async def _capture_screenshot_if_due(
        self,
        page,
        state,
        *,
        prefer_verification=False,
        force=False,
        min_interval_seconds=2.0,
        timing_state=None,
    ):
        timing_state = timing_state if timing_state is not None else {}
        now = asyncio.get_running_loop().time()
        last_capture_at = timing_state.get("last_capture_at", 0.0)
        if not force and now - last_capture_at < min_interval_seconds:
            return False

        await self._capture_login_screenshot(
            page,
            state.screenshot_path,
            prefer_verification=prefer_verification,
        )
        timing_state["last_capture_at"] = now
        state.mark_screenshot_updated()
        return True

    async def _submit_code_command(self, page, scope, cmd, state):
        digit_inputs = await self._find_visible_locators(
            scope,
            [
                '.semi-input-number input',
                'input[type="number"]',
                'input[inputmode="numeric"]',
                'input[type="tel"]',
            ],
        )
        if len(digit_inputs) >= len(cmd) and len(digit_inputs) > 1:
            for index, char in enumerate(cmd):
                await digit_inputs[index].focus()
                with contextlib.suppress(Exception):
                    await digit_inputs[index].fill("")
                await digit_inputs[index].type(char, delay=50)

            await asyncio.sleep(0.2)

            confirm_btn = await self._find_best_visible_action_target(
                page,
                scope,
                ["验证", "确定", "登录", "提交", "下一步"],
            )
            if confirm_btn:
                await confirm_btn.click(force=True)
                with contextlib.suppress(Exception):
                    await confirm_btn.click(force=True, timeout=3000)
            await page.keyboard.press("Enter")
            state.touch(message=f"Submitted code (digits): {cmd}")
            return

        target_input = await self._find_best_visible_locator(
            page,
            scope,
            [
                'input[placeholder*="验证码"]',
                'input[class*="code"]',
                'input[name*="code"]',
                'input[inputmode="numeric"]',
                'input[type="tel"]',
                'input[type="text"]',
                'textarea',
                '[contenteditable="true"]',
                '[role="textbox"]',
            ],
        )

        if target_input:
            logger.info("Target input found, typing code: %s", cmd)
            await target_input.click(force=True)
            with contextlib.suppress(Exception):
                await target_input.focus()
            with contextlib.suppress(Exception):
                await target_input.fill("")
            with contextlib.suppress(Exception):
                await target_input.press("Control+A")
            with contextlib.suppress(Exception):
                await target_input.press("Backspace")
            try:
                await target_input.press_sequentially(cmd, delay=100)
            except Exception:
                await page.keyboard.type(cmd, delay=100)
        else:
            logger.warning("No direct code input field found, falling back to keyboard typing")
            await page.keyboard.type(cmd, delay=100)

        dom_fallback = await page.evaluate(
            """(code) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
                };
                const fire = (el) => {
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                };
                const inputs = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]'))
                    .filter(isVisible);
                const digitInputs = inputs.filter((el) => {
                    const type = (el.getAttribute("type") || "").toLowerCase();
                    const inputMode = (el.getAttribute("inputmode") || "").toLowerCase();
                    return type === "number" || type === "tel" || inputMode === "numeric";
                });
                if (digitInputs.length >= code.length && digitInputs.length > 1) {
                    for (let i = 0; i < code.length; i += 1) {
                        const el = digitInputs[i];
                        if ("value" in el) {
                            el.value = code[i];
                        } else {
                            el.textContent = code[i];
                        }
                        fire(el);
                    }
                    return { mode: "digit-inputs", count: digitInputs.length };
                }
                const target = inputs[0];
                if (!target) {
                    return { mode: "none", count: 0 };
                }
                if ("value" in target) {
                    target.value = code;
                } else {
                    target.textContent = code;
                }
                fire(target);
                return { mode: "single-input", count: inputs.length };
            }""",
            cmd,
        )
        logger.info("DOM fallback result for code submit: %s", dom_fallback)

        await asyncio.sleep(0.2)

        confirm_btn = await self._find_best_visible_action_target(
            page,
            scope,
            ["验证", "确定", "登录", "提交", "下一步"],
        )
        if confirm_btn:
            await confirm_btn.scroll_into_view_if_needed()
            await confirm_btn.click(force=True)
            with contextlib.suppress(Exception):
                await confirm_btn.click(force=True, timeout=3000)
            with contextlib.suppress(Exception):
                dom_click_result = await self._click_action_button_dom_fallback(page, ["验证", "确定", "登录", "提交", "下一步"])
                logger.info("DOM confirm-click fallback result after code submit: %s", dom_click_result)
            await page.keyboard.press("Enter")
            state.touch(message=f"Submitted code: {cmd}")
            return

        await page.keyboard.press("Enter")
        state.touch(message=f"Submitted code (via Enter): {cmd}")



    async def _viewport_center(self, page):

        size = getattr(page, "viewport_size", None) or await page.evaluate(

            "() => ({ width: window.innerWidth, height: window.innerHeight })"

        )

        return size["width"] / 2, size["height"] / 2



    async def _pick_most_central_visible(self, page, candidates):

        if not candidates:

            return None

        center_x, center_y = await self._viewport_center(page)

        best_candidate = None

        best_distance = None

        for candidate in candidates:

            try:

                if not await candidate.is_visible():

                    continue

                box = await candidate.bounding_box()

                if not box:

                    continue

                candidate_center_x = box["x"] + box["width"] / 2

                candidate_center_y = box["y"] + box["height"] / 2

                distance = math.hypot(candidate_center_x - center_x, candidate_center_y - center_y)

                if best_distance is None or distance < best_distance:

                    best_candidate = candidate

                    best_distance = distance

            except Exception:

                continue

        return best_candidate



    async def _find_best_visible_locator(self, page, scope, selectors):

        search_scopes = [scope]

        if scope is not page:

            search_scopes.append(page)



        candidates = []

        for current_scope in search_scopes:

            for selector in selectors:

                group = current_scope.locator(selector)

                count = await group.count()

                for index in range(count):

                    candidates.append(group.nth(index))

        return await self._pick_most_central_visible(page, candidates)



    async def _find_best_visible_text_target(self, page, scope, texts):

        search_scopes = [scope]

        if scope is not page:

            search_scopes.append(page)



        candidates = []

        for current_scope in search_scopes:

            for text in texts:

                group = current_scope.get_by_text(text, exact=False)

                count = await group.count()

                for index in range(count):

                    candidates.append(group.nth(index))

        return await self._pick_most_central_visible(page, candidates)

    async def _find_best_visible_action_target(self, page, scope, texts):

        search_scopes = [scope]

        if scope is not page:

            search_scopes.append(page)

        button_selectors = [
            "button",
            '[role="button"]',
            'input[type="button"]',
            'input[type="submit"]',
            '[class*="button"]',
            '[class*="btn"]',
        ]

        candidates = []
        for current_scope in search_scopes:
            for selector in button_selectors:
                group = current_scope.locator(selector)
                count = await group.count()
                for index in range(count):
                    candidate = group.nth(index)
                    try:
                        if not await candidate.is_visible():
                            continue
                        text = (await candidate.inner_text()).strip()
                    except Exception:
                        text = ""

                    if not text:
                        try:
                            text = (await candidate.get_attribute("value") or "").strip()
                        except Exception:
                            text = ""

                    if text and any(keyword in text for keyword in texts):
                        candidates.append(candidate)

        return await self._pick_most_central_visible(page, candidates)

    async def _find_best_verification_flow_target(self, page, scope, texts):

        action_target = await self._find_best_visible_action_target(page, scope, texts)
        if action_target:
            return action_target

        selector_candidates = []
        search_scopes = [scope]
        if scope is not page:
            search_scopes.append(page)

        selectors = [
            '[class*="verify"]',
            '[class*="security"]',
            '[class*="option"]',
            '[class*="item"]',
            'li',
            'div',
        ]

        for current_scope in search_scopes:
            for selector in selectors:
                group = current_scope.locator(selector)
                count = await group.count()
                for index in range(count):
                    candidate = group.nth(index)
                    try:
                        if not await candidate.is_visible():
                            continue
                        text = (await candidate.inner_text()).strip()
                    except Exception:
                        continue
                    if text and any(keyword in text for keyword in texts):
                        selector_candidates.append(candidate)

        if selector_candidates:
            return await self._pick_most_central_visible(page, selector_candidates)

        return await self._find_best_visible_text_target(page, scope, texts)



    async def start(self, relogin_unique_id=None):
        async with self._lock:
            if self._state and self._state.status in {
                "starting",
                "awaiting_scan",
                "awaiting_sms_request",
                "awaiting_code",
                "submitting_code",
                "authenticated",
            }:
                return self.get_public_state()

            relogin_account = None
            if relogin_unique_id:
                relogin_account = self._find_account_by_unique_id(
                    get_userData(force_reload=True),
                    relogin_unique_id,
                )
                if not relogin_account:
                    raise RuntimeError("Account not found for relogin")

            session_id = secrets.token_urlsafe(12)
            screenshot_path = str(self._session_screenshot_path(session_id))
            self._state = LoginSessionState(
                session_id=session_id,
                status="starting",
                message=(
                    f"Creating remote relogin session for {relogin_account.get('username', 'unknown')}"
                    if relogin_account
                    else "Creating remote login session"
                ),
                screenshot_path=screenshot_path,
                relogin_unique_id=relogin_account.get("unique_id", "") if relogin_account else "",
                relogin_username=relogin_account.get("username", "") if relogin_account else "",
                default_targets=list(relogin_account.get("targets", [])) if relogin_account else [],
            )
            self._cancel_event = asyncio.Event()
            self._task = asyncio.create_task(self._run_login_flow(self._state, self._cancel_event))
            return self.get_public_state()


    async def cancel(self):

        async with self._lock:

            if not self._state:

                return None

            task = self._task

            cancel_event = self._cancel_event

            session_id = self._state.session_id



            if cancel_event:

                cancel_event.set()



            self._task = None

            self._cancel_event = None

            self._state.touch(status="cancelled", message="已放弃本轮登录，可重新扫码")



            if task and not task.done():

                self._track_background_task(asyncio.create_task(self._finish_cancelled_task(task, session_id)))

            return self.get_public_state()



    async def finalize(self, targets, display_name=None):
        async with self._lock:
            if not self._state or self._state.status != "authenticated":
                raise RuntimeError("No authenticated login session is ready to save")

            username = display_name.strip() if display_name else self._state.username
            final_targets = [target for target in targets if target] or list(self._state.default_targets)

            if self._state.relogin_unique_id:
                accounts = get_userData(force_reload=True)
                account = self._find_account_by_unique_id(accounts, self._state.relogin_unique_id)
                if account:
                    account["unique_id"] = self._state.unique_id
                    account["username"] = username
                    account["cookies"] = self._state.cookies
                    account["targets"] = final_targets
                    save_userData(accounts)
                    self._state.touch(status="saved", message=f"Updated account {account['username']}")
                    return account

            account = upsert_user_account(
                self._state.unique_id,
                username,
                self._state.cookies,
                final_targets,
            )
            self._state.touch(status="saved", message=f"Saved account {account['username']}")
            return account


    async def _run_login_flow(self, state, cancel_event):

        playwright = browser = context = page = None

        try:

            logger.info(f"Setting up login flow for session {state.session_id}")

            state.touch(status="starting", message="Opening Douyin Creator Center")

            playwright, browser = await get_browser(GUI=False)

            context = await browser.new_context(
                viewport={"width": 1600, "height": 1200},
                device_scale_factor=2,
            )
            page = await context.new_page()


            state.touch(status="starting", message="Opening Douyin Creator Center")

            await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)

            

            await asyncio.sleep(3)

            qr_selectors = [".login-mask", 'div[class*="qrcode"]', "canvas", ".login-img-code-wrapper"]
            qr_found = False

            for selector in qr_selectors:

                try:

                    await page.wait_for_selector(selector, timeout=5000)

                    qr_found = True

                    break

                except:

                    continue



            msg = "Scan the QR code with the Douyin app" if qr_found else "Opening login page (generating QR code...)"

            state.touch(status="awaiting_scan", message=msg)

            screenshot_timing = {}
            poll_interval = 0.5
            last_sms_flow_enter_at = 0.0

            with contextlib.suppress(Exception):
                await self._capture_screenshot_if_due(
                    page,
                    state,
                    prefer_verification=False,
                    force=True,
                    timing_state=screenshot_timing,
                )

            while not cancel_event.is_set():
                screenshot_force = False
                await self._refresh_expired_qr_if_needed(page, state)

                unique_id_locator = page.locator(XPATHS["unique_id"]).first
                name_locator = page.locator(XPATHS["name"]).first

                if await unique_id_locator.count() > 0 and await name_locator.count() > 0:

                    logger.info("Authentication elements found, finishing login...")

                    result = await collect_login_result(page, context, timeout_ms=5000)

                    state.unique_id = result["unique_id"]

                    state.username = result["username"]

                    state.cookies = result["cookies"]

                    state.touch(status="authenticated", message=f"Logged in as {state.username}")

                    await page.screenshot(path=state.screenshot_path, full_page=True, timeout=15000)
                    state.mark_screenshot_updated()

                    return

                is_verifying = await self._is_verification_step(page)

                if is_verifying:
                    scope = await self._resolve_interaction_scope(page)
                    is_selection_step = await self._is_verification_method_selection_step(page, scope)
                    now = asyncio.get_running_loop().time()
                    if is_selection_step and now - last_sms_flow_enter_at >= 2.0:
                        entered = await self._enter_sms_verification_flow(page, scope, state)
                        if entered:
                            last_sms_flow_enter_at = now
                            screenshot_force = True
                            with contextlib.suppress(Exception):
                                await self._capture_screenshot_if_due(
                                    page,
                                    state,
                                    prefer_verification=True,
                                    force=True,
                                    min_interval_seconds=0.0,
                                    timing_state=screenshot_timing,
                                )
                            await asyncio.sleep(0.5)
                            continue

                    code_input = await self._find_verification_code_input(page, scope)
                    if code_input:
                        if state.status not in {"awaiting_code", "submitting_code"}:
                            state.touch(status="awaiting_code", message="扫码成功，请输入收到的验证码登录")
                            screenshot_force = True
                    elif state.status not in {"awaiting_sms_request", "awaiting_code", "submitting_code"}:
                        state.touch(status="awaiting_sms_request", message="扫码成功，请先点击获取验证码")
                        screenshot_force = True
                elif state.status not in {"awaiting_scan", "authenticated"}:
                    state.touch(status="awaiting_scan", message="请先扫码登录，扫码后会进入验证码验证")
                    screenshot_force = True

                if "/creator-home/" in page.url and state.status != "authenticated":
                    state.touch(status="submitting_code", message="正在跳转登录结果页面…")

                if state.pending_command:

                    cmd = state.pending_command

                    state.pending_command = None

                    logger.info(f"Executing command for session {state.session_id}: {cmd}")

                    scope = page
                    try:

                        scope = await self._resolve_interaction_scope(page)
                        normalized_cmd = self._normalize_code_command(cmd)

                        if normalized_cmd.startswith("click:"):

                            text = normalized_cmd.split(":", 1)[1].strip()

                            logger.info(f"Trying to click text: {text}")

                            found_btn = await self._find_best_verification_flow_target(

                                page,

                                scope,

                                [text, "接收短信验证码", "获取验证码", "重新发送", "重发", "发送验证码"],

                            )

                            if found_btn:

                                await found_btn.scroll_into_view_if_needed()

                                await found_btn.click(timeout=15000, force=True)

                                if "接收短信验证码" in text:
                                    state.touch(status="awaiting_sms_request", message="已选择短信验证码验证，正在进入发码页面…")
                                elif any(keyword in text for keyword in ["验证码", "发送", "重发"]):
                                    state.touch(status="awaiting_code", message="验证码已请求，请输入收到的验证码")
                                else:
                                    state.touch(message=f"Clicked: {text}")
                                screenshot_force = True

                            else:

                                logger.warning(f"No button found with text: {text}")
                                snapshot = await self._visible_text_snapshot(scope)
                                state.touch(status="awaiting_sms_request", message=f"Button not found: {text} | visible={snapshot[:6]}")
                                screenshot_force = True

                        else:

                            await self._submit_code_command(page, scope, normalized_cmd, state)
                            state.touch(status="submitting_code", message="验证码已提交，正在完成登录…")
                            screenshot_force = True

                    except Exception as e:

                        logger.error(f"Command execution failed: {e}")

                        snapshot = await self._visible_text_snapshot(scope)

                        state.touch(message=f"Execution failed: {str(e)} | visible={snapshot[:6]}")
                        screenshot_force = True

                if is_verifying:
                    scope = await self._resolve_interaction_scope(page)
                    if state.status in {"awaiting_code", "submitting_code"}:
                        clicked_verify = await self._click_verify_button_if_ready(page, scope, state)
                        if clicked_verify:
                            screenshot_force = True

                try:

                    await self._capture_screenshot_if_due(
                        page,
                        state,
                        prefer_verification=bool(is_verifying),
                        force=screenshot_force,
                        min_interval_seconds=2.0,
                        timing_state=screenshot_timing,
                    )
                except Exception as e:
                    logger.warning(f"Screenshot attempt failed: {e}")

                await asyncio.sleep(poll_interval)



            state.touch(status="cancelled", message="Login session cancelled")

        except Exception as exc:

            logger.error(f"Login session flow failed: {exc}")

            traceback.print_exc()

            state.touch(status="error", message=str(exc))

        finally:

            logger.info("Closing browser session")

            if page:

                with contextlib.suppress(Exception):

                    await page.close()

            if context:

                with contextlib.suppress(Exception):

                    await context.close()

            if browser:

                with contextlib.suppress(Exception):

                    await browser.close()

            if playwright:

                with contextlib.suppress(Exception):

                    await playwright.stop()





login_session_manager = LoginSessionManager()

