# GavinAgent vs llm-api — 架構比較分析

> 分析日期：2026-04-10  
> 分析範圍：Agent Loop、MCP 整合、Skills 機制、工具調用、RAG 實作

---

## 1. 專案定位總覽

| 面向 | GavinAgent | llm-api |
|------|-----------|---------|
| **核心角色** | 自主 Agent（ReAct 循環） | RAG 聊天平台（單次生成） |
| **架構類型** | LangGraph 狀態機 + 多輪工具調用 | 請求→RAG→生成→回應（無循環） |
| **LLM 呼叫框架** | LiteLLM（provider 無關） | LangChain wrappers（`AzureChatOpenAI`, `ChatOllama`） |
| **工具調用** | OpenAI function calling（真實執行） | 無（偽造 tool messages 只作 RAG 歷史格式） |
| **MCP 支援** | ✅ 完整（stdio + SSE，動態發現） | ❌ 無 |
| **Skills 機制** | ✅ 完整（embedding 路由 + handler 執行） | ❌ 無（有 assistant 概念但無 skills） |
| **向量資料庫** | PostgreSQL + pgvector | Milvus |
| **非同步執行** | Celery 任務 + asyncio（MCP 用） | Django 同步 view + asyncio generator（SSE 用） |
| **人工審核** | ✅ 逐步工具審核（WAITING 暫停機制） | ❌ 無 |
| **記憶機制** | 長期記憶（pgvector 語意搜尋） | 無（僅對話歷史 DB） |

---

## 2. Agent Loop 比較

### 2.1 GavinAgent：ReAct 多輪循環

GavinAgent 使用 **LangGraph `StateGraph`** 實作完整的 ReAct（Reason + Act）循環。

#### 2.1.1 Graph 節點與邊

```
assemble_context（no-op）
    ↓
call_llm ──→ (has tool_calls?) ──→ check_approval
    │                                    │
    └──→ save_result ←──────────────── (waiting?) ──→ END（暫停等待）
              ↑                          │
         force_conclude ←───────────── execute_tools
              ↑                          │
              └──── (rounds >= max  ←───┘
                     or all failed)
                                    ↓
                                call_llm（下一輪）
```

**節點職責：**

| 節點 | 位置 | 職責 |
|------|------|------|
| `assemble_context` | `nodes.py` | 目前為 no-op，context 在 `call_llm` 內組裝 |
| `call_llm` | `nodes.py:283+` | 組裝 system prompt → 呼叫 LLM → 解析 tool_calls 或最終答案 |
| `check_approval` | `nodes.py` | 逐個工具判斷審核政策，建立 `ToolExecution` 紀錄 |
| `execute_tools` | `nodes.py` | 平行/串行執行工具，收集結果 |
| `force_conclude` | `nodes.py` | 強制 LLM 給出最終答案（無工具） |
| `save_result` | `nodes.py` | 持久化輸出到 `AgentRun`，更新狀態 |

#### 2.1.2 State 結構（`AgentState` TypedDict）

```python
class AgentState(TypedDict):
    run_id: str
    agent_id: str
    conversation_id: str | None
    input: str
    messages: Annotated[list[dict], operator.add]  # 累積（LangGraph annotation）
    pending_tool_calls: list[dict]        # 等待執行的工具呼叫
    tool_results: list[dict]              # 當前輪次工具結果（每輪替換）
    assistant_tool_call_message: dict | None  # LLM 發出工具呼叫的完整訊息
    output: str
    waiting_for_approval: bool
    tool_call_rounds: int                 # 已執行輪次計數
    visited_urls: list[str]              # 防重複 URL 抓取
    failed_tool_signatures: list[str]    # 失敗工具指紋（name|arg_hash）
    succeeded_tool_signatures: list[str] # 成功工具指紋
    collected_markdown: list[str]        # 跨輪次累積的 markdown（圖表等）
    search_result_urls: list[str]        # 搜尋結果 URL
    loop_trace: list[dict]               # 每輪決策日誌
    blocked_mcp_servers: list[str]       # 本次 run 中無法使用的 MCP server
    consecutive_failed_rounds: int       # 連續失敗輪次數
    error: str | None
```

#### 2.1.3 單輪 call_llm 流程

```
1. 取消偵測（AgentRun.status == FAILED → 提早退出）
2. 載入 agent model（DB Agent.model 或 settings.LITELLM_DEFAULT_MODEL）
3. _build_system_context(query)
   ├── temporal_context（當前時間 + 時區）
   ├── AGENTS.md（工作區指令）
   ├── SOUL.md（個性定義）
   ├── _build_skills_section(query) → embedding 路由匹配技能
   ├── search_long_term(query) → pgvector 長期記憶
   ├── MCPConnectionPool.fetch_always_include_resources() → MCP 資源注入
   └── _build_knowledge_section(query) → RAG 知識庫
4. 組裝 messages[]
   ├── [system] system_content
   ├── [history] ChatMessage 歷史（最近 N 條，token budget 截斷）
   └── [tool/assistant] 上一輪工具結果（嚴格 ID 匹配）
5. 組裝 tools_schema[]
   ├── 只包含 agent.tools 中啟用的工具
   ├── 技能觸發時自動注入依賴工具
   └── MCP registry 的所有工具（動態）
6. litellm.completion(model, messages, tools)
7. 解析回應：
   ├── tool_calls → 返回 pending_tool_calls → check_approval
   └── content → 返回 output → save_result
```

#### 2.1.4 終止條件

```python
def _after_execute_tools(state):
    max_rounds = settings.AGENT_MAX_TOOL_CALL_ROUNDS  # 預設 20
    if state["tool_call_rounds"] >= max_rounds:
        return "force_conclude"
    if state["consecutive_failed_rounds"] >= settings.AGENT_MAX_CONSECUTIVE_FAILED_ROUNDS:  # 預設 2
        return "force_conclude"
    return "call_llm"
```

#### 2.1.5 工具去重機制

```python
def _tool_sig(tool_name, args) -> str:
    if tool_name == "run_skill":
        key = {"skill_name": args.get("skill_name", "")}   # 只用 skill_name
    elif tool_name == "chart":
        key = {"title": args.get("title", "")}             # 只用 title
    else:
        key = args
    return f"{tool_name}|{md5(json.dumps(key, sort_keys=True)).hexdigest()}"
```

**成功簽名**（`succeeded_tool_signatures`）與**失敗簽名**（`failed_tool_signatures`）跨輪次持久化，防止 LLM 重複調用相同工具。

#### 2.1.6 Run 的執行路徑

```
AgentRunner.run(run)                    [runner.py]
    ↓
detect is_resume（graph_state 有 pending_tool_calls？）
    ↓
初始化 AgentState（或從 WAITING 狀態恢復）
    ↓
build_graph().invoke(initial_state)     [graph.py]
    ↓
Celery task execute_agent_run           [tasks.py]
```

**WAITING 恢復機制：**  
當 `check_approval` 發現需要人工審核時，將 `AgentRun.status = WAITING`，序列化未決工具呼叫到 `AgentRun.graph_state`（JSON）。用戶審核後觸發 `AgentRunner.run(run)` 重新進入，`_resolve_approved_tools()` 執行已核准的工具，不核准的返回錯誤訊息，然後繼續 graph。

---

### 2.2 llm-api：單次 RAG 生成

llm-api **沒有 agent loop**。每次請求是一個線性管道：

```
用戶訊息
    ↓
[可選] RAG 檢索階段（Milvus + BM25 + LLM 文件選擇器）
    ├── MultiStepRAGService.decompose_question_streaming()  ← 問題分解（thinking=True 時）
    │       └── ParallelRAGProcessor.process_sub_questions_parallel()
    ├── _execute_single_step_rag()
    │       ├── 語意搜尋（Milvus cosine similarity）
    │       └── BM25 文件名搜尋
    └── score_based_fusion() + dedupe_rag_chunks()
    ↓
[單次] LLM 生成
    ↓
SSE 串流回應
    ↓
儲存至 DB
```

**假工具訊息（Synthetic Tool Messages）**：  
llm-api 在 `generate_conversation()` 中，將 DB 裡儲存的 RAG chunks（JSON 欄位 `message.context`）以 `ToolMessage` / `AIMessage(tool_calls=[...])` 的 LangChain 格式注入對話歷史。這**不是真實工具呼叫**，只是讓 LLM 在對話歷史中看到 RAG 上下文的格式技巧。

```
# LLM 看到的假工具訊息結構（重建歷史用）
AIMessage(tool_calls=[{"name": "rag_search", "id": "xxx"}])
ToolMessage(content="[RAG chunks...]", tool_call_id="xxx")
HumanMessage(content="...")
```

---

## 3. MCP（Model Context Protocol）比較

### 3.1 GavinAgent：完整 MCP 實作

#### 架構層次

```
MCPServer（Django model）
    ├── name, transport（stdio/sse）
    ├── command（stdio）/ url（sse）
    ├── env（加密 JSON）
    ├── auto_approve_tools（白名單）
    ├── always_include_resources（自動注入資源 URI）
    └── connection_status / last_error
         ↓
MCPConnectionPool（process-level singleton）
    ├── asyncio event loop（背景執行緒 "mcp-pool"）
    ├── 每個 server 一個 _ServerConnection（含 session）
    ├── 背景重試執行緒（"mcp-retry"，每 60 秒健康檢查）
    └── 公開同步 API（call_tool, read_resource, get_status）
         ↓
MCPToolRegistry（in-memory singleton）
    ├── 以 llm_function_name 為 key（server__tool 格式）
    └── to_llm_schemas() → OpenAI function schema list
```

#### 連線生命週期

```
Django/Celery 啟動
    ↓ signals.py → MCPConnectionPool.get().start_all()
    ↓ 對每個 enabled MCPServer：
        ├── stdio → run_stdio_connection() 協程（長存）
        └── sse   → run_sse_connection() 協程（長存）
    ↓ 連線成功 → on_ready()
        ├── session.list_tools() → MCPToolRegistry.register()
        └── 更新 MCPServer.connection_status = "connected"
    ↓ 連線失敗 → 最多 3 次重試（指數退避）
    ↓ 背景 retry loop 每 60 秒補充斷開的 server
```

#### 工具命名規則

```python
# 工具名稱格式：{server_name}__{tool_name}（無效字元替換為 _）
entry.llm_function_name = _safe_function_name(f"{server_name}__{tool_name}")

# 範例：
# MCPServer.name = "github"
# Tool.name = "list-repos"
# → llm_function_name = "github__list_repos"
```

#### 工具執行（execute_tools 中）

```python
mcp_entry = get_mcp_registry().get(tool_name)   # 查 in-memory registry
if mcp_entry:
    result = MCPConnectionPool.get().call_tool(
        mcp_entry.server_name, mcp_entry.tool_name, args
    )
    # call_tool → asyncio.run_coroutine_threadsafe(_call_tool_async(...))
    # _call_tool_async → session.call_tool(tool_name, args)
    # 若 session 已失效（ClosedResourceError 等）→ 自動重連後重試一次
```

#### 審核政策

MCP 工具預設需要人工審核，除非：
1. 是 workflow 觸發（`trigger_source == WORKFLOW`）→ 全部自動核准
2. 工具名稱在 `MCPServer.auto_approve_tools`（白名單 JSONField）中

#### always_include_resources

```python
# 系統提示詞組裝時自動注入
resources = MCPConnectionPool.get().fetch_always_include_resources()
# → 讀取每個 server 的 always_include_resources URI 列表
# → 透過 session.read_resource(uri) 取得內容
# → 注入至 system prompt 的 ## MCP Resources 區塊
```

---

### 3.2 llm-api：無 MCP

llm-api 完全沒有 MCP 相關程式碼，也沒有工具調用機制。外部服務整合通過以下方式：
- **Confluence**：`ConfluenceClient`（HTTP REST API，作為 RAG 資料來源）
- **知識服務**：`KnowledgeClient`（HTTP API，檢查 SMB/S3 資料夾權限）
- **Built-in Assistants**：HTTP proxy 到外部 AI 端點（非 LLM 呼叫，純轉發）

---

## 4. Skills 機制比較

### 4.1 GavinAgent：完整 Skills 系統

#### 目錄結構

```
agent/workspace/skills/
    {skill_name}/
        SKILL.md          # 技能定義（YAML frontmatter + Markdown 說明）
        handler.py        # 可選，程式執行邏輯
```

#### SKILL.md 格式

```yaml
---
name: data-analysis          # 技能識別名稱
description: "分析結構化資料"  # 顯示於技能索引
triggers:                    # 關鍵字觸發（embedding 失效時的 fallback）
  - analyze
  - chart
trigger_patterns:            # Regex 觸發
  - "\\d+.*data"
tools:                       # 觸發時自動注入的工具（即使 agent.tools 未開啟）
  - sql_query
  - chart
approval_required: false     # handler 執行時是否需要審核
---

## 說明內容（注入至 system prompt）
...
```

#### 觸發與路由流程

```
_build_skills_section(query)
    ↓
1. find_relevant_skills(query)          ← embedding 相似度搜尋（pgvector）
    → returns list[tuple[str, float]]   ← (skill_name, similarity_score)
    ↓
2. 對每個 skill_dir 判斷是否匹配：
    優先：embedding_matches 中有此 skill → matched=True, reason="embedding"
    次之：無 embedding 資料且無 triggers → 短技能（<50 行）自動注入
    最後：keyword 比對（triggers 列表）→ reason="keyword"
          regex 比對（trigger_patterns）→ reason="regex"
    ↓
3. 已匹配的技能：
    ├── 將 body 內容注入 system prompt（## Skills 區塊）
    ├── 加入 triggered[] 列表
    └── 依 SKILL.md tools 欄位自動注入工具
    ↓
4. call_llm 中：
    └── 若技能有 handler.py → 自動注入 run_skill 工具
```

#### Skills 執行（兩種模式）

**模式 A：Prompt-only 技能（無 handler.py）**  
技能說明注入 system prompt，LLM 直接遵循指令，不需要工具呼叫。

**模式 B：Handler 技能（有 handler.py）**  
```python
# SkillLoader 動態載入 handler.py
def _load_handler(path, skill_name) -> Callable:
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "handle", None) or getattr(module, "run", None)

# 執行路徑（execute_tools 中的 run_skill 工具）
# LLM 呼叫：run_skill(skill_name="data-analysis", input="分析這份數據")
# → RunSkillTool.execute(skill_name, input)
#   → registry.get(skill_name).handler(input)   ← 動態執行 handler.py
```

#### DB 管理

`Skill` model（`agent/models.py`）可停用特定技能：
```python
disabled_skills = set(Skill.objects.filter(enabled=False).values_list("name", flat=True))
# 在 _build_skills_section 中跳過已停用技能
```

#### Skills 的 Embedding 索引

技能透過 `agent/skills/embeddings.py` 建立向量索引（pgvector），用於語意路由。
技能的 description + instructions 被 embedding 後存入 DB，查詢時計算 cosine distance。

---

### 4.2 llm-api：Assistant 概念（非 Skills）

llm-api 沒有 skills 機制，但有 **Custom Assistant** 概念：

| 概念 | llm-api Assistant | GavinAgent Skill |
|------|-------------------|-----------------|
| 定義方式 | Django DB model（`Assistant`） | 工作區 Markdown 文件 |
| 啟動方式 | 用戶選擇對話時的 Assistant | 每次請求自動 embedding 路由 |
| 指令注入 | `system_instruction`（DB 欄位） | SKILL.md body 注入 system prompt |
| 程式執行 | ❌ 無 | ✅ handler.py 動態執行 |
| 知識庫關聯 | ✅ Library（Milvus 集合） | ✅ 知識庫（pgvector） |
| 多技能同時生效 | ❌（一個對話只有一個 assistant） | ✅（多技能同時觸發並注入） |

---

## 5. 工具系統比較

### 5.1 GavinAgent：完整工具系統

#### 工具基類

```python
class BaseTool(ABC):
    name: str                     # 工具識別名稱
    description: str              # LLM 顯示的說明
    approval_policy: str          # "auto" or "requires_approval"
    parameters: dict              # OpenAI function schema parameters

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult: ...

    def to_llm_schema(self) -> dict: ...   # 生成 OpenAI function definition

    # 可選覆寫（精細化審核邏輯）
    def requires_approval_for(self, args: dict) -> bool: ...
```

#### 內建工具清單

| 工具 | 檔案 | 審核政策 | 說明 |
|------|------|---------|------|
| `web_search` | `web.py` | auto | SearXNG 搜尋 |
| `web_read` | `web.py` | auto | 網頁內容抓取（trafilatura） |
| `api_get` | `api.py` | auto | HTTP GET |
| `api_post` | `api.py` | auto | HTTP POST |
| `file_read` | `file.py` | auto | 工作區文件讀取 |
| `file_write` | `file.py` | requires_approval | 工作區文件寫入（串行執行） |
| `shell_exec` | `shell.py` | requires_approval | Shell 指令執行（串行執行） |
| `chart` | `chart.py` | auto | matplotlib 圖表生成 |
| `run_skill` | `tools/` | depends | 執行 skill handler.py |

#### 執行策略

```python
_SERIAL_TOOLS = {"file_write", "shell_exec"}  # 串行（有狀態副作用）
# 其他工具：ThreadPoolExecutor 平行執行

# 平行執行：
with ThreadPoolExecutor() as executor:
    futures = {executor.submit(_run_one, tc): tc for tc in parallel_tcs}
    for future in as_completed(futures):
        result = future.result()
        tool_results.append(result)

# 串行工具：逐一執行（在平行批次完成後）
for tc in serial_tcs:
    tool_results.append(_run_one(tc))
```

---

### 5.2 llm-api：無工具調用

llm-api 完全沒有工具執行系統。RAG 檢索由服務端自動完成，LLM 只進行文字生成，不發出任何工具呼叫。

---

## 6. RAG 實作比較

| 面向 | GavinAgent | llm-api |
|------|-----------|---------|
| **向量資料庫** | PostgreSQL + pgvector | Milvus |
| **嵌入模型** | 可設定（`EMBEDDING_MODEL`，預設 `openai/text-embedding-3-small`） | Azure OpenAI `text-embd-3-lgr`（固定） |
| **觸發方式** | 每次請求自動嘗試（`_build_knowledge_section`） | 明確依 assistant 的 library 配置 |
| **文件管理** | `KnowledgeDocument`（DB model） | `Library` + `File`（DB model） |
| **搜尋策略** | 單一語意搜尋（cosine distance） | 語意（Milvus）+ BM25 文件名（fusion） |
| **多步驟 RAG** | ❌ 目前單步 | ✅ `MultiStepRAGService`（問題分解） |
| **平行 RAG** | ❌ | ✅ `ParallelRAGProcessor`（子問題平行檢索） |
| **Confluence** | ❌ | ✅ 完整整合（CQL 搜尋、HTML 清理） |
| **相似度閾值** | `RAG_SIMILARITY_THRESHOLD`（預設 0.3） | `CHUNK_SCORE_THRESHOLD`（各 assistant 設定） |
| **知識注入位置** | system prompt（`## Reference Knowledge`） | 對話歷史（fake ToolMessages） |

---

## 7. LLM 呼叫層比較

### GavinAgent：`core/llm.py`

```python
def get_completion(messages, model=None, **kwargs):
    model = model or settings.LITELLM_DEFAULT_MODEL
    response = litellm.completion(model=model, messages=messages, **kwargs)
    # 記錄 LLMUsage（model, tokens, cost）
    return response

def get_completion_stream(messages, model=None, **kwargs):
    return litellm.completion(model=model, messages=messages, stream=True, **kwargs)
```

- Provider 由 model string 前綴決定（`openai/`、`azure/`、`ollama/` 等）
- 所有呼叫統一走 `litellm.completion()`
- 每次呼叫建立 `LLMUsage` 記錄（token count + 估算成本）

### llm-api：`llm/utils/model_handler.py`

```python
class LLMHandler:
    def __init__(self, model_name, category='general'):
        config = LLM_MODEL[model_name]  # or RAG_LLM_MODEL for category='rag'
        if model_name.startswith('gpt') or model_name in ('o1',):
            self.llm = AzureChatOpenAI(deployment_name=config['model'])
        elif model_name.startswith('deepseek'):
            self.llm = AzureAIChatCompletionsModel(...)
        else:
            self.llm = ChatOllama(base_url=config['endpoint'], model=config['model'])

    def inference_stream(self, conversation):   # 主要用途
        async for chunk in self.llm.astream(conversation): yield chunk

    def inference(self, conversation):          # 非串流
        return self.llm.invoke(conversation)
```

- Provider 由 model name prefix 手動判斷
- **注意**：Azure 憑證透過 `os.environ` 設定（非線程安全）
- 模型設定**硬編碼**在 `common.py` 字典，不可動態新增
- 有 `category='rag'` vs `'general'` 兩個端點池（RAG 用不同 Azure deployment）

---

## 8. 資料庫與 ORM 模型比較

### GavinAgent 核心模型

```
Agent              ← LLM 代理設定（model, tools[], SOUL）
AgentRun           ← 每次執行紀錄（status, graph_state, input, output）
ToolExecution      ← 每次工具呼叫紀錄（tool_name, input, output, status, round）
LLMUsage           ← 每次 LLM 呼叫用量（tokens, cost）
MCPServer          ← MCP 伺服器設定（transport, command/url, env, status）
Skill              ← Skills 開關（enabled/disabled）
Memory             ← 長期記憶（embedding + content）
Conversation       ← 對話串
Message            ← 單則訊息（role, content）
```

### llm-api 核心模型

```
CustomAssistant    ← 自訂助理設定（instruction, model, library_ids）
BuiltInAssistant   ← 內建助理（外部端點設定）
Chat               ← 對話串（含 assistant FK）
Message            ← 單則訊息（含 context JSON：RAG chunks）
File               ← 上傳文件（embedding 狀態）
Library            ← RAG 文件庫（Milvus 集合）
LLMModel           ← LLM 模型清單（DB 管理，用於 UI）
LLMUsage           ← 用量記錄
```

---

## 9. 串流輸出比較

| | GavinAgent | llm-api |
|---|---|---|
| **輸出協議** | Django view 直接從 `AgentRun.output` 輪詢（或 WebSocket） | Server-Sent Events（SSE） |
| **串流機制** | `get_completion_stream()`（LiteLLM streaming iterator） | `llm_handler.llm.astream()`（LangChain async generator） |
| **think 標籤過濾** | ❌ | ✅ `_filter_stream()` 過濾 `<think>...</think>`（DeepSeek 推理軌跡） |
| **事件類型** | N/A | `event:delta`（可見文字）、`event:full`（含 think）、`event:title`、`event:complete`、`event:think`、`event:error`、`event:reference` |

---

## 10. 部署與基礎設施比較

| | GavinAgent | llm-api |
|---|---|---|
| **資料庫** | PostgreSQL 17 + pgvector | Oracle（生產）/ SQLite（本地） |
| **任務佇列** | Celery + Redis | Django Background Tasks（輕量，無 broker） |
| **向量資料庫** | pgvector（內嵌於 PostgreSQL） | Milvus（獨立服務，port 19530） |
| **搜尋引擎** | SearXNG（自託管） | N/A |
| **設定管理** | `python-decouple` + `.env` | `django.environ` + `APP_ENV_NAME` 環境切換 |
| **CI/CD** | 無（僅 docker-compose） | Jenkins + Docker |

---

## 11. 核心設計哲學差異

| 面向 | GavinAgent | llm-api |
|------|-----------|---------|
| **知識擴充** | 檔案式工作區（AGENTS.md, SOUL.md, skills/, workspace/） | DB-driven（Assistant DB 設定 + Library DB） |
| **工具擴充** | MCP 協定（外部進程）+ 內建工具 + Skills handler | 無工具擴充機制 |
| **LLM 彈性** | 任何 LiteLLM 支援的 provider（前綴路由） | 手動 if/elif provider 切換（需改程式碼） |
| **可觀測性** | loop_trace + context_trace（每輪決策 JSON 紀錄） | 無（僅 ElasticAPM metrics） |
| **狀態持久化** | `AgentRun.graph_state`（完整恢復 WAITING 狀態） | 無狀態（每次請求獨立） |
| **人機協作** | 工具審核暫停機制（WAITING → RUNNING） | 無 |
| **多租戶** | 多 Agent per user，各自工具/模型設定 | 多 Assistant，各自 library/instruction |

---

## 12. 總結

**GavinAgent** 是一個完整的**自主 Agent 平台**，核心是可暫停/恢復的多輪 ReAct 循環，MCP 協定讓工具擴充無需改動核心程式碼，Skills 系統讓 prompt engineering 以版本控制友好的 Markdown 文件管理。

**llm-api** 是一個成熟的**企業 RAG 聊天平台**，專注於多源文件（PDF/Word/Excel/PPT）的準確檢索與生成，Multi-step RAG 和平行檢索是其差異化優勢，但 LLM 本身只做單次生成，沒有自主行動能力。

兩者**互補不競爭**：llm-api 的多步驟 RAG pipeline（Milvus + BM25 + 問題分解 + 平行檢索）可以作為 GavinAgent 的一個 MCP server 或 Skill，讓 GavinAgent 在需要深度文件檢索時調用 llm-api 的能力。
