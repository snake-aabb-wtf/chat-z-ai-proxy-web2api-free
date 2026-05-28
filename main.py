#!/usr/bin/env python3
"""chat.z.ai OpenAI 兼容代理 — 启动入口

使用方法:
  1. 在浏览器中打开 https://chat.z.ai，发送一条消息
  2. F12 → Network → 右键 → Save all as HAR
  3. python extract_env.py chat.z.ai.har  (提取 TOKEN + CAPTCHA)
  4. python server.py                      (启动代理)
  5. 设置 OPENAI_API_BASE=http://localhost:8000/v1
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
