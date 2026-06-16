# SuperBizAgent 项目深度分析 & Harness 改造方案

> 撰写日期: 2026-06-15 | 版本: 1.2.1

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈与依赖](#2-技术栈与依赖)
3. [架构分层详解](#3-架构分层详解)
4. [完整流程分析：用户输入一条问题经历了什么](#4-完整流程分析用户输入一条问题经历了什么)
5. [文件调用链矩阵](#5-文件调用链矩阵)
6. [关键设计模式与亮点](#6-关键设计模式与亮点)
7. [Harness 改造方案](#7-harness-改造方案)
8. [实施路线图](#8-实施路线图)

---

## 1. 项目概述

**SuperBizAgent** 是一个企业级智能 OnCall 运维助手系统，提供两大核心能力：

| 能力 | 说明 | 技术实现 |
|------|------|----------|
| 🤖 **RAG 智能对话** | 基于知识库的问答，支持文档上传自动入库 | LangGraph Agent + Milvus 向量检索 |
| 🔧 **AIOps 故障诊断** | 自动拉取告警 → 制定诊断计划 → 执行排查 → 生成报告 | Plan-Execute-Replan 状态机 |

**一句话定位**：用 LLM + 向量知识库 + MCP 工具协议，打造一个能自主排查生产故障的 AI 运维助手。

---

## 2. 技术栈与依赖

```
┌─────────────────────────────────────────────────────────────┐
│  层级            │  技术选型                                 │
├─────────────────────────────────────────────────────────────┤
│  Web 框架        │  FastAPI + SSE (sse-starlette)           │
│  LLM 框架        │  LangChain + LangGraph + LangChain-MCP   │
│  LLM 模型        │  阿里云 DashScope Qwen-Max (OpenAI 兼容) │
│  Embedding       │  DashScope text-embedding-v4 (1024维)    │
│  向量数据库       │  Milvus (L2 距离, IVF_FLAT 索引)        │
│  工具协议         │  MCP (Model Context Protocol)            │
│  MCP 实现        │  FastMCP + langchain-mcp-adapters        │
│  前端            │  原生 HTML/CSS/JS + Marked.js + Highlight.js│
│  日志            │  Loguru (按天轮转, 自动压缩)              │
│  配置管理         │  Pydantic Settings (.env)                │
│  包管理           │  uv + pyproject.toml                     │
│  容器化           │  Docker Compose (Milvus)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 架构分层详解

### 3.1 完整分层架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     🖥️  Frontend (static/)                      │
│          index.html  ←→  app.js  ←→  styles.css                 │
│   模式: 快速问答 / 流式对话 / AIOps诊断 / 文件上传              │
└──────────────────────────┬──────────────────────────────────────┘
                           │  HTTP REST + SSE (EventSource)
┌──────────────────────────▼──────────────────────────────────────┐
│                  🌐  FastAPI App (app/main.py)                   │
│                   Port 9900, CORS enabled                        │
│  lifespan: 启动时连接Milvus → 关闭时断开连接                     │
├──────────────────────────────────────────────────────────────────┤
│  📡 API 路由层 (app/api/)                                        │
│  ┌─────────────────┬──────────────────┬──────────────────────┐  │
│  │ chat.py          │ aiops.py          │ file.py              │  │
│  │ POST /api/chat   │ POST /api/aiops   │ POST /api/upload     │  │
│  │ POST /api/chat   │ (SSE 流式)        │ POST /api/index      │  │
│  │      _stream     │                   │      _directory      │  │
│  │ POST /chat/clear │                   │                      │  │
│  │ GET  /chat/      │                   │                      │  │
│  │      session/{id}│                   │                      │  │
│  ├─────────────────┴──────────────────┴──────────────────────┤  │
│  │ health.py: GET /health (Milvus连接检查)                    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                 🧠 Service 业务层 (app/services/)                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ rag_agent_service.py                                     │   │
│  │  - RagAgentService (单例, streaming=True)                │   │
│  │  - LangGraph create_agent(ChatQwen + tools)              │   │
│  │  - MemorySaver checkpointer (会话持久化)                 │   │
│  │  - query() 非流式 / query_stream() SSE流式               │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ aiops_service.py                                         │   │
│  │  - AIOpsService (单例)                                   │   │
│  │  - 构建 StateGraph: Planner → Executor → Replanner       │   │
│  │  - execute() 通用 / diagnose() AIOps专用                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 向量服务链:                                               │   │
│  │  document_splitter_service.py → vector_index_service.py  │   │
│  │       → vector_store_manager.py ← vector_embedding_service│   │
│  │       → vector_search_service.py                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                🤖 Agent 决策层 (app/agent/)                      │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ mcp_client.py                                          │    │
│  │  - MultiServerMCPClient 单例                           │    │
│  │  - retry_interceptor (指数退避, 最多3次)               │    │
│  │  - load_mcp_tools_safe (优雅降级)                      │    │
│  │  - 连接: cls_server(8003) + monitor_server(8004)       │    │
│  └────────────────────────────────────────────────────────┘    │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ aiops/  Plan-Execute-Replan 核心                       │    │
│  │  ├── state.py        PlanExecuteState TypedDict        │    │
│  │  ├── planner.py      制定诊断计划 (知识库SOP + LLM)    │    │
│  │  ├── executor.py     执行单个步骤 (LLM + ToolNode)     │    │
│  │  ├── replanner.py    决策: continue/replan/respond     │    │
│  │  └── utils.py        工具描述格式化                    │    │
│  └────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                  🔧 Tools 工具层 (app/tools/)                    │
│  ┌──────────────────┬──────────────────┬──────────────────────┐ │
│  │ knowledge_tool.py │ time_tool.py     │ query_metrics_      │ │
│  │ retrieve_knowledge│ get_current_time │   alerts.py          │ │
│  │ (Milvus 检索)     │ (时区感知)       │ query_prometheus_   │ │
│  │                   │                  │   alerts (HTTP API) │ │
│  └──────────────────┴──────────────────┴──────────────────────┘ │
│  DEFAULT_LOCAL_AGENT_TOOLS = (以上三个)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                ⚙️  Core 基础设施 (app/core/)                    │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ llm_factory.py      LLMFactory.create_chat_model()     │    │
│  │                     ChatOpenAI (OpenAI 兼容模式)        │    │
│  │                     支持 DashScope/OpenAI/Azure 等      │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ milvus_client.py     MilvusClientManager (单例)        │    │
│  │                     connect / health_check / close      │    │
│  │                     biz collection (1024维, L2索引)     │    │
│  └────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                  🔌 MCP Servers (mcp_servers/)                   │
│  ┌────────────────────────┐  ┌────────────────────────────┐    │
│  │ cls_server.py (8003)    │  │ monitor_server.py (8004)   │    │
│  │ FastMCP("CLS")          │  │ FastMCP("Monitor")         │    │
│  │ - get_current_timestamp │  │ - query_cpu_metrics        │    │
│  │ - search_topic_by_svc   │  │ - query_memory_metrics     │    │
│  │ - search_log            │  │                            │    │
│  │ - get_region_code       │  │ (均为 Mock 数据)           │    │
│  │ - get_topic_info        │  │                            │    │
│  └────────────────────────┘  └────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 完整流程分析：用户输入一条问题经历了什么

### 4.1 场景一：快速问答（非流式）

```
用户输入 "什么是向量数据库？"
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 前端 (static/app.js)                                         │
│    SuperBizAgentApp.sendMessage()                                │
│    → sendQuickMessage("什么是向量数据库？")                       │
│    → POST http://localhost:9900/api/chat                         │
│    → Body: {"Id":"session_xxx","Question":"什么是向量数据库？"}    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. API 路由 (app/api/chat.py:18)                                │
│    @router.post("/chat")                                         │
│    async def chat(request: ChatRequest):                         │
│    → 解析 ChatRequest (Id → id, Question → question)            │
│    → logger.info("收到快速对话请求")                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. RAG Agent 服务 (app/services/rag_agent_service.py:186)       │
│    rag_agent_service.query(question, session_id)                 │
│    ├── await _initialize_agent()                                 │
│    │   ├── 获取 MCP 工具: get_mcp_client_with_retry()            │
│    │   │   └── (app/agent/mcp_client.py:158)                    │
│    │   │       ├── 构建 retry_interceptor                        │
│    │   │       └── MultiServerMCPClient({cls, monitor})          │
│    │   ├── load_mcp_tools_safe(client)                           │
│    │   ├── all_tools = DEFAULT_LOCAL_AGENT_TOOLS + mcp_tools     │
│    │   └── create_agent(model, tools, checkpointer)              │
│    │       └── (LangGraph prebuilt agent)                        │
│    ├── 构建消息: [SystemMessage(prompt), HumanMessage(question)] │
│    ├── agent.ainvoke(input, config={thread_id: session_id})      │
│    │   └── LangGraph Agent 内部循环:                             │
│    │       ├── LLM 决定是否调用工具                              │
│    │       │   ├── 调用 retrieve_knowledge                       │
│    │       │   │   └── (app/tools/knowledge_tool.py:14)         │
│    │       │   │       ├── vector_store_manager.get_vector_store │
│    │       │   │       │   └── (app/services/vector_store_       │
│    │       │   │       │        manager.py:123)                  │
│    │       │   │       ├── as_retriever(k=top_k)                 │
│    │       │   │       ├── retriever.invoke(query)               │
│    │       │   │       │   └── Milvus 相似度搜索                 │
│    │       │   │       └── format_docs(docs) → 格式化上下文      │
│    │       │   ├── 调用 get_current_time                         │
│    │       │   │   └── (app/tools/time_tool.py:11)              │
│    │       │   └── 调用 query_prometheus_alerts                  │
│    │       │       └── (app/tools/query_metrics_alerts.py:158)  │
│    │       └── LLM 基于工具结果生成最终回答                      │
│    └── 返回 answer (string)                                      │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. API 层组装响应 (app/api/chat.py:46)                           │
│    return {                                                       │
│        "code": 200,                                               │
│        "data": {"success": True, "answer": answer}                │
│    }                                                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. 前端展示 (static/app.js:679)                                  │
│    sendQuickMessage() 收到响应                                   │
│    → 移除 "正在思考..." 加载动画                                 │
│    → addMessage('assistant', answer)                             │
│    → renderMarkdown(answer) → marked.parse()                     │
│    → highlightCodeBlocks() → hljs.highlightElement()             │
│    → scrollToBottom()                                            │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 场景二：流式对话（SSE）

```
用户输入 "帮我查一下最近的系统告警"
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 前端 (static/app.js:738)                                     │
│    sendStreamMessage(message)                                    │
│    → POST http://localhost:9900/api/chat_stream                  │
│    → 用 ReadableStream reader 读取 SSE 流                        │
│    → 逐行解析: event: / data:                                    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. API 路由 (app/api/chat.py:69)                                │
│    @router.post("/chat_stream")                                  │
│    async def chat_stream(request):                               │
│    → 返回 EventSourceResponse(event_generator())                 │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. RAG Agent 流式服务                                            │
│    rag_agent_service.query_stream(question, session_id)          │
│    (app/services/rag_agent_service.py:251)                       │
│    ├── await _initialize_agent()  (同上)                         │
│    ├── agent.astream(input, config, stream_mode="messages")      │
│    │   └── LangGraph Agent 流式执行:                             │
│    │       ├── LLM 流式生成 token                                │
│    │       ├── 遇到 tool_call → 暂停生成, 执行工具               │
│    │       └── 工具结果注入上下文, 继续流式生成                   │
│    └── yield {"type": "content", "data": token}                  │
│        yield {"type": "tool_call", "data": {...}}                │
│        yield {"type": "complete"}                                │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. API 层 SSE 封装 (app/api/chat.py:95)                          │
│    event_generator() 逐条转换:                                    │
│    chunk_type == "content"    → yield {"event":"message",        │
│                                        "data": JSON}              │
│    chunk_type == "tool_call"  → yield tool_call 事件              │
│    chunk_type == "complete"   → yield done 事件                   │
│    → EventSourceResponse 自动格式化为 SSE                         │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. 前端 SSE 解析 (static/app.js:760)                             │
│    reader.read() 循环:                                            │
│    ├── 逐行解析 SSE 协议 (event:/data:/id:)                      │
│    ├── 解析 data JSON → 判断 type                                │
│    │   ├── type=content → 拼接到 fullResponse                    │
│    │   │   → 实时 renderMarkdown(fullResponse)                   │
│    │   ├── type=tool_call → (忽略或显示工具状态)                  │
│    │   ├── type=done → handleStreamComplete()                    │
│    │   │   → 最终 Markdown 渲染 + 代码高亮                        │
│    │   │   → 保存到 currentChatHistory                           │
│    │   └── type=error → 显示错误                                  │
│    └── scrollToBottom()                                          │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 场景三：AIOps 智能诊断（Plan-Execute-Replan）

```
用户点击 "AI Ops" 按钮
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 前端 (static/app.js:1585)                                     │
│    triggerAIOps()                                                 │
│    → newChat() 新建会话                                          │
│    → addLoadingMessage("分析中...") 显示加载动画                  │
│    → sendAIOpsRequest(loadingMessage)                            │
│    → POST http://localhost:9900/api/aiops                        │
│    → Body: {"session_id": "session_xxx"}                         │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. API 路由 (app/api/aiops.py:16)                                │
│    @router.post("/aiops")                                        │
│    async def diagnose_stream(request: AIOpsRequest):             │
│    → EventSourceResponse(event_generator())                      │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. AIOps Service (app/services/aiops_service.py:159)             │
│    aiops_service.diagnose(session_id)                             │
│    └── self.execute(aiops_task, session_id)                      │
│        ├── initial_state = {input, plan:[], past_steps:[],       │
│        │                    response:""}                          │
│        └── graph.astream(initial_state, config)                  │
│            │                                                      │
│            ▼                                                      │
│    ┌───────────────────────────────────────────────────┐        │
│    │         LangGraph StateGraph 状态机               │        │
│    │                                                   │        │
│    │  ┌──────────┐     ┌──────────┐    ┌───────────┐  │        │
│    │  │ PLANNER  │────→│ EXECUTOR │───→│ REPLANNER │  │        │
│    │  │ 制定计划 │     │ 执行步骤 │    │ 评估决策  │  │        │
│    │  └──────────┘     └──────────┘    └─────┬─────┘  │        │
│    │                                         │         │        │
│    │              ┌──────────────────────────┼─────┐   │        │
│    │              ▼                ▼         │     ▼   │        │
│    │          continue          replan       │ respond │        │
│    │              │                │         │     │   │        │
│    │              └────────────────┘         │     │   │        │
│    │                       │                 │     │   │        │
│    │                       ▼                 │     ▼   │        │
│    │                   EXECUTOR              │  END    │        │
│    │                    (循环)               │ (最终)  │        │
│    └─────────────────────────────────────────┴─────────┘        │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3a. Planner 节点 (app/agent/aiops/planner.py:63)                 │
│     async def planner(state: PlanExecuteState):                   │
│     ├── retrieve_knowledge.ainvoke(query=input)  ← 查询知识库SOP │
│     │   └── (app/tools/knowledge_tool.py) → Milvus 检索           │
│     ├── 获取本地工具 + MCP 工具                                   │
│     │   └── mcp_client.get_tools() ← (app/agent/mcp_client.py)   │
│     ├── format_tools_description(all_tools)                       │
│     ├── ChatQwen(model, temperature=0)                            │
│     ├── planner_prompt | llm.with_structured_output(Plan)         │
│     ├── → 生成 4-6 个诊断步骤                                     │
│     └── return {"plan": [step1, step2, ...]}                     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3b. Executor 节点 (app/agent/aiops/executor.py:18)               │
│     async def executor(state: PlanExecuteState):                  │
│     ├── plan[0] → task (取第一个步骤)                             │
│     ├── ChatQwen.bind_tools(all_tools)                            │
│     ├── llm_with_tools.ainvoke([SystemMessage, HumanMessage])     │
│     ├── 如果有 tool_calls:                                        │
│     │   └── ToolNode(all_tools).ainvoke()  ← 自动执行工具         │
│     │       └── 实际调用:                                         │
│     │           ├── query_prometheus_alerts()                     │
│     │           │   └── HTTP GET /api/v1/alerts → Prometheus      │
│     │           ├── MCP: search_log(topic_id, start, end)         │
│     │           │   └── FastMCP → cls_server:8003                 │
│     │           └── MCP: query_cpu_metrics(service_name)          │
│     │               └── FastMCP → monitor_server:8004             │
│     └── return {                                                  │
│           "plan": plan[1:],           ← 移除已执行步骤            │
│           "past_steps": [(task, result)] ← 追加执行历史          │
│         }                                                          │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3c. Replanner 节点 (app/agent/aiops/replanner.py:111)            │
│     async def replanner(state: PlanExecuteState):                 │
│     ├── 检查: len(past_steps) >= MAX_STEPS(8) → 强制 respond     │
│     ├── ChatQwen + replanner_prompt                               │
│     ├── llm.with_structured_output(Act)                          │
│     ├── 三种决策:                                                 │
│     │   ├── "continue" → return {}  (状态不变, 继续执行)         │
│     │   ├── "replan"  → return {"plan": new_steps}               │
│     │   │   (额外检查: past_steps>=5 → 禁止replan, 强制respond)  │
│     │   └── "respond" → _generate_response(state, llm)           │
│     │       ├── response_prompt | llm.with_structured_output()   │
│     │       └── return {"response": final_report}                 │
│     └── 安全网: 如果所有计划执行完 → 自动 _generate_response     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. SSE 流式返回 (app/api/aiops.py:127)                           │
│    async for event in aiops_service.diagnose():                   │
│    ├── type="plan"          → 诊断计划制定完成                    │
│    ├── type="step_complete" → 步骤执行完成 (进度)                 │
│    ├── type="status"        → 状态更新                            │
│    ├── type="report"        → 最终诊断报告 (Markdown)             │
│    ├── type="complete"      → 诊断流程结束                        │
│    └── type="error"         → 异常信息                            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. 前端 SSE 解析 (static/app.js:1179)                            │
│    sendAIOpsRequest() → ReadableStream reader 循环:               │
│    ├── type=content       → fullResponse += data                  │
│    ├── type=plan          → fullResponse += "## 📋 执行计划\n"   │
│    ├── type=step_complete → fullResponse += "\n✅ ..."           │
│    ├── type=report        → fullResponse += "## 🎯 诊断报告\n"   │
│    ├── type=complete/done → updateAIOpsMessage(element, response) │
│    │   → renderMarkdown(完整诊断报告)                             │
│    │   → highlightCodeBlocks()                                    │
│    │   → 保存到 currentChatHistory                                │
│    └── type=error         → 显示错误                              │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 场景四：文件上传入库

```
用户上传文件 "cpu_high_usage.md"
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 前端 (static/app.js:1111)                                     │
│    uploadFile(file)                                               │
│    → FormData 封装                                                │
│    → POST http://localhost:9900/api/upload                       │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. API 路由 (app/api/file.py:21)                                 │
│    @router.post("/upload")                                        │
│    async def upload_file(file: UploadFile):                       │
│    ├── 验证: 文件名 / 扩展名(.txt/.md) / 大小(<10MB)             │
│    ├── 保存到 ./uploads/{sanitized_filename}                     │
│    └── vector_index_service.index_single_file(str(file_path))     │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. 向量索引服务 (app/services/vector_index_service.py:131)       │
│    VectorIndexService.index_single_file(file_path)                │
│    ├── 读取文件内容 (UTF-8)                                       │
│    ├── vector_store_manager.delete_by_source(file_path)           │
│    │   └── (app/services/vector_store_manager.py:95)             │
│    │       └── milvus_manager.get_collection().delete(expr)       │
│    ├── document_splitter_service.split_document(content, path)    │
│    │   └── (app/services/document_splitter_service.py:118)       │
│    └── vector_store_manager.add_documents(documents)              │
│        └── (app/services/vector_store_manager.py:63)             │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. 文档分割 (app/services/document_splitter_service.py)          │
│    split_document(content, file_path)                             │
│    ├── 若 .md 文件: split_markdown()                              │
│    │   ├── 第一阶段: MarkdownHeaderTextSplitter (按 #/## 标题)   │
│    │   ├── 第二阶段: RecursiveCharacterTextSplitter (按大小)     │
│    │   └── 第三阶段: _merge_small_chunks (合并<300字符的碎片)    │
│    └── 若 .txt 文件: split_text()                                 │
│        └── RecursiveCharacterTextSplitter.create_documents()      │
│    → 每个 doc 的 metadata 包含: _source, _file_name, h1/h2...    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. 向量存储 (app/services/vector_store_manager.py:63)            │
│    VectorStoreManager.add_documents(documents)                    │
│    ├── 为每个 doc 生成 UUID                                        │
│    ├── self.vector_store.add_documents(docs, ids=ids)             │
│    │   └── LangChain Milvus wrapper 内部:                         │
│    │       ├── 调用 embedding_function.embed_documents(texts)     │
│    │       │   └── (app/services/vector_embedding_service.py:58) │
│    │       │       └── OpenAI client → DashScope API              │
│    │       │           POST /compatible-mode/v1/embeddings        │
│    │       │           model=text-embedding-v4, dim=1024          │
│    │       └── MilvusClient.insert(collection, vectors, ...)      │
│    └── 完成, 文档可被 RAG 检索                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 文件调用链矩阵

以下矩阵展示了「谁调用了谁」的核心关系：

### 5.1 API 层 → Service 层

| API 文件 | API 端点 | 调用的 Service / 函数 |
|----------|----------|----------------------|
| [chat.py](app/api/chat.py) | `POST /api/chat` | `rag_agent_service.query()` |
| [chat.py](app/api/chat.py) | `POST /api/chat_stream` | `rag_agent_service.query_stream()` |
| [chat.py](app/api/chat.py) | `POST /api/chat/clear` | `rag_agent_service.clear_session()` |
| [chat.py](app/api/chat.py) | `GET /api/chat/session/{id}` | `rag_agent_service.get_session_history()` |
| [aiops.py](app/api/aiops.py) | `POST /api/aiops` | `aiops_service.diagnose()` |
| [file.py](app/api/file.py) | `POST /api/upload` | `vector_index_service.index_single_file()` |
| [file.py](app/api/file.py) | `POST /api/index_directory` | `vector_index_service.index_directory()` |
| [health.py](app/api/health.py) | `GET /health` | `milvus_manager.health_check()` |

### 5.2 Service 层 → Agent/Tools 层

| Service 文件 | 调用的 Agent / Tools 函数 |
|-------------|--------------------------|
| [rag_agent_service.py](app/services/rag_agent_service.py) | `get_mcp_client_with_retry()`, `load_mcp_tools_safe()`, `DEFAULT_LOCAL_AGENT_TOOLS`, `create_agent()` |
| [aiops_service.py](app/services/aiops_service.py) | `planner()`, `executor()`, `replanner()` (从 `app.agent.aiops` 导入) |
| [vector_index_service.py](app/services/vector_index_service.py) | `document_splitter_service.split_document()`, `vector_store_manager.add_documents()`, `vector_store_manager.delete_by_source()` |
| [vector_store_manager.py](app/services/vector_store_manager.py) | `milvus_manager.connect()`, `milvus_manager.get_collection()`, `vector_embedding_service` (作为 embedding_function) |
| [vector_embedding_service.py](app/services/vector_embedding_service.py) | `OpenAI` client → DashScope API |

### 5.3 Agent/Tools 层 → Core 层

| Agent/Tools 文件 | 调用的 Core 基础设施 |
|-------------------|---------------------|
| [planner.py](app/agent/aiops/planner.py) | `ChatQwen(model, api_key)`, `config`, `retrieve_knowledge`, `get_mcp_client_with_retry()` |
| [executor.py](app/agent/aiops/executor.py) | `ChatQwen(model, api_key)`, `config`, `DEFAULT_LOCAL_AGENT_TOOLS`, `get_mcp_client_with_retry()` |
| [replanner.py](app/agent/aiops/replanner.py) | `ChatQwen(model, api_key)`, `config`, `get_mcp_client_with_retry()` |
| [mcp_client.py](app/agent/mcp_client.py) | `config.mcp_servers`, `MultiServerMCPClient` |
| [knowledge_tool.py](app/tools/knowledge_tool.py) | `vector_store_manager.get_vector_store()`, `config.rag_top_k` |
| [time_tool.py](app/tools/time_tool.py) | `ZoneInfo`, `datetime` (标准库) |
| [query_metrics_alerts.py](app/tools/query_metrics_alerts.py) | `config.prometheus_base_url`, `httpx` |
| [llm_factory.py](app/core/llm_factory.py) | `config`, `ChatOpenAI` |
| [milvus_client.py](app/core/milvus_client.py) | `config`, `pymilvus` |

### 5.4 全局单例汇总

| 单例变量 | 定义位置 | 类型 |
|----------|---------|------|
| `config` | [app/config.py:72](app/config.py#L72) | `Settings` |
| `rag_agent_service` | [app/services/rag_agent_service.py:418](app/services/rag_agent_service.py#L418) | `RagAgentService(streaming=True)` |
| `aiops_service` | [app/services/aiops_service.py:341](app/services/aiops_service.py#L341) | `AIOpsService` |
| `milvus_manager` | [app/core/milvus_client.py:318](app/core/milvus_client.py#L318) | `MilvusClientManager` |
| `vector_store_manager` | [app/services/vector_store_manager.py:153](app/services/vector_store_manager.py#L153) | `VectorStoreManager` |
| `vector_embedding_service` | [app/services/vector_embedding_service.py:125](app/services/vector_embedding_service.py#L125) | `DashScopeEmbeddings` |
| `vector_index_service` | [app/services/vector_index_service.py:175](app/services/vector_index_service.py#L175) | `VectorIndexService` |
| `vector_search_service` | [app/services/vector_search_service.py:104](app/services/vector_search_service.py#L104) | `VectorSearchService` |
| `document_splitter_service` | [app/services/document_splitter_service.py:176](app/services/document_splitter_service.py#L176) | `DocumentSplitterService` |
| `llm_factory` | [app/core/llm_factory.py:52](app/core/llm_factory.py#L52) | `LLMFactory` |
| `_mcp_client` | [app/agent/mcp_client.py:17](app/agent/mcp_client.py#L17) | `Optional[MultiServerMCPClient]` |

---

## 6. 关键设计模式与亮点

### 6.1 Plan-Execute-Replan 状态机

AIOps 诊断的核心是 LangGraph 的三节点循环状态机：

```
Planner ──→ Executor ──→ Replanner
              ↑               │
              │   continue    │
              └───────────────┘
              ↑               │
              │   replan      │ (新步骤替换旧计划)
              └───────────────┘
                              │
                              ▼  respond
                            END (最终报告)
```

**安全机制**：
- 最多执行 **8 步** → 强制 respond
- 已执行 ≥ **5 步** → 禁止 replan，强制 respond
- 新步骤数 ≤ 剩余步骤数 → 防止无限膨胀
- 信息充足优先级 > 完美追求 → "优先结束 > 保持不变 > 调整计划"

### 6.2 RAG 文档处理管道（三段式分块）

```
原始 MD 文档
  → MarkdownHeaderTextSplitter (按 #/## 标题切分)
    → RecursiveCharacterTextSplitter (按 chunk_size*2=1600 字符再切)
      → _merge_small_chunks (合并 <300 字符的碎片)
        → 每个 chunk 携带 metadata (_source, _file_name, h1/h2)
          → DashScope text-embedding-v4 (1024维)
            → Milvus insert
```

### 6.3 MCP 单例 + 重试拦截器

```python
# 全局单例 (app/agent/mcp_client.py)
_mcp_client: Optional[MultiServerMCPClient] = None

async def get_mcp_client_with_retry():
    # 使用指数退避重试拦截器
    interceptors = [retry_interceptor]  # 最多3次, 指数退避
    return await get_mcp_client(tool_interceptors=interceptors)
```

### 6.4 Agent 设计

RAG Agent 使用 LangGraph 的 `create_agent` (prebuilt)，工具集 = 本地工具 + MCP 工具：

```python
# DEFAULT_LOCAL_AGENT_TOOLS (app/tools/__init__.py)
DEFAULT_LOCAL_AGENT_TOOLS = (
    retrieve_knowledge,        # Milvus 知识检索
    get_current_time,          # 时间查询
    query_prometheus_alerts,   # Prometheus 告警查询
)

# + MCP Tools
# cls_server (8003): search_log, get_current_timestamp, search_topic_by_service_name, ...
# monitor_server (8004): query_cpu_metrics, query_memory_metrics
```

---

## 7. Harness 改造方案

### 7.1 什么是 Harness

在当前项目语境下，**Harness（测试/评估框架）** 是一套可插拔的中间件系统，用于：

1. **拦截和记录** Agent 的每一次 LLM 调用和工具调用
2. **Mock 替换** 外部依赖（MCP 服务、Prometheus、Milvus）以进行可控测试
3. **评估打分** 自动评测 Agent 回答的质量
4. **场景回放** 基于录制的 trace 重现诊断过程
5. **A/B 对比** 不同 Prompt/模型配置的效果对比

### 7.2 架构设计

```
                          ┌─────────────────────┐
                          │   Harness Manager   │  ← 总控
                          │  (app/harness/)      │
                          └──────────┬──────────┘
                                     │
        ┌────────────┬───────────────┼───────────────┬────────────┐
        ▼            ▼               ▼               ▼            ▼
   ┌─────────┐ ┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌──────────┐
   │ Tracer  │ │  Mocker  │ │  Evaluator   │ │ Recorder  │ │Comparator│
   │ 调用追踪 │ │ 工具Mock │ │  质量评估     │ │ 场景录制  │ │ A/B对比  │
   └─────────┘ └──────────┘ └──────────────┘ └───────────┘ └──────────┘
```

### 7.3 新增文件清单

```
app/
├── harness/                              # 🔴 新增：Harness 模块
│   ├── __init__.py                       # 导出 HarnessManager, 装饰器等
│   ├── manager.py                        # HarnessManager 总控类
│   ├── tracer.py                         # LLM/Tool 调用追踪器
│   ├── mocker.py                         # Mock 工具工厂
│   ├── evaluator.py                      # 回答质量评估器
│   ├── recorder.py                       # 场景录制与回放
│   ├── comparator.py                     # A/B 对比器
│   └── types.py                          # Harness 相关类型定义
├── api/
│   └── harness.py                        # 🔴 新增：Harness 控制 API
│       # GET  /api/harness/status        - 查看 Harness 状态
│       # POST /api/harness/mock/start    - 开启 Mock 模式
│       # POST /api/harness/mock/stop     - 关闭 Mock 模式
│       # POST /api/harness/scenario/run  - 运行预设诊断场景
│       # GET  /api/harness/scenarios     - 列出所有场景
│       # GET  /api/harness/traces/{id}   - 查看某次调用 Trace
│       # POST /api/harness/evaluate      - 发起评估
├── scenarios/                            # 🔴 新增：预设诊断场景
│   ├── __init__.py
│   ├── base.py                           # 场景基类
│   ├── cpu_high.py                       # CPU 高使用率场景
│   ├── memory_leak.py                    # 内存泄漏场景
│   ├── disk_full.py                      # 磁盘满场景
│   ├── service_down.py                   # 服务宕机场景
│   └── slow_response.py                  # 响应慢场景
└── config.py                             # 🟡 修改：增加 Harness 配置项
```

### 7.4 各模块详细设计

#### 7.4.1 HarnessManager (`app/harness/manager.py`)

```python
class HarnessManager:
    """Harness 总控制器，单例模式"""

    def __init__(self):
        self._enabled = False
        self._mock_mode = False
        self._tracer = Tracer()
        self._mocker = Mocker()
        self._evaluator = Evaluator()
        self._recorder = Recorder()
        self._comparator = Comparator()

    # ---- 开关控制 ----
    def enable(self): ...
    def disable(self): ...

    # ---- Mock 模式 ----
    def start_mock(self, scenario: str): ...
    def stop_mock(self): ...

    # ---- Trace ----
    def get_trace(self, trace_id: str) -> Trace: ...

    # ---- 评估 ----
    async def evaluate(self, question: str, expected: str) -> EvalResult: ...

    # ---- 场景 ----
    def load_scenario(self, name: str) -> Scenario: ...
```

#### 7.4.2 Tracer (`app/harness/tracer.py`)

**目的**：拦截并记录每一次 LLM 调用和工具调用的输入/输出/耗时。

```python
@dataclass
class LLMCall:
    id: str
    model: str
    messages: list
    response: str
    tool_calls: list
    tokens_used: int
    duration_ms: float
    timestamp: datetime

@dataclass
class ToolCall:
    id: str
    tool_name: str
    input: dict
    output: Any
    duration_ms: float
    success: bool
    error: str | None

class Tracer:
    """调用追踪器"""

    def __init__(self):
        self.traces: dict[str, list[LLMCall | ToolCall]] = {}

    def start_trace(self, session_id: str) -> str: ...  # 返回 trace_id
    def record_llm_call(self, trace_id: str, call: LLMCall): ...
    def record_tool_call(self, trace_id: str, call: ToolCall): ...
    def get_trace(self, trace_id: str) -> list: ...
    def export_trace(self, trace_id: str) -> dict: ...  # 导出 JSON
```

**集成方式**：通过 Monkey Patch 或装饰器模式注入到 `RagAgentService` 和 `AIOpsService` 中：

```python
# 方案 A: 装饰器
@harness.trace(session_id="xxx")
async def query_stream(self, question, session_id): ...

# 方案 B: Harness 包装器
class HarnessedRagAgentService:
    def __init__(self, real_service, harness_manager):
        self._real = real_service
        self._hm = harness_manager

    async def query_stream(self, question, session_id):
        trace_id = self._hm.tracer.start_trace(session_id)
        async for chunk in self._real.query_stream(question, session_id):
            self._hm.tracer.record(...)
            yield chunk
```

#### 7.4.3 Mocker (`app/harness/mocker.py`)

**目的**：用预设的 Mock 数据替换 MCP 工具、Prometheus 查询、Milvus 检索的返回结果，实现对故障场景的可控测试。

```python
class Mocker:
    """Mock 工具工厂"""

    def __init__(self):
        self._active_mocks: dict[str, Callable] = {}
        self._scenario_data: dict = {}

    def load_scenario(self, name: str):
        """加载预设场景的 Mock 数据"""
        self._scenario_data = SCENARIOS[name]

    def mock_tool(self, tool_name: str, return_value: Any):
        """注册单个工具的 Mock"""
        ...

    def get_mocked_tools(self) -> list:
        """返回 Mock 版本的 Tool 列表, 供 Agent 使用"""
        ...

    def clear(self):
        """清除所有 Mock"""
        ...
```

**预设场景数据示例** (`scenarios/cpu_high.py`):

```python
SCENARIO_CPU_HIGH = {
    "name": "cpu_high",
    "description": "模拟 data-sync-service CPU 使用率持续超过 90% 的告警场景",
    "mock_data": {
        "query_prometheus_alerts": {
            "success": True,
            "alerts": [{
                "alert_name": "HighCPUUsage",
                "severity": "critical",
                "instance": "data-sync-service-01",
                "state": "firing",
                "duration": "45m",
                "description": "CPU usage above 90% for 5 minutes"
            }],
            "total": 1
        },
        "query_cpu_metrics": {
            "service_name": "data-sync-service",
            "data_points": [
                {"timestamp": "10:00", "value": 45.0},
                {"timestamp": "10:05", "value": 62.3},
                {"timestamp": "10:10", "value": 88.7},
                {"timestamp": "10:15", "value": 95.2},
                # ... 逐渐升高
            ],
            "statistics": {"avg": 78.5, "max": 96.1}
        },
        "search_log": {
            "total": 3,
            "logs": [
                {"timestamp": "10:12", "level": "ERROR", "message": "Connection pool exhausted"},
                {"timestamp": "10:14", "level": "WARN", "message": "Thread pool at 95% capacity"},
            ]
        },
        "retrieve_knowledge": "## CPU 高使用率排查 SOP\n1. 使用 top/htop 确认进程\n2. 检查数据库连接池\n3. 分析慢查询..."
    },
    "expected_report": {
        "root_cause": "数据库连接池耗尽导致线程堆积",
        "should_contain": ["连接池", "数据库", "慢查询"],
        "recommendations": ["扩容连接池", "优化 SQL", "增加限流"]
    }
}
```

#### 7.4.4 Evaluator (`app/harness/evaluator.py`)

**目的**：用 LLM-as-Judge 或规则匹配自动评估 Agent 回答和诊断报告的质量。

```python
@dataclass
class EvalResult:
    score: float           # 0-100
    accuracy: float        # 事实准确性
    completeness: float    # 覆盖度
    relevance: float       # 相关性
    structure: float       # 结构完整性
    details: dict          # 详细扣分点

class Evaluator:
    """回答质量评估器"""

    def __init__(self, llm_judge_model: str = "qwen-max"):
        self.judge_llm = ChatQwen(model=llm_judge_model, temperature=0)

    async def evaluate(
        self,
        question: str,
        answer: str,
        expected: str | None = None,
        criteria: list[str] | None = None,
    ) -> EvalResult:
        """使用 LLM-as-Judge 评估回答质量"""
        ...

    async def evaluate_aiops_report(
        self,
        report: str,
        scenario_mock_data: dict,
        expected_findings: list[str],
    ) -> EvalResult:
        """专门评估 AIOps 诊断报告的质量"""
        ...
```

**评估维度**：

| 维度 | 权重 | 评估方式 |
|------|------|----------|
| 事实准确性 | 35% | LLM 判断 + 与 Expected 关键点对比 |
| 覆盖完整性 | 25% | 检查是否覆盖了 Expected 中的所有要点 |
| 逻辑连贯性 | 20% | LLM 判断报告的逻辑结构 |
| 可操作性 | 15% | 检查是否包含具体的处理建议 |
| 格式规范 | 5% | 检查 Markdown 格式和结构 |

#### 7.4.5 Recorder (`app/harness/recorder.py`)

**目的**：录制真实的 Agent 交互过程，保存为可回放的 Scenario 文件，便于回归测试。

```python
class Recorder:
    """场景录制与回放"""

    def start_recording(self, session_id: str): ...
    def stop_recording(self) -> dict:  # 返回录制的场景数据
    def save_scenario(self, name: str, data: dict, filepath: str): ...
    def load_scenario(self, filepath: str) -> dict: ...
    def replay(self, scenario: dict) -> AsyncGenerator: ...
```

#### 7.4.6 Comparator (`app/harness/comparator.py`)

**目的**：A/B 对比不同配置（Prompt、模型、温度等）下 Agent 的回答质量。

```python
class Comparator:
    """A/B 对比器"""

    async def compare(
        self,
        question: str,
        configs: list[dict],  # 每组配置: {model, temperature, system_prompt, ...}
        evaluator: Evaluator,
    ) -> ComparisonResult:
        """
        用不同配置运行同一问题，对比评估结果
        返回: {config_id: EvalResult, winner: config_id, analysis: str}
        """
```

### 7.5 Harness API 设计

新增 [app/api/harness.py](app/api/harness.py)：

```python
router = APIRouter(prefix="/api/harness", tags=["Harness 测试框架"])

@router.get("/status")
async def harness_status():
    """查看当前 Harness 状态 (enabled/mock_mode/active_scenario)"""

@router.post("/mock/start")
async def start_mock(scenario: str):
    """开启 Mock 模式，加载指定场景的 Mock 数据"""

@router.post("/mock/stop")
async def stop_mock():
    """关闭 Mock 模式，恢复正常服务"""

@router.get("/scenarios")
async def list_scenarios():
    """列出所有可用的预设诊断场景"""

@router.post("/scenarios/run")
async def run_scenario(scenario: str):
    """运行指定场景的诊断（SSE 流式返回 + 自动评估）"""

@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """查看指定 Trace 的完整调用记录"""

@router.post("/evaluate")
async def evaluate_answer(question: str, answer: str, expected: str | None = None):
    """评估一个回答的质量"""

@router.post("/compare")
async def compare_configs(question: str, configs: list[dict]):
    """A/B 对比不同配置的回答质量"""
```

### 7.6 配置扩展

修改 [app/config.py](app/config.py) 增加 Harness 相关配置：

```python
class Settings(BaseSettings):
    # ... 现有配置 ...

    # Harness 配置
    harness_enabled: bool = False
    harness_mock_mode: bool = False
    harness_trace_dir: str = "traces"          # Trace 文件存储目录
    harness_scenario_dir: str = "scenarios"    # 场景文件目录
    harness_judge_model: str = "qwen-max"      # 评估 LLM 模型
```

### 7.7 `__init__.py` 改造

修改 [app/__init__.py](app/__init__.py) 以支持 Harness 的可选初始化：

```python
__version__ = "1.3.0"  # 升级版本号

from app.utils import logger  # noqa: F401

# 如果启用 Harness，自动初始化
from app.config import config
if config.harness_enabled:
    from app.harness import harness_manager
    harness_manager.enable()
    logger.info("Harness 框架已启用")
```

---

## 8. 实施路线图

### Phase 1: 基础框架 (1-2 周)

- [ ] 创建 `app/harness/` 目录结构
- [ ] 实现 `types.py` 类型定义
- [ ] 实现 `manager.py` HarnessManager 单例
- [ ] 实现 `tracer.py` 调用追踪器
- [ ] 在 `RagAgentService.query_stream()` 中添加 Trace 钩子
- [ ] 实现 Harness API 基础端点 (status, traces)

### Phase 2: Mock 与场景 (1-2 周)

- [ ] 实现 `mocker.py` Mock 工具工厂
- [ ] 实现 `scenarios/base.py` 场景基类
- [ ] 编写 5 个预设诊断场景（CPU高、内存泄漏、磁盘满、服务宕机、响应慢）
- [ ] 实现 Mock 模式开关 API
- [ ] 在 `AIOpsService` 中支持 Mock 工具注入

### Phase 3: 评估体系 (1 周)

- [ ] 实现 `evaluator.py` LLM-as-Judge 评估器
- [ ] 设计评估 Prompt 和评分标准
- [ ] 实现 `recorder.py` 场景录制与回放
- [ ] 实现评估 API

### Phase 4: A/B 对比与优化 (1 周)

- [ ] 实现 `comparator.py` A/B 对比器
- [ ] 实现对比 API
- [ ] 前端增加 Harness 控制面板（可选）
- [ ] 编写使用文档

---

## 附录：项目数据流总图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│   用户浏览器 (static/)                                                      │
│   ┌──────────────────────────────────────────┐                            │
│   │  SuperBizAgentApp (app.js)               │                            │
│   │  ├─ sendQuickMessage()  → /api/chat      │                            │
│   │  ├─ sendStreamMessage() → /api/chat_stream│                           │
│   │  ├─ triggerAIOps()      → /api/aiops     │                            │
│   │  └─ uploadFile()        → /api/upload    │                            │
│   └──────────────┬───────────────────────────┘                            │
│                  │  HTTP/SSE                                                 │
│   ┌──────────────▼───────────────────────────┐                            │
│   │  FastAPI (app/main.py) :9900            │                            │
│   │  ├─ chat.py    → rag_agent_service       │                            │
│   │  ├─ aiops.py   → aiops_service           │                            │
│   │  ├─ file.py    → vector_index_service    │                            │
│   │  └─ health.py  → milvus_manager          │                            │
│   └──────────────┬───────────────────────────┘                            │
│                  │                                                          │
│   ┌──────────────▼───────────────────────────────────────────────────┐    │
│   │  Service Layer                                                     │    │
│   │  ┌────────────────────┐  ┌──────────────────────────────────┐    │    │
│   │  │ RagAgentService     │  │ AIOpsService                     │    │    │
│   │  │ LangGraph Agent     │  │ LangGraph StateGraph             │    │    │
│   │  │ + ChatQwen          │  │ Planner → Executor → Replanner    │    │    │
│   │  │ + MemorySaver       │  │ + MemorySaver                    │    │    │
│   │  └─────────┬──────────┘  └────────────┬─────────────────────┘    │    │
│   │            │                           │                           │    │
│   │            ▼                           ▼                           │    │
│   │  ┌─────────────────────────────────────────────────────────┐     │    │
│   │  │  Tools: retrieve_knowledge | get_current_time |         │     │    │
│   │  │         query_prometheus_alerts | MCP Tools              │     │    │
│   │  └─────────────────────────────────────────────────────────┘     │    │
│   └──────────────────────────────────────────────────────────────────┘    │
│                  │                                                          │
│   ┌──────────────▼───────────────────────────────────────────────────┐    │
│   │  External Services                                                 │    │
│   │  ├─ Milvus (:19530)       ← 向量存储与检索                        │    │
│   │  ├─ DashScope API         ← LLM + Embedding                       │    │
│   │  ├─ Prometheus (:9090)    ← 告警查询                              │    │
│   │  ├─ cls_server (:8003)    ← MCP 日志查询                          │    │
│   │  └─ monitor_server (:8004)← MCP 监控数据                          │    │
│   └──────────────────────────────────────────────────────────────────┘    │
│                                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```
