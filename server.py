import os
import time
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Union

from adapter import ChatAdapter

load_dotenv()

TOKEN = os.getenv("TOKEN", "")
MODEL_NAME = os.getenv("MODEL_NAME", "GLM-5.1")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DSML_ENABLED = os.getenv("DSML_ENABLED", "true").lower() in ("true", "1", "yes")

adapter = ChatAdapter(token=TOKEN, dsml_enabled=DSML_ENABLED)

app = FastAPI(title="web2api - chat.z.ai proxy", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ContentPart(BaseModel):
    type: str
    text: Optional[str] = None

class ChatMessage(BaseModel):
    role: str
    content: Union[str, list[ContentPart]]

class ToolFunction(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[dict] = None

class Tool(BaseModel):
    type: str = "function"
    function: ToolFunction

class ChatCompletionRequest(BaseModel):
    model: str = MODEL_NAME
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[list[Tool]] = None
    tool_choice: Optional[Union[str, dict]] = None


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "created": int(time.time()), "owned_by": "web2api"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    stream = request.stream
    tools_dict = None
    if request.tools:
        tools_dict = [t.model_dump() for t in request.tools]
    payload = adapter.convert_request(messages, stream=stream, tools=tools_dict, tool_choice=request.tool_choice)

    if stream:
        return StreamingResponse(
            adapter.stream_request(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    else:
        try:
            return adapter.convert_response(await adapter.send_request(payload))
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": {"message": f"Upstream error: {str(e)}", "type": "upstream_error", "code": 502}})


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.on_event("startup")
async def startup():
    print("Starting browser...")
    await adapter.start()
    print("Ready!")


@app.on_event("shutdown")
async def shutdown():
    await adapter.close()


if __name__ == "__main__":
    print(f"chat.z.ai proxy on http://{HOST}:{PORT}")
    print(f"  Set OPENAI_API_BASE=http://localhost:{PORT}/v1")
    uvicorn.run(app, host=HOST, port=PORT)
