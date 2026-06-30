# Python SDK 使用指南

NanoBot 提供基于 HTTP API 的 Python SDK，允许外部程序通过 REST API 与运行中的 NanoBot 实例交互。

## 基础用法

```python
import httpx

BASE_URL = "http://localhost:18080"  # NanoBot HTTP 服务地址
client = httpx.Client(base_url=BASE_URL)
```

---

## 配置读取与更新

### 获取完整配置

```python
def get_config():
    """GET /api/config — 返回完整的配置 JSON"""
    resp = client.get("/api/config")
    resp.raise_for_status()
    return resp.json()

config = get_config()
print(config["agents"]["defaults"]["model"])
```

### 更新完整配置

```python
def update_config(config_data: dict):
    """PUT /api/config — 保存完整配置"""
    resp = client.put("/api/config", json=config_data)
    resp.raise_for_status()
    return resp.json()

# 更新模型设置
config = get_config()
config["agents"]["defaults"]["model"] = "gpt-4o"
update_config(config)
```

### 获取/更新设置

```python
def get_settings():
    """GET /api/settings — 获取简化设置"""
    resp = client.get("/api/settings")
    resp.raise_for_status()
    return resp.json()

def update_settings(model: str = None, provider: str = None):
    """PUT /api/settings/update — 更新模型和提供商"""
    data = {}
    if model:
        data["model"] = model
    if provider:
        data["provider"] = provider
    resp = client.put("/api/settings/update", json=data)
    resp.raise_for_status()
    return resp.json()
```

### 获取可选模型列表

```python
def get_provider_models(provider: str):
    """GET /api/provider-models?provider=X — 从提供商 API 获取可用模型列表"""
    resp = client.get(f"/api/provider-models?provider={provider}")
    resp.raise_for_status()
    return resp.json()["models"]

models = get_provider_models("openai")
print(models)  # ["gpt-4o", "gpt-4o-mini", ...]
```

---

## 记忆搜索

### 基本搜索

```python
def memory_search(query: str, llm_interpret: bool = False):
    """GET /api/memory/search?q=...
    
    Args:
        query: 搜索关键词
        llm_interpret: 是否使用 LLM 进行语义解释
    
    Returns:
        results: 匹配的记忆片段列表
        interpretation: (可选) LLM 对搜索结果的解释
    """
    params = {"q": query}
    if llm_interpret:
        params["llm"] = "1"
    resp = client.get("/api/memory/search", params=params)
    resp.raise_for_status()
    return resp.json()

result = memory_search("我的 API 密钥")
for r in result["results"]:
    print(f"[{r['source']}] {r['heading']}: {r['text'][:100]}...")
    print(f"  相关度: {r['score']}")

# 使用 LLM 解释搜索结果
result = memory_search("我之前做了什么项目", llm_interpret=True)
print(result["interpretation"])
```

返回格式：

```json
{
  "results": [
    {
      "source": "memory/notes.md",
      "heading": "项目计划",
      "text": "相关文本内容...",
      "score": 0.85
    }
  ],
  "interpretation": "LLM 对结果的解释（仅当 llm=1 时）"
}
```

搜索策略（自动回退）：
1. 首先尝试 FAISS 向量检索（前 5 条匹配）
2. 如果 FAISS 索引不可用或为空，回退到 grep 关键词匹配
3. 搜索范围包括记忆文件（`memory/`）、任务文件（`tasks/`）和技能文件

### 重建向量索引

```python
def rebuild_memory_index():
    """POST /api/memory/rebuild-index — 重建 FAISS 向量索引"""
    resp = client.post("/api/memory/rebuild-index")
    resp.raise_for_status()
    return resp.json()

result = rebuild_memory_index()
print(f"FAISS 可用: {result['faiss_available']}")
print(f"索引块数: {result['chunks']}")
```

---

## AI 对话（记忆 AI 聊天 SSE）

```python
import json
import httpx

def memory_chat(message: str, history: list = None):
    """POST /api/memory/chat — 基于记忆的 AI 对话，SSE 流式返回
    
    Args:
        message: 用户消息
        history: 对话历史 (可选)
    
    Yields:
        tokens / error / sources / done 事件
    """
    payload = {
        "message": message,
        "history": history or [],
    }
    
    with httpx.Client(base_url=BASE_URL) as client:
        with client.stream("POST", "/api/memory/chat", json=payload) as resp:
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    yield event_type, data

# 使用示例
history = [
    {"role": "assistant", "content": "你好！有什么可以帮你的？"}
]

print("AI: ", end="", flush=True)
full_text = ""
for event, data in memory_chat("我的待办事项有哪些？", history=history):
    if event == "token":
        token = data["token"]
        full_text += token
        print(token, end="", flush=True)
    elif event == "sources":
        print("\n\n参考资料:")
        for s in data["sources"]:
            print(f"  - {s['source']} (相关度: {s['score']:.2f})")
    elif event == "done":
        print("\n\n[对话完成]")
    elif event == "error":
        print(f"\n[错误]: {data['error']}")
```

### SSE 事件类型

| 事件 | 说明 | data 字段 |
|------|------|-----------|
| `token` | 生成的文本 token | `{"token": "..."}` |
| `error` | 错误信息 | `{"error": "..."}` |
| `sources` | 引用的记忆来源 | `{"sources": [...]}` |
| `done` | 对话结束 | `{}` |

### 对话流程

1. 用户发送消息和可选历史
2. 系统自动搜索相关记忆（FAISS + grep 回退）
3. 将检索到的记忆片段作为上下文
4. LLM 基于上下文生成回复
5. SSE 流式返回 token，最后返回引用来源

---

## 工作区文件读取

```python
def read_workspace_file(file_path: str):
    """GET /api/workspace/file?path=...
    
    读取工作区内的 Markdown 文件。带有路径穿越防护。
    
    Args:
        file_path: 相对于工作区的文件路径
    
    Returns:
        content: 文件内容
        exists: 文件是否存在
        path: 实际路径
    """
    resp = client.get("/api/workspace/file", params={"path": file_path})
    resp.raise_for_status()
    return resp.json()

# 读取记忆文件
result = read_workspace_file("memory/MEMORY.md")
print(f"存在: {result['exists']}")
if result['exists']:
    print(result['content'])

# 读取任务文件
result = read_workspace_file("tasks/CURRENT.md")
print(result['content'])
```

安全机制：
- 路径穿越防护：解析后的路径必须以工作区路径为前缀
- 只允许读取存在的文件
- 返回 `{"exists": false}` 而非 404，方便客户端处理

---

## 健康检查

```python
def health_check():
    """GET /health — 检查服务是否运行"""
    resp = client.get("/health")
    resp.raise_for_status()
    return resp.json()["status"] == "ok"
```

---

## 关闭与重启

```python
def shutdown():
    """POST /api/shutdown — 停止所有代理后重启 Gateway 进程"""
    resp = client.post("/api/shutdown")
    resp.raise_for_status()
    return resp.json()

def stop():
    """POST /api/stop — 停止所有代理并退出 Gateway 进程"""
    resp = client.post("/api/stop")
    resp.raise_for_status()
    return resp.json()
```

---

## API 路由汇总

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/settings` | 获取简化设置 |
| PUT | `/api/settings/update` | 更新设置 |
| GET | `/api/config` | 获取完整配置 |
| PUT | `/api/config` | 保存完整配置 |
| GET | `/api/provider-models?provider=X` | 获取提供商模型列表 |
| GET | `/api/memory/search?q=...` | 记忆搜索 |
| POST | `/api/memory/rebuild-index` | 重建向量索引 |
| POST | `/api/memory/chat` | AI 对话（SSE 流式） |
| GET | `/api/workspace/file?path=...` | 读取工作区文件 |
| POST | `/api/shutdown` | 重启 Gateway |
| POST | `/api/stop` | 停止 Gateway |
