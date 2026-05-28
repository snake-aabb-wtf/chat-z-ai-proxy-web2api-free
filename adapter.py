import asyncio
import json
import time
import uuid
import base64
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
        self._captcha_param = ""

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
            "([t, u]) => { localStorage.setItem('token', t); localStorage.setItem('user_id', u); }",
            [self.token, self.user_id])
        await self._page.goto(self.base_url, wait_until="networkidle")
        await asyncio.sleep(3)

    async def refresh_captcha(self):
        """Trigger captcha via page interaction, capture result from API."""
        self._captcha_param = ""

        captcha_result = {}
        async def on_captcha_resp(response):
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
            except:
                pass

        self._page.on("response", on_captcha_resp)

        # Go to main page and click New Chat to trigger captcha
        await self._page.goto(self.base_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        nc = await self._page.query_selector("#sidebar-new-chat-button")
        if nc:
            await nc.click()
            await asyncio.sleep(4)

        ie = await self._page.query_selector("#chat-input")
        if ie:
            await ie.click()
            await ie.type("x", delay=10)
            await asyncio.sleep(0.3)
            await self._page.keyboard.press("Enter")

        for _ in range(30):
            await asyncio.sleep(1)
            if captcha_result.get("certify_id") and captcha_result.get("security_token"):
                break

        self._page.remove_listener("response", on_captcha_resp)

        if captcha_result.get("certify_id") and captcha_result.get("security_token"):
            pd = {"certifyId": captcha_result["certify_id"], "sceneId": "didk33e0",
                  "isSign": True, "securityToken": captcha_result["security_token"]}
            pj = json.dumps(pd, separators=(",", ":"), ensure_ascii=False)
            self._captcha_param = base64.b64encode(pj.encode()).decode()
            return True
        return False

    async def _compute_and_send(self, messages, stream, model):
        """Compute x-signature and send request via page JS, with captcha."""
        last_msg = messages[-1]["content"] if messages else ""
        if isinstance(last_msg, list):
            texts = [p.get("text", "") for p in last_msg if p.get("type") == "text"]
            last_msg = " ".join(texts)

        t = time.localtime()
        weekdays = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        variables = {
            "{{USER_NAME}}": "", "{{USER_LOCATION}}": "Unknown",
            "{{CURRENT_DATETIME}}": time.strftime("%Y-%m-%d %H:%M:%S", t),
            "{{CURRENT_DATE}}": time.strftime("%Y-%m-%d", t),
            "{{CURRENT_TIME}}": time.strftime("%H:%M:%S", t),
            "{{CURRENT_WEEKDAY}}": weekdays[t.tm_wday],
            "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
            "{{USER_LANGUAGE}}": "zh-CN",
        }

        payload = {
            "token": self.token,
            "message": last_msg,
            "captcha": self._captcha_param,
            "messages": messages,
            "model": model,
            "stream": stream,
            "variables": variables,
        }

        script = """
async (p) => {
    const timestamp = Date.now();
    const requestId = crypto.randomUUID();

    const coreParams = {timestamp: String(timestamp), requestId, user_id: "fd1a59ff-1780-4403-904e-219c32ca0162"};
    const sortedPayload = Object.entries(coreParams)
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([k, v]) => k + ":" + v)
        .join(",");

    const encoder = new TextEncoder();
    const encoded = encoder.encode(p.message);
    let binaryStr = "";
    for (let i = 0; i < encoded.length; i += 32768) {
        binaryStr += String.fromCharCode(...Array.from(encoded.slice(i, i + 32768)));
    }
    const b64Prompt = btoa(binaryStr);
    const hmacMsg = sortedPayload + "|" + b64Prompt + "|" + timestamp;
    const key = Math.floor(timestamp / 300000);

    const keyBytes = new TextEncoder().encode(String(key));
    const msgBytes = new TextEncoder().encode(hmacMsg);
    const cryptoKey = await crypto.subtle.importKey("raw", keyBytes, {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
    const sigBytes = await crypto.subtle.sign("HMAC", cryptoKey, msgBytes);
    const signature = Array.from(new Uint8Array(sigBytes)).map(b => b.toString(16).padStart(2, "0")).join("");

    const allParams = {
        ...coreParams,
        version: "0.0.1", platform: "web", token: p.token,
        user_agent: navigator.userAgent,
        language: navigator.language, languages: navigator.languages.join(","),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        cookie_enabled: String(navigator.cookieEnabled),
        screen_width: String(screen.width), screen_height: String(screen.height),
        current_url: window.location.href, pathname: window.location.pathname,
        host: window.location.host, hostname: window.location.hostname,
        protocol: window.location.protocol, title: document.title,
        timezone_offset: String(-new Date().getTimezoneOffset()),
        browser_name: "Chrome", os_name: "Windows",
        is_mobile: "false", is_touch: "false", max_touch_points: "0",
        signature_timestamp: String(timestamp),
    };

    const sortedKeys = Object.keys(allParams).sort();
    const qs = sortedKeys.map(k => encodeURIComponent(k) + "=" + encodeURIComponent(allParams[k])).join("&");

    const body = {
        stream: p.stream,
        model: p.model,
        messages: p.messages,
        signature_prompt: p.message,
        params: {}, extra: {},
        features: {image_generation: false, web_search: false, auto_web_search: false,
            preview_mode: true, flags: [], enable_thinking: true},
        variables: p.variables,
        chat_id: crypto.randomUUID(),
        id: crypto.randomUUID(),
        current_user_message_id: crypto.randomUUID(),
        current_user_message_parent_id: null,
        background_tasks: {title_generation: true, tags_generation: true},
        captcha_verify_param: p.captcha,
    };

    try {
        const resp = await fetch("https://chat.z.ai/api/v2/chat/completions?" + qs, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + p.token,
                "Origin": "https://chat.z.ai",
                "x-fe-version": "prod-fe-1.1.37",
                "x-region": "overseas",
                "x-signature": signature,
            },
            body: JSON.stringify(body)
        });
        const text = await resp.text();
        return {status: resp.status, body: text};
    } catch(e) {
        return {error: e.toString()};
    }
}
"""
        return await self._page.evaluate(script, payload)

    def _parse_sse_body(self, body: str) -> tuple:
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

    def _build_response(self, thinking: str, answer: str):
        ts = int(time.time())
        message = {"role": "assistant", "content": answer}
        if thinking:
            message["reasoning_content"] = thinking
        return {"id": f"chatcmpl-{ts}", "object": "chat.completion", "created": ts,
                "model": "gpt-4o",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

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
        return response

    async def send_request(self, payload: dict) -> dict:
        if not self._captcha_param:
            ok = await self.refresh_captcha()
            if not ok:
                return self._build_response("", "Error: Failed to get captcha")

        resp = await self._compute_and_send(payload["messages"], stream=False, model="GLM-5.1")

        if resp.get("status") != 200:
            return self._build_response("", f"Error: {resp.get('body', str(resp))[:200]}")

        thinking, answer = self._parse_sse_body(resp["body"])
        if self.dsml_enabled and self.dsml_ready and has_dsml_content(answer):
            return self.convert_with_dsml(answer)
        return self._build_response(thinking, answer)

    async def stream_request(self, payload: dict) -> AsyncGenerator[bytes, None]:
        if not self._captcha_param:
            ok = await self.refresh_captcha()
            if not ok:
                yield b"data: [DONE]\n\n"
                return

        resp = await self._compute_and_send(payload["messages"], stream=True, model="GLM-5.1")
        if resp.get("status") != 200:
            yield f"data: {json.dumps({'error': resp.get('body', str(resp))[:200]})}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        for line in resp["body"].split("\n"):
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
                    yield f"data: {json.dumps({'choices': [{'delta': delta, 'index': 0}]}, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._page:
            await self._page.close()
