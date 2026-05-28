"""Browser-based proxy for chat.z.ai - fixed version."""
import asyncio, json, time, uuid
from playwright.async_api import async_playwright

TOKEN = "YOUR_TOKEN_HERE"
CAPTCHA = "YOUR_CAPTCHA_HERE"

class BrowserBackend:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._chat_id = None
        self._ready = False

    async def start(self):
        self._p = async_playwright()
        self._playwright = await self._p.__aenter__()
        self._browser = await self._playwright.chromium.launch(headless=True)
        ctx = await self._browser.new_context(viewport={"width": 1366, "height": 768})
        self._page = await ctx.new_page()
        await self._page.goto("https://chat.z.ai", wait_until="domcontentloaded")
        await self._page.evaluate("""(t) => {
            localStorage.setItem("token", t);
            localStorage.setItem("user_id", "fd1a59ff-1780-4403-904e-219c32ca0162");
        }""", TOKEN)
        await self._page.goto("https://chat.z.ai", wait_until="networkidle")
        await asyncio.sleep(5)
        self._ready = True

    async def create_chat(self, title="New Chat"):
        chat_body = {
            "chat": {
                "id": "", "title": title[:30],
                "models": ["GLM-5.1"], "params": {},
                "history": {"messages": {}, "currentId": None},
                "tags": [], "flags": [],
                "features": [], "mcp_servers": [],
                "enable_thinking": True,
                "auto_web_search": False,
                "message_version": 1,
                "extra": {},
                "timestamp": int(time.time() * 1000),
                "type": "default"
            }
        }
        result = await self._page.evaluate("""async ([token, bodyStr]) => {
            const body = JSON.parse(bodyStr);
            const resp = await fetch("https://chat.z.ai/api/v1/chats/new", {
                method: "POST",
                headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            return data.id || (data.chat && data.chat.id);
        }""", [TOKEN, json.dumps(chat_body, ensure_ascii=False)])
        self._chat_id = result
        return result

    async def chat_completion(self, messages, stream=False, model="GLM-5.1"):
        last_msg = messages[-1]["content"] if messages else ""
        if isinstance(last_msg, list):
            texts = [p.get("text", "") for p in last_msg if p.get("type") == "text"]
            last_msg = " ".join(texts)

        if not self._chat_id:
            await self.create_chat(last_msg[:30])

        # Build variables dict
        t = time.localtime()
        weekdays = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
        variables = {
            "{{USER_NAME}}": "", "{{USER_LOCATION}}": "Unknown",
            "{{CURRENT_DATETIME}}": time.strftime("%Y-%m-%d %H:%M:%S", t),
            "{{CURRENT_DATE}}": time.strftime("%Y-%m-%d", t),
            "{{CURRENT_TIME}}": time.strftime("%H:%M:%S", t),
            "{{CURRENT_WEEKDAY}}": weekdays[t.tm_wday],
            "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
            "{{USER_LANGUAGE}}": "zh-CN",
        }

        # Try to get captcha from page context first
        captcha_from_page = await self._page.evaluate("""() => {
            // Look for captcha_verify_param in the session
            // It might be stored in a variable or computed on demand
            return window.__captcha_param || localStorage.getItem("captcha_param") || "";
        }""")
        captcha = captcha_from_page or CAPTCHA

        payload = {
            "token": TOKEN,
            "chatId": self._chat_id,
            "message": last_msg,
            "captcha": captcha,
            "messages": messages,
            "model": model,
            "stream": stream,
            "variables": variables,
        }

        script = """
async ([p]) => {
    const token = p.token;
    const chatId = p.chatId;
    const message = p.message;
    const captcha = p.captcha;

    const timestamp = Date.now();
    const requestId = crypto.randomUUID();
    const userId = "fd1a59ff-1780-4403-904e-219c32ca0162";

    const coreParams = {timestamp: String(timestamp), requestId, user_id: userId};
    const sortedPayload = Object.entries(coreParams)
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([k, v]) => k + ":" + v)
        .join(",");

    const encoder = new TextEncoder();
    const encoded = encoder.encode(message);
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
        version: "0.0.1", platform: "web", token: token,
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
        signature_prompt: message,
        params: {}, extra: {},
        features: {image_generation: false, web_search: false, auto_web_search: false,
            preview_mode: true, flags: [], enable_thinking: true},
        variables: p.variables,
        chat_id: chatId,
        id: crypto.randomUUID(),
        current_user_message_id: crypto.randomUUID(),
        current_user_message_parent_id: null,
        background_tasks: {title_generation: true, tags_generation: true},
        captcha_verify_param: captcha,
    };

    try {
        const resp = await fetch("https://chat.z.ai/api/v2/chat/completions?" + qs, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
                "Origin": "https://chat.z.ai",
                "x-fe-version": "prod-fe-1.1.37",
                "x-region": "overseas",
                "x-signature": signature,
            },
            body: JSON.stringify(body)
        });
        const text = await resp.text();
        return {status: resp.status, body: text.substring(0, 5000)};
    } catch(e) {
        return {error: e.toString()};
    }
}
"""
        return await self._page.evaluate(script, [payload])

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._p:
            await self._p.__aexit__(None, None, None)


async def main():
    backend = BrowserBackend()
    await backend.start()
    print("Browser started!")

    chat_id = await backend.create_chat("Test")
    print(f"Chat: {chat_id}")

    print("\n=== Non-streaming ===")
    result = await backend.chat_completion(
        [{"role": "user", "content": "Say hello in Chinese"}], stream=False
    )
    print(f"Status: {result.get('status')}")
    body = result.get("body", "")
    if result.get("status") == 200:
        print(f"OK! Len: {len(body)}, preview: {body[:300]}")
    else:
        print(f"Response: {str(result)[:300]}")

    await backend.close()

asyncio.run(main())
