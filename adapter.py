import asyncio
import json
import time
import uuid
import base64
from typing import AsyncGenerator, Optional

from playwright.async_api import async_playwright

from tool_dsml import build_dsml_tool_prompt, has_dsml_content, parse_dsml_invoke, strip_dsml_tags


class ChatAdapter:
    """Playwright-based adapter that sends messages via page UI (captcha + signature handled by page)."""

    def __init__(self, token: str, base_url: str = "https://chat.z.ai",
                 dsml_enabled: bool = True):
        self.token = token
        self.base_url = base_url
        self.dsml_enabled = dsml_enabled
        self.dsml_ready = False
        self._playwright = None
        self._browser = None
        self._page = None

    async def start(self):
        p = async_playwright()
        self._playwright = await p.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=True, channel="chrome",
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
        self._page = await ctx.new_page()

        await self._page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        """)

        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await self._page.evaluate(
            "([t]) => { localStorage.setItem('token', t); }",
            [self.token])
        await self._page.goto(self.base_url, wait_until="networkidle")
        await asyncio.sleep(3)

    async def _send_via_ui(self, message: str, model: str = "GLM-5.1") -> dict:
        """Type a message in the chat input and press Enter. The page handles everything."""
        # Navigate to main page and click New Chat
        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        nc = await self._page.query_selector("#sidebar-new-chat-button")
        if nc:
            await nc.click()
            await asyncio.sleep(5)

        # Find input
        ie = None
        for _ in range(30):
            ie = await self._page.query_selector("#chat-input, textarea, [contenteditable='true']")
            if ie:
                break
            await asyncio.sleep(1)
        if not ie:
            return {"error": "Chat input not found"}

        # Capture the response - wait for one with actual content
        api_result = {}
        async def on_resp(response):
            if "/api/v2/chat/completions" in response.url:
                try:
                    body = await response.text()
                    # Only capture responses that have delta_content (real AI content)
                    if len(body) > 300 or "delta_content" in body:
                        api_result["body"] = body
                        api_result["status"] = response.status
                except:
                    pass

        self._page.on("response", on_resp)

        # Type and send
        await ie.click()
        await ie.type(message, delay=30)
        await asyncio.sleep(0.5)
        await self._page.keyboard.press("Enter")

        # Wait for response with content
        for _ in range(90):
            await asyncio.sleep(1)
            if api_result.get("body"):
                break

        self._page.remove_listener("response", on_resp)
        return api_result

    def _parse_sse_body(self, body: str) -> tuple:
        thinking, answer = [], []
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if raw in ("[DONE]",):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            inner = data.get("data")
            if isinstance(inner, dict):
                content = inner.get("delta_content") or inner.get("content") or ""
                if not content:
                    continue
                phase = inner.get("phase", "answering")
                (thinking if phase == "thinking" else answer).append(content)
        return "".join(thinking), "".join(answer)

    def _build_response(self, thinking: str, answer: str):
        ts = int(time.time())
        msg = {"role": "assistant", "content": answer}
        if thinking:
            msg["reasoning_content"] = thinking
        return {"id": f"chatcmpl-{ts}", "object": "chat.completion", "created": ts,
                "model": "gpt-4o",
                "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def _build_chunk(self, delta: dict) -> bytes:
        return f"data: {json.dumps({'choices': [{'delta': delta, 'index': 0}]}, ensure_ascii=False)}\n\n".encode()

    def convert_request(self, messages, stream=False, tools=None, tool_choice=None, **kwargs):
        if tools:
            messages = self._inject_dsml_prompt(messages, tools, tool_choice)
        return {"_internal": True, "messages": messages, "stream": stream, "tool_choice": tool_choice}

    def _inject_dsml_prompt(self, messages, tools, tool_choice=None):
        if not self.dsml_enabled or not self.dsml_ready:
            return messages
        if tool_choice == "none":
            return messages
        prompt = build_dsml_tool_prompt(tools, tool_choice)
        if not prompt:
            return messages
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                messages[i] = {**m, "content": m["content"] + "\n\n" + prompt}
                return messages
        return [{"role": "system", "content": prompt}] + messages

    def convert_with_dsml(self, full_text: str) -> dict:
        base = self._build_response("", "")
        if not has_dsml_content(full_text):
            base["choices"][0]["message"]["content"] = full_text
            return base
        tc = parse_dsml_invoke(full_text)
        base["choices"][0]["message"]["content"] = strip_dsml_tags(full_text)
        if tc:
            base["choices"][0]["finish_reason"] = "tool_calls"
            base["choices"][0]["message"]["tool_calls"] = tc
        return base

    def convert_response(self, response: dict) -> dict:
        return response

    async def send_request(self, payload: dict) -> dict:
        msg = payload["messages"][-1]["content"] if payload["messages"] else ""
        if isinstance(msg, list):
            msg = " ".join(p.get("text", "") for p in msg if p.get("type") == "text")

        result = await self._send_via_ui(msg)
        if "error" in result:
            return self._build_response("", result["error"])
        if not result.get("body"):
            return self._build_response("", "No response received")

        thinking, answer = self._parse_sse_body(result["body"])
        if self.dsml_enabled and self.dsml_ready and has_dsml_content(answer or thinking):
            return self.convert_with_dsml(answer or thinking)
        return self._build_response(thinking, answer)

    async def stream_request(self, payload: dict) -> AsyncGenerator[bytes, None]:
        msg = payload["messages"][-1]["content"] if payload["messages"] else ""
        if isinstance(msg, list):
            msg = " ".join(p.get("text", "") for p in msg if p.get("type") == "text")

        result = await self._send_via_ui(msg)
        if "error" in result:
            yield self._build_chunk({"content": result["error"]})
            yield b"data: [DONE]\n\n"
            return

        body = result.get("body", "")
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if raw in ("[DONE]",):
                yield b"data: [DONE]\n\n"
                return
            try:
                data = json.loads(raw)
            except:
                continue
            inner = data.get("data")
            if isinstance(inner, dict):
                content = inner.get("delta_content") or inner.get("content")
                if content:
                    phase = inner.get("phase", "answering")
                    key = "reasoning_content" if phase == "thinking" else "content"
                    yield self._build_chunk({key: content})
        yield b"data: [DONE]\n\n"

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._page:
            await self._page.close()
