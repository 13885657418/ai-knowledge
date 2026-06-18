# Agent 循环踩坑笔记

> Day9 做 `agent_service` 的 function calling 循环（`use_agent=true`），第一次写真正的 ReAct 工具循环，被模型各种「不按套路出牌」教做人。全是后端 Agent 编排的坑。

---

## 坑 1：function calling 返回解析不对，工具根本没被调用

**现象**：模型明明该调 `kb_info` 工具，结果我代码里一直走「直接返回文本」分支，工具从没执行。

**根因**：我只看了 `message.content`，但 function calling 模式下，模型要调工具时 `content` 往往是 `None`，真正的调用信息在 `tool_calls` 字段里。我判断逻辑写反了。

**解决**：先判断有没有 `tool_calls`，有就执行工具、没有才当最终答案：

```python
msg = response.choices[0].message
if msg.tool_calls:
    for call in msg.tool_calls:
        name = call.function.name
        args = json.loads(call.function.arguments)   # 注意是字符串，要 json.loads
        result = tool_registry.run(name, args)
        # 回灌（见坑 3）
else:
    final_answer = msg.content   # 没有工具调用了，收尾
```

另一个小坑：`call.function.arguments` 是 **JSON 字符串** 不是 dict，得 `json.loads`。模型偶尔还会吐不合法 JSON，要 try/except 兜底。

**教训**：function calling 的结果结构和普通 chat 不一样，`tool_calls` 才是关键，`content` 可能是空的。

---

## 坑 2：没设最大步数，模型陷入死循环

**现象**：某次 Agent 跑了几十轮工具调用停不下来，token 哗哗烧，接口超时。

**根因**：我写的是 `while True:` 一直循环直到模型给最终答案。但模型有时候会反复调同一个工具（拿到结果觉得「还不够」又调一次），没有终止条件就停不下来。

**解决**：设 `max_steps` 上限 + 单轮总超时，到顶就强制收敛：

```python
MAX_STEPS = 6
for step in range(MAX_STEPS):
    response = llm.chat(messages, tools=tool_schemas)
    msg = response.choices[0].message
    if not msg.tool_calls:
        return msg.content              # 正常产出
    # ...执行工具并回灌...
else:
    # 达到最大步数还没收敛，强制让模型基于已有信息作答
    messages.append({"role": "user",
                     "content": "已达最大工具调用次数，请基于现有信息直接给出最终答案。"})
    return llm.chat(messages).choices[0].message.content   # 不再带 tools
```

**教训**：Agent 循环必须有刹车。`max_steps` + 超时 + 「最后一轮不给 tools 逼它收尾」三件套，按设计文档的「失控防护」来。

---

## 坑 3：工具结果回灌格式错，模型「看不见」结果

**现象**：工具执行成功了，但模型下一轮表现得像没拿到结果，要么重复调用要么瞎编。

**根因**：回灌消息格式不对。工具结果必须作为 `role: "tool"` 的消息，而且要带上对应的 `tool_call_id`，模型才能把「这个结果」对上「我刚才发起的那次调用」。我一开始塞成了 `role: "user"`，模型对不上号。

**解决**：先把模型那条带 tool_calls 的 assistant 消息原样加回 messages，再为每个 call 追加一条 `role: tool` 结果：

```python
messages.append(msg)   # 模型发起调用的 assistant 消息，必须先加回去
for call in msg.tool_calls:
    result = tool_registry.run(call.function.name, json.loads(call.function.arguments))
    messages.append({
        "role": "tool",
        "tool_call_id": call.id,          # 关键：和发起的调用对应
        "content": json.dumps(result, ensure_ascii=False),
    })
# 下一轮再把整个 messages 发回模型
```

**教训**：回灌不是随便塞个结果就行——`role: tool` + 正确的 `tool_call_id` + 先加回 assistant 消息，三个都对，模型才能正确「接着推理」。

---

## 坑 4：模型反复调同一个工具，死循环防护不够

**现象**：加了 `max_steps` 后不会无限循环了，但模型会在上限内反复用 **完全相同的参数** 调同一个工具，纯属浪费步数。

**根因**：模型有时拿到结果没「领会」，下一轮又发起一模一样的调用。光靠步数上限只是兜底，没解决重复。

**解决**：
1. 记录已执行过的 `(tool_name, args)`，命中重复时不真正执行，直接回灌「该工具已用相同参数调用过，结果见上」，提醒模型换思路。
2. 工具白名单：只允许调注册过的工具，模型乱点不存在的工具直接拒绝并回灌错误。
3. 每一步都记进 retrieval_log / Langfuse，复盘时能一眼看出它卡在哪一步。

```python
seen = set()
key = (name, json.dumps(args, sort_keys=True))
if key in seen:
    tool_result = "（该工具已用相同参数调用过，请勿重复，请基于已有结果继续。）"
else:
    seen.add(key)
    tool_result = tool_registry.run(name, args)
```

**教训**：真实模型不是理想 ReAct agent，会犯重复调用、调不存在工具的错。防护要「步数上限 + 去重 + 白名单 + 全程可观测」一起上，光靠一个 max_steps 不够。

---

## 坑 5：MCP 工具和本地工具 schema 不统一，循环里分叉处理

**现象**：接入 MCP（filesystem）后，代码里出现「如果是本地工具走 A、如果是 MCP 工具走 B」的分叉，越写越乱。

**根因**：没把 MCP 工具收敛到统一抽象，循环里到处 if-else 判断工具来源。

**解决**：让 MCP 工具和本地工具都实现同一个工具基类、暴露统一的 JSON Schema，全部注册进同一个 `ToolRegistry`。Agent 循环只跟 `ToolRegistry` 打交道，不关心工具背后是本地函数还是 MCP server：

```python
# tools/base.py 统一接口；mcp_tool.py 把 MCP server 的工具包装成同一接口
schemas = tool_registry.list_schemas()     # 本地 + MCP 一起给模型
result = tool_registry.run(name, args)      # 统一入口，内部路由到本地/MCP
```

**教训**：按设计文档把工具统一到 ToolRegistry + 统一 schema，Agent 循环才干净。新增工具（不管本地还是 MCP）只是注册一下，循环逻辑一行不用改。
