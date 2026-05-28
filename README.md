# chat.z.ai 反向代理 — web2api

将 [Z.ai](https://chat.z.ai)（智谱 GLM-5.1 免费版）转为 **OpenAI 兼容 API**，支持流式、多轮对话、工具调用（DSML）。  
无需逆向 x-signature 算法或手动处理验证码 — 使用 **Playwright 浏览器后端** 自动绕过所有防护。

---

## 原理

```
┌──────────────────────────────────────────────────────────┐
│                   你的应用 (OpenAI SDK)                     │
│   OPENAI_API_BASE=http://localhost:8000/v1                │
└────────────────────────┬─────────────────────────────────┘
                         │ POST /v1/chat/completions
                         ▼
┌──────────────────────────────────────────────────────────┐
│              web2api 代理 (FastAPI)                        │
│                                                          │
│   ┌──────────────────────────────────────────────────┐   │
│   │              Playwright 浏览器后端                  │   │
│   │                                                  │   │
│   │  1. 导航到聊天页面                                  │   │
│   │  2. 输入消息 → 按 Enter                            │   │
│   │  3. 页面自动处理：                                  │   │
│   │     ├─ 触发阿里云 TRACELESS 验证码（无感通过）        │   │
│   │     ├─ 计算 x-signature（HMAC-SHA256）              │   │
│   │     ├─ 携带验证码 + 签名 发往 /api/v2/chat/completions│   │
│   │  4. 捕获 SSE 响应 → 转 OpenAI 格式返回               │   │
│   └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  chat.z.ai 后端      │
              │  (智谱 GLM-5.1)     │
              └─────────────────────┘
```

## 特性

| 特性 | 支持 |
|------|------|
| 纯文本对话 | ✅ |
| 流式输出 (SSE → OpenAI chunk) | ✅ |
| 多轮对话 | ✅ |
| 阿里云无痕验证码 (TRACELESS) | ✅ 自动通过 |
| x-signature 签名 | ✅ 浏览器自动计算 |
| WAF 绕过 | ✅ 浏览器原生指纹 |
| 工具调用 (function calling) | ⚠️ DSML 提示词注入 |
| 多模态 (图片/文件) | ❌ |
| `max_tokens` / `temperature` | ⚠️ 取决于目标模型 |

## 前置要求

- Python 3.10+
- 一个有效的 chat.z.ai 账号
- 已安装 Playwright 浏览器（安装见下方）

## 安装

```powershell
# 1. 克隆/进入项目目录
cd D:\G\G1\反代AI研究\z.ai\chat-z-ai-proxy

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器
python -m playwright install chromium
```

## 快速开始

### 第一步：提取 Token

1. 在浏览器中打开 [chat.z.ai](https://chat.z.ai) 并登录
2. 按 `F12` 打开 DevTools → Network 面板
3. 发送一条消息
4. 在 Network 中找到 `POST /api/v2/chat/completions` 请求
5. 右键该请求 → **Save all as HAR with content**
6. 运行：

```powershell
python extract_env.py 你的文件.har .env
```

### 第二步：启动代理

```powershell
python server.py
```

输出示例：
```
chat.z.ai proxy on http://0.0.0.0:8000
Starting browser...
Ready!
```

### 第三步：调用

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-web2api-placeholder",
    base_url="http://localhost:8000/v1",
)

# 非流式
response = client.chat.completions.create(
    model="GLM-5.1",
    messages=[{"role": "user", "content": "用中文打招呼"}],
)
print(response.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="GLM-5.1",
    messages=[{"role": "user", "content": "数到5"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

或 cURL：

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-5.1",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

## 项目结构

```
chat-z-ai-proxy/
├── adapter.py          # 核心适配器（Playwright 浏览器后端）
├── server.py           # FastAPI 代理服务器
├── extract_env.py      # 从 HAR 提取 TOKEN
├── requirements.txt     # Python 依赖
├── .env                # 配置文件
├── tool_dsml.py        # DSML 工具调用支持
└── tool_sieve.py       # 流式内容分离引擎
```

## 验证码说明

本项目使用 **Playwright 浏览器** 作为请求后端。当用户发送消息时：

1. 适配器控制浏览器打开 [chat.z.ai](https://chat.z.ai) 的聊天页面
2. 在输入框键入消息并按 Enter
3. **页面自身的 JavaScript** 自动触发阿里云 TRACELESS 验证码
4. 阿里云通过设备指纹判断→**自动无感通过**
5. 页面同时计算 `x-signature`（HMAC-SHA256，5 分钟时间窗口 key）
6. 携带验证码 + 签名请求 `/api/v2/chat/completions`
7. 适配器捕获 SSE 响应，转换为 OpenAI 格式

**无需手动处理验证码或签名** — 浏览器代劳一切。

## x-signature 算法（已逆向）

仅供技术参考，适配器不直接使用此算法：

```
sortedPayload = "requestId:<uuid>,timestamp:<ts>,user_id:<user_id>"
b64prompt = base64(prompt)
msg = sortedPayload + "|" + b64prompt + "|" + timestamp
key = str(floor(timestamp / 300000))   # 5 分钟时间窗口
signature = HMAC-SHA256(msg, key)
```

## 已知限制

- **Token 有效期**：JWT token 有效期较长，但仍需定期通过导出 HAR 刷新
- **验证码绑定会话**：验证码与浏览器会话绑定，切换会话需重启代理
- **不支持多模态**：图片/文件/语音输入不可用
- **首请求延迟**：首次请求需启动浏览器 + 加载页面（约 10-15 秒）
- **DSML 工具调用**：基于提示词注入，非原生 function calling

## 常见问题

**Q: 启动报错 `Executable doesn't exist`**  
A: 运行 `python -m playwright install chromium`

**Q: 返回 INTERNAL_ERROR**  
A: 可能是 Token 过期，重新导出 HAR 并运行 `extract_env.py`

**Q: 首请求很慢**  
A: 浏览器首次启动需加载页面资源（约 10-15s），后续请求复用浏览器

**Q: 如何更换模型**  
A: 在 `.env` 中修改 `MODEL_NAME`，可选值见 `GET /api/models` 响应

## 许可

本项目仅供学习和研究使用。使用前请确保遵守 Z.ai 的服务条款。
