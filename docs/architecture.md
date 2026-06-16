# SuperBizAgent 架构文档

## 概述

SuperBizAgent 是一个企业级智能对话与 AIOps 运维助手系统，基于 **FastAPI + LangChain + LangGraph** 构建，使用阿里云 DashScope（通义千问 Qwen）作为 LLM 后端。

- **版本**: 1.2.1
- **Python**: ≥3.11
- **LLM**: DashScope Qwen-Max
- **向量数据库**: Milvus (1024 维 L2 索引)
- **Embedding**: DashScope text-embedding-v4

---

## 系统架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Frontend (static/)                           │
│                  HTML/CSS/JS - Material Design UI                    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ HTTP/SSE
┌──────────────────────────────▼───────────────────────────────────────┐
│                     FastAPI App (app/main.py)                        │
│                      Port 9900, CORS enabled                         │
├──────────────────────────────────────────────────────────────────────┤
│   API Routes (app/api/)                                              │
│   ├── /api/chat, /api/chat_stream  — RAG 对话                       │
│   ├── /api/chat/session/{id}       — 会话管理                       │
│   ├── /api/chat/clear              — 清除会话                       │
│   ├── /api/aiops                   — AIOps SSE 故障诊断             │
│   ├── /api/upload                  — 文件上传入库                   │
│   ├── /api/index_directory         — 目录批量入库                   │
│   └── /health                      — 健康检查                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                    Service Layer (app/services/)                      │
│   ├── rag_agent_service.py       — RAG Agent (LangGraph + ChatQwen) │
│   ├── aiops_service.py           — Plan-Execute-Replan 工作流       │
│   ├── vector_store_manager.py    — Milvus VectorStore 封装          │
│   ├── vector_embedding_service.py — DashScope Embedding             │
│   ├── vector_index_service.py    — 文件向量化入库                   │
│   ├── vector_search_service.py   — 向量相似度检索                   │
│   └── document_splitter_service.py — 文档分块（三段式）            │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                     Agent Layer (app/agent/)                          │
│   ├── mcp_client.py              — MCP 客户端单例                   │
│   └── aiops/                     — Plan-Execute-Replan 核心         │
│       ├── state.py               — PlanExecuteState TypedDict       │
│       ├── planner.py             — 规划器（知识检索 + 结构化计划）  │
│       ├── executor.py            — 执行器（LLM + ToolNode）         │
│       └── replanner.py           — 重规划器（继续/重规划/响应）     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                     Tools Layer (app/tools/)                          │
│   ├── knowledge_tool.py           — retrieve_knowledge (Milvus)     │
│   ├── time_tool.py                — get_current_time                │
│   └── query_metrics_alerts.py     — query_prometheus_alerts         │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                   Core Infrastructure (app/core/)                     │
│   ├── llm_factory.py              — ChatOpenAI / ChatQwen 工厂      │
│   └── milvus_client.py            — Milvus 连接管理器               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 数据流

### RAG 对话流

```
User Message
    → POST /api/chat_stream
    → RagAgentService.query_stream()
    → LangGraph create_agent(ChatQwen + tools)
    → Agent 决定是否调用 retrieve_knowledge
        ├── 是 → Milvus 向量检索 → 返回知识上下文
        └── 否 → 直接回答
    → SSE 流式返回 Token
```

### AIOps 故障诊断流

```
Alert Input
    → POST /api/aiops (SSE)
    → AIOpsService.diagnose()
    → Planner: 检索知识库 SOP → 生成诊断计划 (4-6 步)
    → Executor: 执行计划步骤 → 调用工具 (Prometheus/MCP)
    → Replanner: 评估执行结果 → 决定继续/重规划/生成报告
    → 循环至最终报告 → SSE 流式输出
```

---

## 关键设计模式

### 1. 单例模式 (Singleton)

所有 Service、Manager、Client 均为模块级全局单例，在 import 时初始化：

```python
# 示例：app/services/rag_agent_service.py
rag_agent_service = RagAgentService()
```

### 2. Plan-Execute-Replan (LangGraph StateGraph)

AIOps 诊断采用三节点循环状态机：

```
     ┌──────────┐     ┌──────────┐     ┌───────────┐
     │ Planner  │────→│ Executor │────→│ Replanner │
     └──────────┘     └──────────┘     └─────┬─────┘
                                              │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                          continue          replan          respond
                              │                │                │
                              └────────────────┘                │
                                     │                         │
                                     ▼                         ▼
                                  Executor               Final Report
```

### 3. RAG (检索增强生成)

文档处理管道：

```
上传文件 → 读取内容 → 三段式分块 → DashScope Embedding → Milvus 存储
                                                                │
查询时：用户问题 → Embedding → Milvus 相似搜索 → Top-K 上下文 → LLM 生成
```

### 4. MCP 集成

```
Main App                                    MCP Servers
┌──────────────┐     streamable-http       ┌─────────────────┐
│ mcp_client.py│←─────────────────────────→│ cls_server.py   │
│ (MultiServer │                           │ (日志查询, 8003) │
│  MCPClient)  │←─────────────────────────→│                 │
│              │     streamable-http       │monitor_server.py│
│              │                           │ (监控数据, 8004) │
└──────────────┘                           └─────────────────┘
```

---

## 目录结构

```
super_biz_agent_py/
├── app/                    # 核心应用
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # Pydantic Settings 配置
│   ├── api/                # API 路由层
│   ├── services/           # 业务服务层
│   ├── agent/              # Agent 模块
│   │   └── aiops/          # Plan-Execute-Replan 核心
│   ├── models/             # Pydantic 数据模型
│   ├── tools/              # LangChain 工具
│   ├── core/               # 基础设施
│   └── utils/              # 工具函数
├── mcp_servers/            # MCP 工具服务器
├── static/                 # 前端资源
├── aiops-docs/             # 知识库文档
├── docs/                   # 项目文档
├── tests/                  # 测试
├── pyproject.toml          # 项目配置与依赖
├── Makefile                # Linux/macOS 命令
├── start-windows.bat       # Windows 启动脚本
└── vector-database.yml     # Milvus Docker Compose
```

---

## 配置说明

所有配置通过 `.env` 文件管理，由 `app/config.py` 中的 `Settings` 类加载：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DASHSCOPE_API_KEY` | — | DashScope API Key（必填） |
| `DASHSCOPE_MODEL` | `qwen-max` | LLM 模型 |
| `MILVUS_HOST` | `localhost` | Milvus 地址 |
| `MILVUS_PORT` | `19530` | Milvus 端口 |
| `RAG_TOP_K` | `3` | RAG 检索返回数 |
| `CHUNK_MAX_SIZE` | `800` | 文档分块最大长度 |
| `MCP_CLS_URL` | `http://localhost:8003/mcp` | CLS MCP 服务地址 |
| `MCP_MONITOR_URL` | `http://localhost:8004/mcp` | Monitor MCP 服务地址 |
| `PROMETHEUS_BASE_URL` | `http://127.0.0.1:9090` | Prometheus 地址 |

---

## 开发指南

### 启动服务

**Windows:**
```batch
start-windows.bat
```

**Linux/macOS:**
```bash
make init    # 首次初始化
make start   # 启动所有服务
make dev     # 开发模式
```

---

## 多 Agent 协作机制

SuperBizAgent 不是一个单一的 Agent，而是由 **多个专业化 Agent 通过共享状态、共享工具、共享基础设施** 协作完成任务的 Agent 系统。

### Agent 全景图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SuperBizAgent Agent 系统                          │
│                                                                          │
│  ┌──────────────────────────────┐    ┌──────────────────────────────┐   │
│  │     RAG Agent (问答Agent)     │    │   AIOps Agent (诊断Agent)     │   │
│  │  rag_agent_service.py        │    │  aiops_service.py             │   │
│  │                              │    │                               │   │
│  │  LangGraph create_agent()    │    │  LangGraph StateGraph         │   │
│  │  单一 ReAct Agent            │    │  三节点协作 Agent 系统         │   │
│  │  + ChatQwen                  │    │  ┌─────────────────────┐      │   │
│  │  + 工具自主决策调用           │    │  │   Planner Agent     │      │   │
│  └──────────────┬───────────────┘    │  │   规划 Agent         │      │   │
│                 │                    │  │   + ChatQwen(t=0)   │      │   │
│                 │                    │  │   + 知识库检索       │      │   │
│                 │                    │  └─────────┬───────────┘      │   │
│                 │                    │            │  plan            │   │
│                 │                    │            ▼                  │   │
│                 │                    │  ┌─────────────────────┐      │   │
│                 │                    │  │   Executor Agent    │      │   │
│                 │                    │  │   执行 Agent         │      │   │
│                 │                    │  │   + ChatQwen(t=0)   │      │   │
│                 │                    │  │   + ToolNode        │      │   │
│                 │                    │  └─────────┬───────────┘      │   │
│                 │                    │            │ past_steps       │   │
│                 │                    │            ▼                  │   │
│                 │                    │  ┌─────────────────────┐      │   │
│                 │                    │  │   Replanner Agent   │      │   │
│                 │                    │  │   决策 Agent         │      │   │
│                 │                    │  │   + ChatQwen(t=0)   │      │   │
│                 │                    │  └─────────┬───────────┘      │   │
│                 │                    │            │                  │   │
│                 │                    │   continue │ replan │ respond │   │
│                 │                    └────────────┼───────┼─────────┘   │
│                 │                                 │       │             │
│                 │                                 ▼       ▼             │
│                 │                            Executor   END            │
│                 │                             (循环)   (最终报告)       │
└─────────────────┼──────────────────────────────────────────────────────┘
                  │
                  │  共享层
                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         共享基础设施层                                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │ 工具集        │ │ MCP 客户端   │ │ LLM 后端     │ │ 知识库       │   │
│  │ DEFAULT_      │ │ _mcp_client  │ │ ChatQwen     │ │ Milvus       │   │
│  │ LOCAL_AGENT_  │ │ (全局单例)   │ │ qwen-max     │ │ biz          │   │
│  │ TOOLS         │ │              │ │              │ │ collection   │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Agent 间六大连接方式

#### 连接 1：共享状态 (Shared State) —— AIOps 内部 Agent 间通信

AIOps 的三个 Agent (Planner / Executor / Replanner) **不直接互相调用**，而是通过 **LangGraph StateGraph 的共享状态**实现通信。这是多 Agent 协作最核心的机制。

**状态载体**：[app/agent/aiops/state.py](app/agent/aiops/state.py)

```python
class PlanExecuteState(TypedDict):
    input: str                              # 用户任务
    plan: List[str]                         # 待执行步骤
    past_steps: Annotated[List[tuple], operator.add]  # 已执行步骤 (追加式)
    response: str                           # 最终报告
```

**数据流转**：

```
                     PlanExecuteState (共享状态)
┌──────────────────────────────────────────────────────────────────┐
│                                                                    │
│  input: "诊断当前系统告警"                                         │
│  plan:  ["步骤1", "步骤2", ...]  ←── 不断弹出/替换               │
│  past_steps: [(step1, result1), ...] ←── operator.add 追加       │
│  response: ""  ←── Replanner 最终写入                             │
│                                                                    │
└────────────┬──────────────┬──────────────┬────────────────────────┘
             │              │              │
             ▼              ▼              ▼
        ┌─────────┐   ┌─────────┐   ┌──────────┐
        │ Planner │   │Executor │   │Replanner │
        └─────────┘   └─────────┘   └──────────┘
```

| 状态字段 | 写入方 | 读取方 | 写入方式 |
|----------|--------|--------|----------|
| `input` | 外部调用者 (`aiops_service`) | Planner, Replanner | 初始化时设置 |
| `plan` | **Planner** (初始填充), **Replanner** (replan 时替换) | Executor, Replanner | 完全替换 (`return {"plan": [...]}`) |
| `past_steps` | **Executor** (每次执行后追加) | Replanner | `operator.add` 追加式 (`return {"past_steps": [(task, result)]}`) |
| `response` | **Replanner** (respond 时写入) | 外部调用者 (`aiops_service`) | 完全替换 (`return {"response": "..."}`) |

**关键设计细节**：

- `past_steps` 使用 `operator.add` 而非覆盖，意味着每次 Executor 返回的 `past_steps` 会自动**追加**到已有列表末尾，而非替换
- `plan` 使用默认的覆盖策略，Planner 写入初始计划，Replanner `replan` 时用新计划**替换**旧计划
- StateGraph 的边定义了 Agent 之间允许的通信路径：Planner → Executor → Replanner → (Executor | END)

#### 连接 2：共享工具集 (Shared Tools) —— 跨 Agent 系统

```python
# app/tools/__init__.py
DEFAULT_LOCAL_AGENT_TOOLS = (
    retrieve_knowledge,         # Milvus 知识检索
    get_current_time,           # 时间查询
    query_prometheus_alerts,    # Prometheus 告警
)
```

**使用矩阵**：

| 工具 | RAG Agent | AIOps Planner | AIOps Executor | 调用方式 |
|------|-----------|---------------|----------------|----------|
| `retrieve_knowledge` | ✅ 由 LLM 自主决定调用 | ✅ 直接 `ainvoke` 调用 (非 LLM 决策) | ✅ 由 LLM 自主决定调用 | Planner 直接调用，其他通过 bind_tools |
| `get_current_time` | ✅ 由 LLM 自主决定调用 | ❌ | ✅ 由 LLM 自主决定调用 | bind_tools |
| `query_prometheus_alerts` | ✅ 由 LLM 自主决定调用 | ❌ | ✅ 由 LLM 自主决定调用 | bind_tools |

**关键区别**：Planner 对 `retrieve_knowledge` 的使用是 **编程式直接调用**（`await retrieve_knowledge.ainvoke({"query": input_text})`），这是因为 Planner 必须在生成计划**之前**获取 SOP 经验，而非让 LLM 决定要不要查。

#### 连接 3：共享 MCP 客户端 (Shared MCP Client) —— 跨 Agent 的工具扩展

```python
# app/agent/mcp_client.py
_mcp_client: Optional[MultiServerMCPClient] = None  # 全局单例
```

**调用链**：

```
get_mcp_client_with_retry()  ←── 四个 Agent 都调用此函数
│
├── RagAgentService._initialize_agent()
│   └── 加载 MCP 工具到 create_agent 的工具列表
│
├── Planner (planner.py)
│   └── 获取 MCP 工具描述，帮助制定计划（不实际调用）
│
├── Executor (executor.py)
│   └── 通过 bind_tools + ToolNode 实际调用 MCP 工具
│
└── Replanner (replanner.py)
    └── 获取 MCP 工具描述，评估剩余步骤可行性
```

**单例保证**：四个 Agent 获取的是**同一个** `MultiServerMCPClient` 实例，这意味着：

1. MCP 连接（cls_server:8003, monitor_server:8004）只建立一次
2. 重试拦截器 (`retry_interceptor`) 全局生效
3. 工具缓存共享，避免重复加载

#### 连接 4：共享 LLM 后端 (Shared LLM Backend)

所有 Agent 使用相同的 LLM 配置：

```python
# 均使用
ChatQwen(
    model=config.rag_model,       # "qwen-max"
    api_key=config.dashscope_api_key,
    temperature=0                  # Planner/Executor/Replanner 使用 0
)
```

| Agent | temperature | streaming | 说明 |
|-------|-------------|-----------|------|
| RAG Agent | 0.7 | True | 对话需要一定创造性 |
| Planner | 0 | False | 计划需要确定性 |
| Executor | 0 | False | 工具调用需要精确 |
| Replanner | 0 | False | 决策需要确定性 |

#### 连接 5：共享知识库 (Shared Knowledge Base)

所有 Agent 通过**同一套 RAG 管道**检索知识：

```
                    ┌─────────────────────┐
                    │   Milvus (biz)       │
                    │   1024维 L2 索引     │
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                  │
            ▼                  ▼                  ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │ RAG Agent    │   │ Planner      │   │ Executor     │
   │ (Q&A 场景)   │   │ (SOP 检索)   │   │ (必要时检索) │
   │              │   │              │   │              │
   │ "向量数据库   │   │ "CPU高使用率  │   │ 工具执行中    │
   │  是什么？"   │   │  排查SOP"    │   │ 可能需要查    │
   └──────────────┘   └──────────────┘   └──────────────┘
```

**检索路径**：

```
retrieve_knowledge(query)
  → vector_store_manager.get_vector_store()
    → Milvus.as_retriever(k=rag_top_k)
      → Milvus similarity_search
        → DashScopeEmbeddings.embed_query(query)
          → Milvus ANN search (L2 距离)
            → 返回 Top-K 文档
```

#### 连接 6：LangGraph StateGraph 编排 (Orchestration)

AIOps 内部的 Agent 协作由 **LangGraph StateGraph** 统一编排：

```python
# app/services/aiops_service.py
workflow = StateGraph(PlanExecuteState)

workflow.add_node("planner", planner)        # Agent 1
workflow.add_node("executor", executor)      # Agent 2
workflow.add_node("replanner", replanner)    # Agent 3

workflow.set_entry_point("planner")          # 入口: Planner

workflow.add_edge("planner", "executor")     # 固定边: Planner → Executor
workflow.add_edge("executor", "replanner")   # 固定边: Executor → Replanner

# 条件边: Replanner → Executor (循环) 或 END (终止)
workflow.add_conditional_edges(
    "replanner",
    should_continue,                          # 路由函数
    {"executor": "executor", END: END}
)
```

**完整执行时序**：

```
时间 →
──────────────────────────────────────────────────────────────────────

Step 1: Planner Agent 执行
  ├── 读取 state.input = "诊断当前系统告警"
  ├── 调用 retrieve_knowledge.ainvoke("诊断当前系统告警")  → 获取 SOP
  ├── 调用 get_mcp_client_with_retry().get_tools()         → 获取工具列表
  ├── ChatQwen + planner_prompt + structured_output(Plan)
  ├── 写入 state.plan = [步骤1, 步骤2, 步骤3, 步骤4]
  └── 输出 {"plan": [...]}

Step 2: StateGraph 沿固定边 Planner → Executor

Step 3: Executor Agent 执行
  ├── 读取 state.plan[0] = 步骤1
  ├── ChatQwen.bind_tools(all_tools)
  ├── LLM 决定调用 query_prometheus_alerts()
  ├── ToolNode 执行工具 → HTTP GET Prometheus API
  ├── LLM 基于工具结果生成回答
  ├── 写入 state.plan = [步骤2, 步骤3, 步骤4]  (弹出步骤1)
  ├── 写入 state.past_steps = [(步骤1, 结果1)] (追加)
  └── 输出 {"plan": [...], "past_steps": [...]}

Step 4: StateGraph 沿固定边 Executor → Replanner

Step 5: Replanner Agent 执行
  ├── 读取 state.past_steps, state.plan
  ├── 检查: len(past_steps) >= 8? → 否, 继续
  ├── ChatQwen + replanner_prompt + structured_output(Act)
  ├── 决策: action = "continue"
  └── 输出 {}  (状态不变)

Step 6: should_continue() 评估
  ├── state.response 为空? → 是
  ├── state.plan 非空? → 是
  └── 返回 "executor"

Step 7: 循环回 Executor... (重复 Step 3-6)
  ...
  直到 Replanner 决策 = "respond" 或 plan 为空

Step N: Replanner 生成最终报告
  ├── _generate_response(state, llm)
  ├── response_prompt + structured_output(Response)
  └── 写入 state.response = "# 诊断报告\n..."

Step N+1: should_continue()
  ├── state.response 非空? → 是
  └── 返回 END

Step N+2: aiops_service 读取 final_state.values["response"]
  └── SSE 流式输出最终报告
```

### Agent 依赖关系图（按文件）

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent 文件依赖关系                              │
│                                                                        │
│  app/services/rag_agent_service.py                                    │
│  ├── RagAgentService                                                  │
│  │   ├── 使用: create_agent(ChatQwen, tools, checkpointer)            │
│  │   ├── 依赖: app/tools/__init__.py → DEFAULT_LOCAL_AGENT_TOOLS     │
│  │   ├── 依赖: app/agent/mcp_client.py → get_mcp_client_with_retry() │
│  │   └── 依赖: app/config.py → config.rag_model                      │
│  │                                                                     │
│  app/services/aiops_service.py                                        │
│  ├── AIOpsService                                                     │
│  │   ├── 构建: StateGraph(PlanExecuteState)                           │
│  │   └── 编排: planner → executor → replanner 循环                    │
│  │                                                                     │
│  app/agent/aiops/planner.py                                           │
│  ├── planner(state) → Dict                                            │
│  │   ├── 直接调用: retrieve_knowledge.ainvoke()                       │
│  │   │   └── 依赖: app/tools/knowledge_tool.py                       │
│  │   ├── 依赖: app/agent/mcp_client.py → get_mcp_client_with_retry() │
│  │   ├── 依赖: app/tools/__init__.py → DEFAULT_LOCAL_AGENT_TOOLS     │
│  │   ├── 依赖: .utils.py → format_tools_description()                │
│  │   └── 使用: ChatQwen + planner_prompt + structured_output(Plan)   │
│  │                                                                     │
│  app/agent/aiops/executor.py                                          │
│  ├── executor(state) → Dict                                           │
│  │   ├── 依赖: app/agent/mcp_client.py → get_mcp_client_with_retry() │
│  │   ├── 依赖: app/tools/__init__.py → DEFAULT_LOCAL_AGENT_TOOLS     │
│  │   ├── 使用: ChatQwen.bind_tools() + ToolNode                      │
│  │   └── 返回: {"plan": plan[1:], "past_steps": [(task, result)]}   │
│  │                                                                     │
│  app/agent/aiops/replanner.py                                         │
│  ├── replanner(state) → Dict                                          │
│  │   ├── 依赖: app/agent/mcp_client.py → get_mcp_client_with_retry() │
│  │   ├── 依赖: app/tools/__init__.py → DEFAULT_LOCAL_AGENT_TOOLS     │
│  │   ├── 依赖: .utils.py → format_tools_description()                │
│  │   ├── 使用: ChatQwen + replanner_prompt + structured_output(Act)  │
│  │   └── 使用: ChatQwen + response_prompt + structured_output(Response)│
│  │                                                                     │
│  app/agent/mcp_client.py                                              │
│  ├── _mcp_client (全局单例)                                           │
│  ├── get_mcp_client_with_retry() → MultiServerMCPClient              │
│  ├── load_mcp_tools_safe() → list[BaseTool]                          │
│  ├── retry_interceptor() → 指数退避重试                               │
│  └── 依赖: app/config.py → config.mcp_servers                        │
│                                                                        │
│  app/tools/__init__.py                                                │
│  ├── DEFAULT_LOCAL_AGENT_TOOLS                                       │
│  │   ├── retrieve_knowledge  (knowledge_tool.py)                      │
│  │   ├── get_current_time    (time_tool.py)                           │
│  │   └── query_prometheus_alerts (query_metrics_alerts.py)           │
│  └── 被引用方: rag_agent_service, planner, executor, replanner       │
└──────────────────────────────────────────────────────────────────────┘
```

### 关键设计总结

| 设计原则 | 实现方式 | 效果 |
|----------|----------|------|
| **专业化分工** | Planner / Executor / Replanner 各有独立 Prompt 和职责 | 每个 Agent 只做自己擅长的事 |
| **松耦合通信** | 通过 Shared State (TypedDict) 而非直接函数调用 | Agent 可独立修改和测试 |
| **集中编排** | LangGraph StateGraph 管理节点和边 | 流程可视化、可追踪 |
| **资源共享** | 全局单例 (MCP, Tools, LLM, Milvus) | 避免重复初始化，节省资源 |
| **优雅降级** | `load_mcp_tools_safe` 失败时返回空列表 | MCP 不可用时系统仍可运行 |
| **安全护栏** | max 8 steps, ≥5 steps 禁 replan | 防止无限循环 |

---

### 运行测试

```bash
pytest tests/ -v
pytest tests/ -v --cov=app --cov-report=html  # 含覆盖率报告
```

### 代码质量

```bash
make lint      # ruff 检查
make format    # black + isort 格式化
make typecheck # mypy 类型检查
```
