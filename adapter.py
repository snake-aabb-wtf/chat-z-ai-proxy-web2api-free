import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Optional

from playwright.async_api import async_playwright

from tool_dsml import build_dsml_tool_prompt, has_dsml_content, parse_dsml_invoke, strip_dsml_tags


class ChatAdapter:
    """Playwright-based adapter for chat.z.ai with automatic captcha + x-signature."""

    def __init__(self, token: str, base_url: str = "https://chat.z.ai",
                 user_id: str = "fd1a59ff-1780-4403-904e-219c32ca0162",
                 dsml_enabled: bool = True):
        self.token = token
        self.base_url = base_url
        self.user_id = user_id
        self.dsml_enabled = dsml_enabled
        self.dsml_ready = False
        self._playwright = None
        self._browser = None
        self._page = None
        self._chat_id = None

    async def start(self):
        p = async_playwright()
        self._playwright = await p.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="zh-CN", timezone_id="Asia/Shanghai")
        self._page = await ctx.new_page()

        await self._page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        """)

        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await self._page.evaluate(
            "([t, u]) => { localStorage.setItem('token', t); localStorage.setItem('user_id', u); }",
            [self.token, self.user_id])
        await self._page.goto(self.base_url, wait_until="networkidle")
        await asyncio.sleep(3)

    async def _create_chat(self):
        body = json.dumps({"chat": {"id": "", "title": "", "models": ["GLM-5.1"],
            "params": {}, "history": {"messages": {}, "currentId": None},
            "tags": [], "flags": [], "features": [], "mcp_servers": [],
            "enable_thinking": True, "auto_web_search": False,
            "message_version": 1, "extra": {},
            "timestamp": int(time.time() * 1000), "type": "default"}}, ensure_ascii=False)
        cid = await self._page.evaluate(
            "async ([t, b]) => { const r = await fetch('https://chat.z.ai/api/v1/chats/new', "
            "{ method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+t}, body:b }); "
            "return (await r.json()).id; }", [self.token, body])
        self._chat_id = cid
        return cid

    def _parse_sse_body(self, body: str) -> tuple:
        """Parse SSE body into (thinking_text, answer_text)."""
        thinking = []
        answer = []
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
                if phase == "thinking":
                    thinking.append(content)
                else:
                    answer.append(content)
        return "".join(thinking), "".join(answer)

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
        result = list(messages)
        for i, m in enumerate(result):
            if m.get("role") == "system":
                result[i] = {**m, "content": m["content"] + "\n\n" + prompt}
                return result
        result.insert(0, {"role": "system", "content": prompt})
        return result

    def _build_response(self, thinking: str, answer: str):
        ts = int(time.time())
        message = {"role": "assistant", "content": answer}
        if thinking:
            message["reasoning_content"] = thinking
        return {"id": f"chatcmpl-{ts}", "object": "chat.completion", "created": ts,
                "model": "gpt-4o",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def convert_with_dsml(self, full_text: str) -> dict:
        base = self._build_response("", "")
        if not has_dsml_content(full_text):
            base["choices"][0]["message"]["content"] = full_text
            return base
        tool_calls = parse_dsml_invoke(full_text)
        base["choices"][0]["message"]["content"] = strip_dsml_tags(full_text)
        if tool_calls:
            base["choices"][0]["finish_reason"] = "tool_calls"
            base["choices"][0]["message"]["tool_calls"] = tool_calls
        return base

    def convert_response(self, response: dict) -> dict:
        return response  # Already in OpenAI format from send_request

    async def send_request(self, payload: dict) -> dict:
        messages = payload["messages"]
        last_msg = messages[-1]["content"] if messages else ""
        if isinstance(last_msg, list):
            texts = [p.get("text", "") for p in last_msg if p.get("type") == "text"]
            last_msg = " ".join(texts)

        # Navigate to main page and click New Chat to trigger chat UI
        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # Click New Chat button to create a fresh chat and show input
        nc = await self._page.query_selector(
            "#sidebar-new-chat-button, a[href='/'], nav a[href='/'], button:has-text('New Chat')"
        )
        if nc:
            await nc.click()
            await asyncio.sleep(5)

        # Poll for input
        input_el = None
        for _ in range(30):
            input_el = await self._page.query_selector("#chat-input, textarea, [contenteditable='true']")
            if input_el:
                break
            await asyncio.sleep(1)
        if not input_el:
            return self._build_response("", f"Error: Chat input not found (url={self._page.url})")

        # Set up response capture
        api_result = {}
        async def on_chat_response(response):
            if "/api/v2/chat/completions" in response.url:
                api_result["status"] = response.status
                try:
                    api_result["body"] = await response.text()
                except:
                    pass
        self._page.on("response", on_chat_response)

        await input_el.click()
        await input_el.type(last_msg, delay=20)
        await asyncio.sleep(0.5)
        await self._page.keyboard.press("Enter")

        for _ in range(90):
            await asyncio.sleep(1)
            if api_result.get("body"):
                break

        self._page.remove_listener("response", on_chat_response)

        if not api_result.get("body"):
            return self._build_response("", "Error: No response received from chat.z.ai")

        thinking, answer = self._parse_sse_body(api_result["body"])
        content = answer or thinking  # fallback if no phase separation
        if self.dsml_enabled and self.dsml_ready and has_dsml_content(content):
            return self.convert_with_dsml(content)
        return self._build_response(thinking, answer)

    async def stream_request(self, payload: dict) -> AsyncGenerator[bytes, None]:
        messages = payload["messages"]
        last_msg = messages[-1]["content"] if messages else ""
        if isinstance(last_msg, list):
            texts = [p.get("text", "") for p in last_msg if p.get("type") == "text"]
            last_msg = " ".join(texts)

        if not self._chat_id:
            await self._create_chat()

        await self._page.goto(f"{self.base_url}/c/{self._chat_id}", wait_until="networkidle")
        await asyncio.sleep(5)

        input_el = await self._page.query_selector("#chat-input, textarea, [contenteditable='true']")
        if not input_el:
            yield b"data: [DONE]\n\n"
            return

        # For streaming, we proxy the SSE content line by line
        api_result = {}
        async def on_chat_response(response):
            if "/api/v2/chat/completions" in response.url:
                api_result["status"] = response.status
                try:
                    api_result["body"] = await response.text()
                except:
                    pass
        self._page.on("response", on_chat_response)

        await input_el.click()
        await input_el.type(last_msg, delay=20)
        await asyncio.sleep(0.5)
        await self._page.keyboard.press("Enter")

        for _ in range(90):
            await asyncio.sleep(1)
            if api_result.get("body"):
                break

        self._page.remove_listener("response", on_chat_response)

        body = api_result.get("body", "")
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
            except json.JSONDecodeError:
                yield (line + "\n").encode()
                continue
            inner = data.get("data")
            if isinstance(inner, dict):
                content = inner.get("delta_content") or inner.get("content")
                if content:
                    phase = inner.get("phase", "answering")
                    delta = {}
                    if phase == "thinking":
                        delta["reasoning_content"] = content
                    else:
                        delta["content"] = content
                    chunk = {"choices": [{"delta": delta, "index": 0}]}
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

        yield b"data: [DONE]\n\n"

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._page:
            await self._page.close()
