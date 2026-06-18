# SSE 流式踩坑笔记

> Day7 做 `ask/stream` 流式问答，第一次用 FastAPI 的 SSE，踩了一路。全是后端流式输出的坑。

---

## 坑 1：StreamingResponse 用了普通 def，整个响应卡住

**现象**：`curl -N` 请求 `/ask/stream`，半天没反应，等模型全部生成完才一次性吐出来，根本不是流式。

**根因**：我把生成器写成了普通函数 + 同步阻塞调用 LLM，FastAPI 在线程池里跑完才返回，逐 token 的意义没了。SSE 要的是「边生成边 yield」。

**解决**：用 `async def` 生成器 + 异步流式调用 LLM，每拿到一个增量 token 就立刻 yield：

```python
from fastapi.responses import StreamingResponse

async def event_generator():
    # 先发检索元数据
    yield format_sse("retrieval", retrieval_meta)
    async for delta in llm_service.stream(prompt):   # 异步逐 token
        yield format_sse("token", {"text": delta})
    yield format_sse("done", {"citations": cits, "usage": usage})

return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**教训**：流式三要素——`async def` 生成器、异步的上游 LLM 调用、`media_type="text/event-stream"`，缺一个就退化成「假流式」。

---

## 坑 2：event 格式不对，前端 EventSource 收不到

**现象**：演示前端用 `EventSource` 监听，`onmessage` 一直不触发，但 `curl` 能看到原始数据。

**根因**：SSE 有严格的文本格式：每个事件由 `event:` / `data:` 行组成，**每条数据以两个换行 `\n\n` 结尾**。我只写了一个 `\n`，浏览器认为事件没结束，一直缓着。

**解决**：统一格式化函数，注意 data 必须是单行（JSON 不能带裸换行），结尾两个换行：

```python
import json

def format_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
```

按设计文档的事件序列：`event: retrieval` → 多个 `event: token` → `event: done`。前端按 `event` 名分别监听。

**教训**：SSE 是纯文本协议，格式错一个换行就全乱。data 里的 JSON 要保证单行。

---

## 坑 3：跨域，前端连 SSE 直接被 CORS 拦

**现象**：演示前端（`localhost:5173`）连后端（`localhost:8000`）的 SSE，控制台报 CORS 错误。

**根因**：跨域请求，后端没把演示前端的 origin 加进 `BACKEND_CORS_ORIGINS`。SSE 本质还是 HTTP 请求，照样受 CORS 约束。

**解决**：`.env` 里把演示前端地址加进允许列表（模板已有 CORS 中间件）：

```dotenv
BACKEND_CORS_ORIGINS="http://localhost:5173,http://localhost"
```

**教训**：流式接口也是普通 HTTP，CORS 该配还得配。这个前端就是个本地联调用的演示页，加白名单即可，别在后端为它写特殊逻辑。

---

## 坑 4：Nginx/反代把流缓冲了，又变成一次性返回

**现象**：本地直连后端是流式的，过了一层 Nginx 反代后又变成「等全部生成完才一起返回」。

**根因**：Nginx 默认开 `proxy_buffering`，会把上游响应攒在缓冲区里再转发，正好把流式攒没了。

**解决**：对 SSE 路由关掉缓冲。后端在响应头里加提示，反代侧也关：

```python
headers = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # 告诉 Nginx 别缓冲这个响应
    "Connection": "keep-alive",
}
return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
```

Nginx 侧：`proxy_buffering off;` 并 `proxy_read_timeout` 调大，避免长响应被断。

**教训**：流式链路上任何一层缓冲都会毁掉流式。`X-Accel-Buffering: no` 是给 Nginx 的暗号，记得加。

---

## 坑 5：客户端断连了，后端还在烧 token

**现象**：用户关页面或网络断了，后端生成器还在继续跑 LLM，token 照烧，retrieval_log 也记了一条「正常完成」的假数据。

**根因**：没监听客户端断连。SSE 是长连接，客户端走了服务端不会自动知道。

**解决**：用 `request.is_disconnected()` 检测，断连就停掉生成、做清理、按「中断」落库：

```python
async def event_generator(request: Request):
    try:
        async for delta in llm_service.stream(prompt):
            if await request.is_disconnected():
                break          # 客户端走了，停止生成
            yield format_sse("token", {"text": delta})
    finally:
        # 无论正常结束还是断连，都落库已生成部分 + 已用 token
        persist_message_and_usage(partial_answer, usage)
```

**教训**：长连接一定要处理断连，否则白烧钱还污染日志。`finally` 里兜底落库，保证 token 统计不丢。
