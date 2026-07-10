import asyncio
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn
from playwright.async_api import async_playwright

from core.login import collect_login_result


REMOTE_LOGIN_URL = "https://creator.douyin.com/"
WWW_SELF_URL = "https://www.douyin.com/user/self"
PROFILE_DIR = Path("/data/login-profile")
GENERIC_WWW_NAMES = {
    "",
    "我的",
    "我",
    "抖音官网账号",
    "精选",
    "推荐",
    "搜索",
    "关注",
    "朋友",
    "直播",
    "放映厅",
    "短剧",
    "小游戏",
    "客户端",
    "通知",
    "私信",
    "投稿",
    "海量优质视频内容",
    "抖音精选电脑版",
}


class LoginDesktopManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.playwright = None
        self.context = None
        self.page = None

    async def start(self):
        async with self._lock:
            if self.context and not self._context_is_closed():
                return
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1600, "height": 1000},
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--start-maximized",
                    "--disable-gpu",
                    "--disable-gpu-compositing",
                    "--disable-software-rasterizer",
                    "--disable-accelerated-2d-canvas",
                    "--disable-accelerated-video-decode",
                    "--renderer-process-limit=2",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
                ],
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    def _context_is_closed(self):
        return not self.context or getattr(self.context, "_impl_obj", None) is None

    async def _get_active_page(self):
        await self.ensure_running()
        try:
            if self.page and not self.page.is_closed():
                return self.page
        except Exception:
            pass
        try:
            for candidate in self.context.pages:
                if not candidate.is_closed():
                    self.page = candidate
                    return candidate
        except Exception:
            pass
        self.page = await self.context.new_page()
        return self.page

    async def stop(self, clear_profile=False):
        async with self._lock:
            if self.page:
                await self.page.close()
                self.page = None
            if self.context:
                await self.context.close()
                self.context = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            if clear_profile and PROFILE_DIR.exists():
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)

    async def reset(self):
        await self.stop(clear_profile=True)
        await self.start()

    async def ensure_running(self):
        if not self.context or self._context_is_closed():
            await self.start()

    async def status(self):
        logged_in = False
        username = ""
        unique_id = ""
        current_url = ""

        if not self.context or self._context_is_closed():
            return {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
            }

        page = None
        try:
            if self.page and not self.page.is_closed():
                page = self.page
            else:
                for candidate in self.context.pages:
                    if not candidate.is_closed():
                        self.page = candidate
                        page = candidate
                        break
        except Exception:
            self.page = None
            self.context = None
            return {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
            }

        if page:
            current_url = page.url
            try:
                result = await collect_login_result(page, self.context, timeout_ms=1000)
                logged_in = True
                username = result["username"]
                unique_id = result["unique_id"]
            except Exception:
                pass

        return {
            "running": True,
            "logged_in": logged_in,
            "username": username,
            "unique_id": unique_id,
            "current_url": current_url,
            "profile_dir": str(PROFILE_DIR),
        }

    async def open_login(self):
        try:
            page = await self._get_active_page()
            await page.goto(REMOTE_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await self.reset()
            page = await self._get_active_page()
            await page.goto(REMOTE_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

    async def export(self):
        page = await self._get_active_page()
        result = await collect_login_result(page, self.context, timeout_ms=5000)
        return result


def _clean_www_display_name(value):
    raw = str(value or "").replace("\u200b", "").replace("\ufeff", "")
    name = " ".join(raw.split()).strip(" -_｜|·•")
    if not name or name in GENERIC_WWW_NAMES:
        return ""
    if len(name) > 40:
        return ""
    if any(token in name for token in ("登录", "注册", "关注", "粉丝", "获赞", "作品", "喜欢", "收藏", "观看历史", "海量优质视频", "抖音旗下")):
        return ""
    return name


async def collect_www_identity_from_page(page):
    return await page.evaluate(
        r"""() => {
            const normalize = (value) => String(value || "")
                .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
                .replace(/\s+/g, " ")
                .trim();
            const bad = new Set(["", "我的", "我", "抖音官网账号", "精选", "推荐", "搜索", "关注", "朋友", "直播", "放映厅", "短剧", "小游戏", "客户端", "通知", "私信", "投稿"]);
            const candidates = [];
            const add = (value, source) => {
                const text = normalize(value).replace(/^@+/, "").trim();
                if (!text || bad.has(text) || text.length > 40) return;
                if (/登录|注册|关注|粉丝|获赞|作品|喜欢|收藏|观看历史/.test(text)) return;
                candidates.push({ text, source });
            };

            add(document.querySelector('[data-e2e="user-title"]')?.innerText, "data-e2e=user-title");
            add(document.querySelector('[class*="userName"], [class*="UserName"], [class*="nickname"], [class*="Nickname"], h1')?.innerText, "profile-name-selector");
            add(document.title.split(/[｜|\-]/)[0], "document-title");
            add(document.querySelector('meta[property="og:title"]')?.content?.split(/[｜|\-]/)[0], "og:title");

            const selfLink = document.querySelector('a[href*="/user/self"]');
            if (selfLink) {
                const root = selfLink.closest('div')?.parentElement?.parentElement || selfLink;
                const lines = normalize(root.innerText).split(/关注|粉丝|获赞|我的喜欢|我的收藏|观看历史|稍后再看|我的作品|我的预约|我的订单|退出登录/);
                add(lines[0], "self-link-root");
            }

            if (location.pathname.includes('/user/')) {
                add(document.querySelector('meta[name="description"]')?.content?.split(/[，,。|｜-]/)[0], "description");
            }

            return {
                url: location.href,
                title: document.title,
                profileHref: selfLink?.href || "",
                candidates,
            };
        }"""
    )


async def collect_www_login_result(page, context):
    cookies = await context.cookies()
    try:
        await page.goto(WWW_SELF_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    identity = await collect_www_identity_from_page(page)
    username = ""
    for item in identity.get("candidates") or []:
        username = _clean_www_display_name(item.get("text"))
        if username:
            break

    if not username:
        identity = await collect_www_identity_from_page(page)
        for item in identity.get("candidates") or []:
            username = _clean_www_display_name(item.get("text"))
            if username:
                break

    if not username:
        username = "抖音官网账号"
    uid_cookie = ""
    for cookie in cookies:
        if cookie.get("name") in {"uid_tt", "uid_tt_ss", "sid_uid", "passport_csrf_token"}:
            uid_cookie = str(cookie.get("value") or "")
            if uid_cookie:
                break
    suffix = "".join(ch for ch in uid_cookie if ch.isalnum())[:24]
    unique_id = f"web-self-{suffix or username}"
    return {
        "unique_id": unique_id,
        "username": username,
        "cookies": cookies,
    }


manager = LoginDesktopManager()
app = FastAPI(title="Douyin Login Desktop")


@app.on_event("startup")
async def startup():
    await manager.start()


@app.on_event("shutdown")
async def shutdown():
    await manager.stop(clear_profile=False)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/status")
async def status():
    return await manager.status()


@app.post("/open-login")
async def open_login():
    await manager.open_login()
    return {"ok": True}


@app.post("/reset")
async def reset():
    await manager.reset()
    return {"ok": True}


@app.post("/export")
async def export():
    page = await manager._get_active_page()
    try:
        result = await manager.export()
    except Exception as creator_exc:
        try:
            result = await collect_www_login_result(page, manager.context)
        except Exception as www_exc:
            raise HTTPException(status_code=400, detail=f"creator export failed: {creator_exc}; www export failed: {www_exc}")
    return {"ok": True, "result": result}


@app.get("/debug/screenshot")
async def debug_screenshot():
    page = await manager._get_active_page()
    data = await page.screenshot(full_page=False, type="png")
    return Response(content=data, media_type="image/png")


@app.get("/debug/snapshot")
async def debug_snapshot():
    page = await manager._get_active_page()
    items = await page.evaluate(
        r"""() => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            };
            const textOf = (el) => String(el.innerText || el.textContent || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim();
            const nodes = [...document.querySelectorAll('button, a, [role="button"], [aria-label], input, textarea, [contenteditable="true"], [class*="message"], [class*="chat"], [class*="im"]')];
            return nodes.filter(visible).slice(0, 300).map((el, i) => {
                const r = el.getBoundingClientRect();
                return {
                    i,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria: el.getAttribute('aria-label') || '',
                    title: el.title || '',
                    text: textOf(el).slice(0, 120),
                    cls: String(el.className || '').slice(0, 120),
                    contenteditable: el.getAttribute('contenteditable') || '',
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
                };
            });
        }"""
    )
    return {"url": page.url, "title": await page.title(), "items": items}


@app.post("/debug/action")
async def debug_action(request: Request):
    page = await manager._get_active_page()
    payload = await request.json()
    action = payload.get("action")
    if action == "click_text":
        text = str(payload.get("text") or "")
        exact = bool(payload.get("exact", False))
        await page.get_by_text(text, exact=exact).first.click(timeout=int(payload.get("timeout", 5000)))
    elif action == "click_at":
        await page.mouse.click(float(payload["x"]), float(payload["y"]))
    elif action == "wheel":
        await page.mouse.wheel(float(payload.get("dx", 0)), float(payload.get("dy", 0)))
    elif action == "type":
        await page.keyboard.type(str(payload.get("text") or ""), delay=int(payload.get("delay", 20)))
    elif action == "press":
        await page.keyboard.press(str(payload.get("key") or "Enter"))
    elif action == "goto":
        await page.goto(str(payload.get("url") or REMOTE_LOGIN_URL), wait_until="commit", timeout=15000)
    elif action == "eval":
        result = await page.evaluate(str(payload.get("script") or "undefined"))
        return {"ok": True, "result": result, "url": page.url}
    elif action == "list_frames":
        frames = []
        for fr in page.frames:
            frames.append({"url": fr.url, "name": fr.name})
        return {"ok": True, "frames": frames, "url": page.url}
    elif action == "eval_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        script = str(payload.get("script") or "undefined")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found", "url": page.url}
        result = await target_frame.evaluate(script)
        return {"ok": True, "result": result, "url": page.url, "frame_url": target_frame.url}
    elif action == "click_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found"}
        await target_frame.click(selector, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url, "frame_url": target_frame.url}
    elif action == "type_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        text = str(payload.get("text") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": "no frame found"}
        await target_frame.fill(selector, text, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url}
    else:
        raise HTTPException(status_code=400, detail=f"unknown action {action!r}")
    return {"ok": True, "url": page.url}


# ---- Network capture (codex-added observability) ----
_net_log = []
_net_capturing = False
_net_max = 500

@app.post("/debug/net_capture")
async def net_capture(request: Request):
    global _net_capturing
    payload = await request.json()
    action_type = payload.get("type", "start")
    page = await manager._get_active_page()
    if action_type == "start":
        _net_log.clear()
        _net_capturing = True

        async def on_request(req):
            if not _net_capturing:
                return
            if len(_net_log) >= _net_max:
                return
            try:
                _net_log.append({
                    "ts": __import__("time").time(),
                    "phase": "request",
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                })
            except Exception:
                pass

        async def on_response(resp):
            if not _net_capturing:
                return
            if len(_net_log) >= _net_max:
                return
            try:
                body_preview = None
                try:
                    body_preview = (await resp.text())[:300]
                except Exception:
                    pass
                _net_log.append({
                    "ts": __import__("time").time(),
                    "phase": "response",
                    "url": resp.url,
                    "status": resp.status,
                    "headers": dict(resp.headers),
                    "body": body_preview,
                })
            except Exception:
                pass

        page.on("request", lambda req: __import__("asyncio").ensure_future(on_request(req)))
        page.on("response", lambda resp: __import__("asyncio").ensure_future(on_response(resp)))
        return {"ok": True, "msg": "capture started"}
    elif action_type == "stop":
        _net_capturing = False
        return {"ok": True, "count": len(_net_log)}
    elif action_type == "get":
        return {"ok": True, "count": len(_net_log), "log": _net_log.copy()}
    elif action_type == "clear":
        _net_log.clear()
        return {"ok": True}
    return {"ok": False, "error": "unknown type"}

@app.get("/debug/net_log")
async def get_net_log():
    return {"count": len(_net_log), "capturing": _net_capturing, "log": _net_log.copy()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("LOGIN_DESKTOP_API_PORT", "18090")), reload=False)
