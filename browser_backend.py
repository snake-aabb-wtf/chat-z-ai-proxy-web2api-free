#!/usr/bin/env python3
"""
browser_backend.py — Playwright browser backend for chat.z.ai proxy.

This module provides the browser automation layer used by adapter.py.
It handles browser lifecycle, page navigation, and captcha interception.

Usage:
    from browser_backend import BrowserBackend
    backend = BrowserBackend(token="...")
    await backend.start()
"""

import asyncio
import json
import base64
from playwright.async_api import async_playwright


class BrowserBackend:
    """Manages a Playwright browser session for interacting with chat.z.ai."""

    def __init__(self, token: str, base_url: str = "https://chat.z.ai"):
        self.token = token
        self.base_url = base_url
        self._playwright = None
        self._browser = None
        self._page = None

    async def start(self):
        p = async_playwright()
        self._playwright = await p.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._page = await ctx.new_page()

        await self._page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        """)

        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await self._page.evaluate(
            "([t, u]) => { localStorage.setItem('token', t); localStorage.setItem('user_id', u); }",
            [self.token, "fd1a59ff-1780-4403-904e-219c32ca0162"],
        )
        await self._page.goto(self.base_url, wait_until="networkidle")
        await asyncio.sleep(3)

    async def refresh_captcha(self) -> str:
        """Trigger Aliyun TRACELESS captcha and return captcha_verify_param."""
        captcha_result = {}

        async def on_response(response):
            url = response.url
            if "captcha-open" not in url:
                return
            try:
                body = json.loads(await response.text())
                cid = body.get("CertifyId", "")
                if cid:
                    captcha_result["certify_id"] = cid
                if isinstance(body.get("Result"), dict):
                    st = body["Result"].get("securityToken", "")
                    if st:
                        captcha_result["security_token"] = st
            except Exception:
                pass

        self._page.on("response", on_response)

        # Click send to trigger captcha
        input_el = await self._page.query_selector("#chat-input, textarea, [contenteditable='true']")
        if input_el:
            await input_el.click()
            await input_el.fill("x")
            send_btn = await self._page.query_selector("button[type='submit']:not([disabled])")
            if send_btn:
                await send_btn.click()

        for _ in range(30):
            await asyncio.sleep(1)
            if captcha_result.get("certify_id") and captcha_result.get("security_token"):
                break

        self._page.remove_listener("response", on_response)

        if captcha_result.get("certify_id") and captcha_result.get("security_token"):
            param = {
                "certifyId": captcha_result["certify_id"],
                "sceneId": "didk33e0",
                "isSign": True,
                "securityToken": captcha_result["security_token"],
            }
            return base64.b64encode(
                json.dumps(param, separators=(",", ":"), ensure_ascii=False).encode()
            ).decode()
        return ""

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._page:
            await self._page.close()


if __name__ == "__main__":
    import sys
    token = sys.argv[1] if len(sys.argv) > 1 else input("Token: ")
    print(f"Starting browser with token: {token[:20]}...")

    async def main():
        bb = BrowserBackend(token=token)
        await bb.start()
        print("Browser started. Captcha:", await bb.refresh_captcha())
        await bb.close()

    asyncio.run(main())
