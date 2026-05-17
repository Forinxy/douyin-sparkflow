import asyncio
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
import uvicorn
from playwright.async_api import async_playwright

from core.login import collect_login_result


REMOTE_LOGIN_URL = "https://creator.douyin.com/"
PROFILE_DIR = Path("/data/login-profile")


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
                ],
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await self.page.goto(REMOTE_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

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
        await self.ensure_running()
        logged_in = False
        username = ""
        unique_id = ""
        page = await self._get_active_page()
        current_url = page.url if page else ""
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
    try:
        result = await manager.export()
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("LOGIN_DESKTOP_API_PORT", "18090")), reload=False)
