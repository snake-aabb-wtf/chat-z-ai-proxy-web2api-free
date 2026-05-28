# chat.z.ai 反向代理 — web2api

将 [Z.ai](https://chat.z.ai)（智谱 GLM-5.1 免费版）转为 **OpenAI 兼容 API**。  
使用 Playwright 浏览器后端，自动绕过 WAF、验证码、x-signature，无需手动处理任何反爬机制。

---

## 目录

- [原理](#原理)
- [特性](#特性)
- [快速开始](#快速开始)
- [接入指南](#接入指南)
- [思考/回答分离](#思考回答分离)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [技术参考](#技术参考)

---

## 原理

```
┌─────────────────────────────────────────────┐
│         你的应用 (OpenAI SDK)                 │
│  OPENAI_API_BASE=http://localhost:8000/v1    │
└──────────────────┬──────────────────────────┘
                   │ POST /v1/chat/completions
                   ▼
┌─────────────────────────────────────────────┐
│           web2api 代理 (FastAPI)             │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │       Playwright 浏览器后端            │  │
│  │                                       │  │
│  │  1. 打开 chat.z.ai → 创建新会话       │  │
│  │  2. 在输入框键入消息 → 按 Enter        │  │
│  │  3. 页面自动处理：                     │  │
│  │     ├─ 阿里云 TRACELESS 验证码(无感)    │  │
│  │     ├─ 计算 x-signature (HMAC-SHA256)  │  │
│  │     ├─ 发送请求 → /api/v2/chat/...     │  │
│  │  4. 捕获 SSE 响应 → 解析 phases        │  │
│  │  5. 返回 OpenAI 格式                   │  │
│  └───────────────────────────────────────┘  │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
         ┌───────────────────┐
         │  chat.z.ai 后端   │
         │  (智谱 GLM-5.1)  │
         └───────────────────┘
```

**关键设计：** 本项目不逆向签名算法或验证码，而是让真实浏览器页面代为处理所有反爬机制。适配器只负责操控浏览器输入消息、捕获响应、转换格式。

---

## 特性

| 特性 | 支持 |
|------|------|
| 纯文本对话 | ✅ |
| 流式输出 (SSE → OpenAI chunk) | ✅ |
| 多轮对话 | ✅ |
| 思考/回答分离 | ✅ `reasoning_content` + `content` |
| 阿里云无痕验证码 (TRACELESS) | ✅ 浏览器自动无感通过 |
| x-signature 签名 | ✅ 浏览器自动计算 |
| WAF 绕过 | ✅ 系统 Chrome + 真实指纹 |
| 工具调用 (function calling) | ⚠️ DSML 提示词注入 |
| 多模态 (图片/文件) | ❌ |
| `max_tokens` / `temperature` | ⚠️ 取决于目标模型 |

---

## 快速开始

### 前置要求

- Windows 10/11（本工具在 Windows 上开发）
- Python 3.10+
- 已安装 **Google Chrome** 浏览器
- 一个有效的 chat.z.ai 账号（免费注册即可）

### 安装

```powershell
# 1. 克隆项目
git clone https://github.com/snake-aabb-wtf/chat-z-ai-proxy-web2api-free.git
cd chat-z-ai-proxy-web2api-free

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器支持
python -m playwright install chromium
```

### 配置

项目需要你的 **JWT Token** 来登录 chat.z.ai。按以下步骤获取：

<details>
<summary><b>📄 展开查看：如何提取 Token</b></summary>

1. 打开浏览器，登录 [chat.z.ai](https://chat.z.ai)
2. 按 `F12` 打开 DevTools
3. 切换到 **Network**（网络）面板
4. **勾选 "Preserve log"**（保留日志）
5. 在聊天输入框输入任意消息并发送
6. 在 Network 列表中找到 `POST /api/v2/chat/completions` 请求
7. 右键该请求 → **Copy → Copy as cURL**（或 Save all as HAR with content）

#### 方式 A：从 HAR 文件提取（推荐）

```powershell
# 将保存的 .har 文件放在项目目录
python extract_env.py 你的文件.har .env
```

#### 方式 B：手动设置

直接编辑 `.env` 文件：

```env
TOKEN=eyJhbGciOiJFUzI1NiIs...
HOST=0.0.0.0
PORT=8000
MODEL_NAME=GLM-5.1
DSML_ENABLED=true
```

Token 可从 cURL 命令的 `Authorization: Bearer <token>` 或 URL 参数中的 `token=` 提取。

</details>

### 启动

```powershell
# 方式 1：直接启动
python server.py

# 方式 2：一键启动脚本
start.bat
```

首次启动需加载浏览器，约 15-20 秒。输出示例：

```
chat.z.ai proxy on http://0.0.0.0:8000
Starting browser...
Ready!
```

---

## 接入指南

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-web2api-placeholder",
    base_url="http://localhost:8000/v1",
)

# 非流式 — 含 reasoning_content
response = client.chat.completions.create(
    model="GLM-5.1",
    messages=[{"role": "user", "content": "日本的 capital 是什么？"}],
)
msg = response.choices[0].message
print("思考:", msg.reasoning_content)
print("回答:", msg.content)

# 流式 — reasoning_content 与 content 分离
stream = client.chat.completions.create(
    model="GLM-5.1",
    messages=[{"role": "user", "content": "数到5"}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.reasoning_content:
        print(f"[思考] {delta.reasoning_content}", end="")
    if delta.content:
        print(f"[回答] {delta.content}", end="")
```

### cURL

```bash
# 非流式
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM-5.1","messages":[{"role":"user","content":"你好"}],"stream":false}'

# 流式
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM-5.1","messages":[{"role":"user","content":"数到5"}],"stream":true}'
```

### Claude Code

```bash
# 设置环境变量后启动 Claude Code
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=sk-web2api-placeholder
claude
```

### Cursor

在 Cursor 设置中：
```
OpenAI API Base: http://localhost:8000/v1
OpenAI API Key: sk-web2api-placeholder
Model: GLM-5.1
```

### Continue (VS Code 插件)

在 `config.json` 中添加：

```json
{
  "models": [{
    "title": "Z.ai GLM-5.1",
    "provider": "openai",
    "model": "GLM-5.1",
    "apiBase": "http://localhost:8000/v1",
    "apiKey": "sk-web2api-placeholder"
  }]
}
```

---

## 思考/回答分离

chat.z.ai 的 SSE 响应包含 `phase` 字段，代理自动解析并映射为 OpenAI 兼容格式：

### 非流式响应

```json
{
  "choices": [{
    "message": {
      "content": "Tokyo",
      "reasoning_content": "1. Identify the core question...The user asks for the capital of Japan. 2. Recall geographical knowledge..."
    }
  }]
}
```

### 流式响应

```
data: {"choices":[{"delta":{"reasoning_content":"思考过程..."},"index":0}]}
data: {"choices":[{"delta":{"content":"最终答案"},"index":0}]}
data: [DONE]
```

---

## 项目结构

```
chat-z-ai-proxy/
├── adapter.py          # 核心适配器 — Playwright 浏览器后端
├── server.py           # FastAPI 代理服务器
├── extract_env.py      # HAR → .env 提取工具
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 一键启动
├── .env                # 配置文件（Token 等）
├── tool_dsml.py        # DSML 工具调用支持
└── tool_sieve.py       # 流式内容分离引擎
```

---

## 常见问题

### 启动报错 `Executable doesn't exist`

Playwright 未安装浏览器：

```powershell
python -m playwright install chromium
```

### 报错 `Error: Chat input not found`

浏览器页面未正常加载。常见原因：
- **Token 过期**：重新导出 HAR 并更新 `.env`
- **首次启动需等待**：浏览器首次加载约 15-20 秒

### 返回 INTERNAL_ERROR

服务端返回错误，可能原因：
- **Token 过期** → 重新提取 Token
- **模型暂时不可用** → 等待几分钟后重试
- **使用频率过高** → 降低调用频率

### 首请求很慢

浏览器首次启动需加载页面资源（约 15-20 秒），后续请求复用同一个浏览器会话，不再有此延迟。

### 如何更换模型

在 `.env` 中修改 `MODEL_NAME`。可用模型列表：

```bash
curl http://localhost:8000/v1/models
```

常见模型：`GLM-5.1`、`GLM-5-Turbo`、`glm-4-flash`、`glm-4.7`

### Token 多久过期？

JWT Token 有效期不定（数小时到数天）。当遇到一直返回 403/INTERNAL_ERROR 时，重新导出 HAR 提取新 Token。

---

## 技术参考

### x-signature 算法（已逆向）

适配器不直接使用此算法，仅作技术参考：

```
sortedPayload = "requestId:<uuid>,timestamp:<ts>,user_id:<user_id>"
b64prompt = base64(prompt)
msg = sortedPayload + "|" + b64prompt + "|" + timestamp
key = str(floor(timestamp / 300000))   # 5 分钟时间窗口
signature = HMAC-SHA256(msg, key)
```

### 反检测措施

- 使用系统 Chrome（`channel: "chrome"`）而非 Playwright 内置 Chromium
- 覆盖 `navigator.webdriver`、`navigator.plugins`、`chrome.runtime`
- 设置完整 User-Agent，隐藏 HeadlessChrome 特征

### 验证码流程

阿里云 TRACELESS 验证码通过设备指纹自动判定，无需用户交互。适配器通过浏览器页面发送消息，页面 JS 自动触发并完成验证码流程，适配器仅捕获结果。

---

## 许可

本项目仅供学习和研究使用。使用前请确保遵守 Z.ai 的服务条款。
