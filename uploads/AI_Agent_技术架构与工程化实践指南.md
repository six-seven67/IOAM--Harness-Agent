# AI Agent 技术架构与工程化实践指南

# 一、文档概述

本文档系统介绍 AI Agent 的技术架构、核心组件与工程化实践，涵盖从基础概念到企业级落地的全链路知识。文档结合 Python 技术栈，提供可落地的代码示例与最佳实践。

本文档与《Python 企业级开发技术白皮书》配套使用，Python 开发规范请参考配套文档。

## 1\.1 适用范围

- 企业级 AI Agent 系统设计与开发

- 对话式 AI 应用与智能客服

- RAG 检索增强生成系统

- 多 Agent 协作系统

- AI 辅助工具与自动化工作流

## 1\.2 读者对象

- AI/ML 工程师与算法工程师

- Python 后端开发工程师

- 产品经理与技术架构师

- AI 应用开发者与创业者

- 对 Agent 技术感兴趣的技术人员

## 1\.3 技术栈说明

本文档所有代码示例均基于 Python 语言，主要技术栈包括：

| 类别       | 技术选型                   | 说明            |
| -------- | ---------------------- | ------------- |
| 编程语言     | Python 3\.11\+         | AI 领域主流语言     |
| Web 框架   | FastAPI                | 高性能异步框架       |
| Agent 框架 | LangChain / LlamaIndex | 主流 Agent 开发框架 |
| 向量数据库    | ChromaDB / Milvus      | 向量存储与检索       |
| 数据库      | MySQL \+ SQLAlchemy    | 结构化数据存储       |
| 缓存       | Redis                  | 会话缓存与限流       |

---

# 二、Agent 技术概述与发展历程

## 2\.1 什么是 Agent

**AI Agent（智能体）**是一种能够感知环境、自主决策并执行动作以实现特定目标的人工智能系统。与传统的问答系统不同，Agent 具备以下核心能力：

1. **感知能力**：接收用户输入、环境信息和工具反馈

2. **推理能力**：基于大语言模型进行思考、规划和决策

3. **行动能力**：调用工具、执行操作、与外部系统交互

4. **记忆能力**：保存历史对话、知识和经验

5. **学习能力**：从交互中优化行为策略

简单理解：LLM 是 Agent 的"大脑"，工具是 Agent 的"手脚"，记忆是 Agent 的"经验"，三者结合构成完整的智能体。

## 2\.2 Agent 与传统应用的区别

| 维度   | 传统应用         | AI Agent       |
| ---- | ------------ | -------------- |
| 交互方式 | 固定菜单、表单、按钮   | 自然语言对话         |
| 逻辑实现 | 硬编码规则        | LLM 推理 \+ 工具调用 |
| 扩展性  | 需要开发新功能      | 添加新工具即可扩展能力    |
| 容错性  | 严格输入校验，错误即失败 | 可理解模糊输入，自动纠错   |
| 个性化  | 预设配置         | 基于记忆动态适配       |
| 开发模式 | 功能驱动         | 意图驱动 \+ 工具编排   |

## 2\.3 发展历程

1. **2022 年底 \- 萌芽期**：ChatGPT 发布，Prompt Engineering 兴起

2. **2023 年初 \- 探索期**：AutoGPT、BabyAGI 等自主 Agent 出现，概念验证阶段

3. **2023 年中 \- 框架期**：LangChain、LlamaIndex 等框架成熟，工具调用能力增强

4. **2023 年底 \- 应用期**：RAG 技术普及，企业级应用开始落地

5. **2024 年 \- 工程化期**：多 Agent 协作、Agent 评估、可观测性等工程化能力完善

6. **2025 年及以后 \- 普及期**：Agent 成为软件标配，人机协作成为常态

## 2\.4 Agent 的分类

### 2\.4\.1 按能力复杂度分类

| 级别   | 名称         | 特点          | 典型应用         |
| ---- | ---------- | ----------- | ------------ |
| L1   | 基础对话 Agent | 单轮问答、无工具调用  | 智能客服、FAQ 机器人 |
| L2   | 工具调用 Agent | 支持工具调用、单步推理 | 查询助手、数据分析师   |
| L3   | 规划型 Agent  | 多步规划、链式思考   | 任务助手、研究助理    |
| L4   | 自主 Agent   | 自主决策、长期目标   | 自动化研究员、数字员工  |
| L5   | 多 Agent 系统 | 多智能体协作、分工明确 | 虚拟团队、自动化工作流  |

### 2\.4\.2 按应用场景分类

- **对话型 Agent**：客服、助理、咨询

- **工具型 Agent**：代码助手、数据分析、文档处理

- **创作型 Agent**：写作、设计、编程辅助

- **决策型 Agent**：投资建议、风险评估、策略优化

- **执行型 Agent**：自动化操作、工作流编排、RPA

---

# 三、Agent 核心架构设计

## 3\.1 经典 Agent 架构

一个标准的 Agent 系统由以下核心模块组成：

### 核心组件

- 大语言模型（LLM）

- 提示词管理（Prompt）

- 记忆系统（Memory）

- 工具集（Tools）

- 规划模块（Planner）

- 执行器（Executor）

- 评估模块（Evaluator）

### 工作流程

1. 接收用户输入

2. 加载历史记忆

3. LLM 思考与规划

4. 选择并调用工具

5. 处理工具返回结果

6. 生成最终回答

7. 更新记忆系统

## 3\.2 ReAct 架构

ReAct（Reasoning \+ Acting）是目前最主流的 Agent 架构模式，将推理和行动交替进行：

```text
用户问题 → 思考（Thought）→ 行动（Action）→ 观察（Observation）→ 思考 → 行动 → ... → 最终答案
```

每一轮循环包括：

1. **Thought**：LLM 分析当前状态，思考下一步该做什么

2. **Action**：决定调用哪个工具，以及传入什么参数

3. **Observation**：获取工具执行的结果

4. 重复以上步骤，直到得出最终答案

## 3\.3 分层架构设计

企业级 Agent 系统推荐采用分层架构：

| 层级    | 模块                  | 职责                   |
| ----- | ------------------- | -------------------- |
| 接入层   | API Gateway、Web、App | 用户接入、协议转换、鉴权         |
| 编排层   | Agent Orchestrator  | 任务调度、多 Agent 协作、流程控制 |
| 能力层   | LLM、Memory、Tools    | 核心能力提供               |
| 数据层   | 向量库、关系库、缓存          | 数据存储与检索              |
| 基础设施层 | 监控、日志、安全            | 运维支撑                 |

## 3\.4 关键设计原则

1. **模块化**：各组件解耦，可独立替换和升级

2. **可观测**：完整的日志、追踪、指标体系

3. **可扩展**：支持动态添加工具和能力

4. **容错性**：工具调用失败、LLM 异常的降级处理

5. **安全性**：输入输出校验、权限控制、内容安全

6. **可评估**：建立质量评估体系，持续优化

---

# 四、核心技术模块详解

## 4\.1 大语言模型（LLM）

### 4\.1\.1 模型选型

| 模型              | 厂商        | 特点           | 适用场景       |
| --------------- | --------- | ------------ | ---------- |
| GPT\-4o         | OpenAI    | 能力最强、多模态、速度快 | 复杂推理、生产环境  |
| GPT\-3\.5 Turbo | OpenAI    | 性价比高、速度快     | 日常对话、简单任务  |
| Claude 3        | Anthropic | 长上下文、写作能力强   | 长文档处理、内容创作 |
| 通义千问            | 阿里云       | 中文好、国内访问快    | 国内企业应用     |
| 文心一言            | 百度        | 中文理解好        | 国内企业应用     |
| Llama 3         | Meta      | 开源、可私有化部署    | 数据敏感场景     |

### 4\.1\.2 模型调用封装

```python
from typing import List, Dict, Any, AsyncGenerator
from openai import AsyncOpenAI
from app.config import settings

class LLMService:
    """大语言模型服务封装"""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )
        self.default_model = settings.LLM_MODEL
        self.default_temperature = settings.LLM_TEMPERATURE
        self.default_max_tokens = settings.LLM_MAX_TOKENS

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> str:
        """同步聊天接口"""
        response = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens or self.default_max_tokens,
            **kwargs
        )
        return response.choices[0].message.content

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式聊天接口"""
        stream = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens or self.default_max_tokens,
            stream=True,
            **kwargs
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        model: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """带工具调用的聊天"""
        response = await self.client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            **kwargs
        )
        message = response.choices[0].message

        return {
            "content": message.content,
            "tool_calls": message.tool_calls,
            "usage": response.usage,
        }

# 全局实例
llm_service = LLMService()
```

## 4\.2 提示词工程（Prompt Engineering）

### 4\.2\.1 提示词设计原则

1. **角色设定**：明确 Agent 的身份和专业领域

2. **任务描述**：清晰说明需要完成的任务

3. **输出格式**：指定输出格式和结构

4. **约束条件**：说明限制和注意事项

5. **示例引导**：提供 Few\-shot 示例

6. **思维链**：引导模型逐步思考

### 4\.2\.2 系统提示词模板

```text
你是一个专业的 {role}，擅长 {domain}。

## 你的职责
- {responsibility_1}
- {responsibility_2}
- {responsibility_3}

## 工作流程
1. 理解用户的问题和需求
2. 分析问题，确定需要哪些信息
3. 必要时调用工具获取信息
4. 基于信息给出专业的回答
5. 如果信息不足，向用户确认

## 回答要求
- 回答要专业、准确、简洁
- 使用中文回答
- 数据和事实要有依据
- 不确定的内容要说明
- 不要编造信息

## 可用工具
你可以使用以下工具来帮助回答问题：
{tools_description}

## 输出格式
{output_format_instructions}
```

### 4\.2\.3 提示词版本管理

- 提示词作为代码管理，纳入版本控制

- 不同环境使用不同版本的提示词

- 建立提示词评估体系，持续优化

- A/B 测试不同提示词的效果

## 4\.3 记忆系统（Memory）

### 4\.3\.1 记忆的分类

| 记忆类型 | 存储内容         | 存储方式       | 生命周期 |
| ---- | ------------ | ---------- | ---- |
| 短期记忆 | 当前对话历史       | 内存 / Redis | 会话级  |
| 长期记忆 | 用户画像、偏好、历史交互 | 数据库 / 向量库  | 用户级  |
| 知识记忆 | 领域知识、文档库     | 向量数据库      | 全局   |
| 工作记忆 | 当前任务的中间状态    | 内存         | 任务级  |

### 4\.3\.2 对话记忆实现

```python
from typing import List, Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain.memory import ConversationBufferWindowMemory
from app.core.db import db_manager
from app.models.message import Message
from sqlalchemy import select, desc

class ConversationMemory:
    """对话记忆管理器"""

    def __init__(self, max_history: int = 20, window_size: int = 10):
        self.max_history = max_history
        self.window_size = window_size

    async def get_history(
        self, 
        conversation_id: str,
        system_prompt: Optional[str] = None
    ) -> List[BaseMessage]:
        """获取对话历史"""
        # 从数据库查询最近的消息
        async with db_manager.async_session() as session:
            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(desc(Message.created_at))
                .limit(self.max_history)
            )
            messages = result.scalars().all()

        # 按时间正序排列
        messages = list(reversed(messages))

        # 转换为 LangChain 消息格式
        result = []

        # 添加系统提示词
        if system_prompt:
            result.append(SystemMessage(content=system_prompt))

        # 添加历史消息
        for msg in messages:
            if msg.role == "user":
                result.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                result.append(AIMessage(content=msg.content))

        return result

    async def add_message(
        self, 
        conversation_id: str, 
        role: str,
        content: str,
        metadata: Optional[dict] = None
    ):
        """保存消息到数据库"""
        async with db_manager.async_session() as session:
            msg = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                metadata=metadata or {},
            )
            session.add(msg)
            await session.commit()

    async def clear_history(self, conversation_id: str):
        """清空对话历史"""
        async with db_manager.async_session() as session:
            await session.execute(
                delete(Message).where(Message.conversation_id == conversation_id)
            )
            await session.commit()

    def format_messages(self, messages: List[BaseMessage]) -> List[dict]:
        """格式化为 OpenAI 消息格式"""
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})
        return result
```

### 4\.3\.3 记忆优化策略

- **滑动窗口**：只保留最近 N 条消息，控制上下文长度

- **摘要记忆**：对历史对话生成摘要，减少 token 占用

- **向量检索**：从历史中检索相关片段，而非全部加载

- **分层记忆**：短期详细记忆 \+ 长期摘要记忆结合

---

# 五、工具调用与 RAG 检索增强

## 5\.1 工具调用（Tool Use）

### 5\.1\.1 什么是工具调用

工具调用是 Agent 与外部世界交互的核心能力。LLM 本身只有"知识"，通过工具调用，Agent 可以：

- 获取实时信息（天气、股票、新闻）

- 查询业务数据（数据库、API）

- 执行操作（发邮件、创建工单、调用接口）

- 进行计算（数学运算、代码执行）

- 检索知识（知识库、文档库）

### 5\.1\.2 工具定义规范

```python
from langchain.tools import tool
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class SearchInput(BaseModel):
    """搜索工具输入参数"""
    query: str = Field(description="搜索查询关键词")
    top_k: int = Field(default=5, description="返回结果数量")
    domain: Optional[str] = Field(default=None, description="搜索领域限制")

@tool(args_schema=SearchInput)
def knowledge_search(query: str, top_k: int = 5, domain: str = None) -> List[Dict[str, Any]]:
    """
    在企业知识库中搜索相关信息。
    当用户询问产品说明、使用方法、常见问题等知识时使用此工具。

    Args:
        query: 搜索查询关键词
        top_k: 返回结果数量，默认5条
        domain: 搜索领域限制，可选值：product, faq, policy

    Returns:
        相关知识片段列表，包含内容和来源
    """
    # 调用向量数据库进行相似度搜索
    results = vector_store.similarity_search(
        query, 
        k=top_k,
        filter={"domain": domain} if domain else None
    )

    return [
        {
            "content": doc.page_content,
            "source": doc.metadata.get("source", ""),
            "score": float(doc.metadata.get("score", 0)),
        }
        for doc in results
    ]

class OrderQueryInput(BaseModel):
    """订单查询工具输入"""
    order_id: Optional[str] = Field(default=None, description="订单编号")
    user_id: Optional[int] = Field(default=None, description="用户ID")
    status: Optional[str] = Field(default=None, description="订单状态")
    limit: int = Field(default=10, description="返回数量限制")

@tool(args_schema=OrderQueryInput)
def query_orders(
    order_id: str = None,
    user_id: int = None, 
    status: str = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    查询订单信息。
    当用户询问订单状态、物流信息、订单详情时使用此工具。

    Args:
        order_id: 订单编号，精确查询
        user_id: 用户ID，查询该用户的所有订单
        status: 订单状态筛选：pending, paid, shipped, delivered, cancelled
        limit: 返回数量限制，默认10条

    Returns:
        订单列表
    """
    # 从数据库查询订单
    orders = order_service.query_orders(
        order_id=order_id,
        user_id=user_id,
        status=status,
        limit=limit
    )

    return [
        {
            "id": order.id,
            "order_no": order.order_no,
            "product": order.product_name,
            "amount": float(order.amount),
            "status": order.status,
            "created_at": order.created_at.isoformat(),
        }
        for order in orders
    ]
```

### 5\.1\.3 工具调用流程

1. LLM 判断是否需要调用工具

2. LLM 生成工具调用参数（JSON 格式）

3. 解析并执行工具调用

4. 处理工具返回结果

5. 将结果返回给 LLM 继续推理

6. 重复直到得出最终答案

### 5\.1\.4 工具安全

- **参数校验**：所有工具参数必须严格校验

- **权限控制**：不同用户可调用的工具不同

- **执行沙箱**：代码执行类工具在沙箱中运行

- **操作审计**：所有工具调用记录日志

- **频率限制**：防止恶意调用

- **确认机制**：高危操作需要用户确认

## 5\.2 RAG 检索增强生成

### 5\.2\.1 什么是 RAG

**RAG（Retrieval\-Augmented Generation，检索增强生成）**是一种将信息检索与大语言模型结合的技术，通过从外部知识库检索相关信息，增强 LLM 的回答质量。

RAG 解决的核心问题：

- LLM 知识截止日期问题

- 私有数据和领域知识问题

- 幻觉（Hallucination）问题

- 数据安全与隐私问题

### 5\.2\.2 RAG 架构

#### 离线处理（索引阶段）

1. 文档采集与清洗

2. 文档分块（Chunking）

3. 向量化（Embedding）

4. 存入向量数据库

#### 在线处理（检索阶段）

1. 用户问题向量化

2. 向量相似度检索

3. 检索结果重排序

4. 构建增强提示词

5. LLM 生成回答

### 5\.2\.3 文档分块策略

| 分块方法   | 特点             | 适用场景             |
| ------ | -------------- | ---------------- |
| 固定大小分块 | 简单、按字符数分割      | 通用场景             |
| 语义分块   | 按语义边界分割，保持完整性  | 结构化文档            |
| 递归分块   | 按标题、段落递归分割     | Markdown、HTML 文档 |
| 父子分块   | 大粒度检索 \+ 小粒度返回 | 需要上下文的场景         |

### 5\.2\.4 RAG 优化技巧

1. **优化分块**：合适的分块大小和重叠

2. **优化 Embedding**：选择合适的向量模型

3. **混合检索**：向量检索 \+ 关键词检索结合

4. **重排序（Rerank）**：用交叉编码器对结果重排

5. **查询改写**：用 LLM 优化用户查询

6. **多轮检索**：多次检索，逐步细化

7. **元数据过滤**：按时间、类型等过滤

### 5\.2\.5 RAG 实现代码

```python
from typing import List, Dict, Any, Optional
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.document_loaders import TextLoader, PyPDFLoader, Docx2txtLoader
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor
from app.config import settings

class RAGService:
    """RAG 检索增强生成服务"""

    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model=settings.EMBEDDING_MODEL,
        )
        self.vector_store = Chroma(
            persist_directory=settings.CHROMA_PERSIST_DIR,
            embedding_function=self.embeddings,
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
        )

    def add_document(self, file_path: str, metadata: Dict[str, Any] = None):
        """添加文档到知识库"""
        # 根据文件类型选择加载器
        if file_path.endswith(".txt"):
            loader = TextLoader(file_path)
        elif file_path.endswith(".pdf"):
            loader = PyPDFLoader(file_path)
        elif file_path.endswith(".docx"):
            loader = Docx2txtLoader(file_path)
        else:
            raise ValueError(f"不支持的文件类型: {file_path}")

        # 加载并分块
        documents = loader.load()
        splits = self.text_splitter.split_documents(documents)

        # 添加元数据
        if metadata:
            for doc in splits:
                doc.metadata.update(metadata)

        # 存入向量数据库
        self.vector_store.add_documents(splits)
        self.vector_store.persist()

    def search(
        self, 
        query: str, 
        top_k: int = 5,
        filter: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """检索相关文档"""
        docs = self.vector_store.similarity_search_with_score(
            query,
            k=top_k,
            filter=filter,
        )

        return [
            {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": float(score),
            }
            for doc, score in docs
        ]

    def search_with_rerank(
        self,
        query: str,
        top_k: int = 5,
        filter: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """带重排序的检索"""
        # 先检索更多候选
        candidates = self.search(query, top_k=top_k * 3, filter=filter)

        # 使用 LLM 进行重排序（简化版，实际可用交叉编码器）
        # 这里可以接入 Cohere Rerank 或 bge-reranker 等专业模型
        scored = []
        for doc in candidates:
            # 简化的相关性评分逻辑
            relevance = self._calculate_relevance(query, doc["content"])
            scored.append({**doc, "relevance": relevance})

        # 按相关性排序
        scored.sort(key=lambda x: x["relevance"], reverse=True)

        return scored[:top_k]

    def _calculate_relevance(self, query: str, content: str) -> float:
        """计算查询与内容的相关性（简化版）"""
        # 实际项目中使用专门的 rerank 模型
        query_words = set(query.lower().split())
        content_words = set(content.lower().split())
        overlap = len(query_words & content_words)
        return overlap / max(len(query_words), 1)

    def build_context(self, docs: List[Dict[str, Any]]) -> str:
        """构建检索上下文"""
        context_parts = []
        for i, doc in enumerate(docs, 1):
            context_parts.append(
                f"【参考资料 {i}】\n{doc[content]}\n来源: {doc[metadata].get(source, 未知)}"
            )
        return "\n\n".join(context_parts)

# 全局实例
rag_service = RAGService()
```

### 5\.2\.6 向量数据库选型

| 数据库      | 类型            | 特点              | 适用场景              |
| -------- | ------------- | --------------- | ----------------- |
| ChromaDB | 轻量级           | Python 原生、简单易用  | 原型开发、小型项目         |
| FAISS    | 库             | Facebook 开源、性能好 | 高性能检索             |
| Milvus   | 分布式           | 云原生、可扩展         | 大规模生产环境           |
| Pinecone | SaaS          | 托管服务、无需运维       | 快速上线              |
| Weaviate | 开源            | 功能丰富、模块化        | 中等规模项目            |
| pgvector | PostgreSQL 扩展 | 与关系库结合          | 已有 PostgreSQL 的项目 |

---

# 六、多 Agent 协作系统

## 6\.1 为什么需要多 Agent

单个 Agent 能力有限，复杂任务需要多个专业 Agent 协作完成，类似人类团队分工：

- **专业分工**：每个 Agent 专注一个领域，能力更专业

- **复杂任务分解**：大任务拆分为小任务，逐个攻克

- **并行处理**：多个 Agent 同时工作，提高效率

- **容错性强**：单个 Agent 失败不影响整体

- **可扩展性**：新增能力只需添加新 Agent

## 6\.2 多 Agent 架构模式

### 6\.2\.1 层级式（Hierarchical）

一个管理者 Agent 统筹多个工作 Agent，自上而下分配任务。

**特点**：结构清晰、易于管理、适合有明确流程的任务

**示例**：项目经理 \+ 开发 \+ 测试 \+ 设计

### 6\.2\.2 平等式（Collaborative）

多个 Agent 地位平等，通过消息传递协作。

**特点**：灵活、适应性强、适合开放式任务

**示例**：圆桌讨论、头脑风暴

### 6\.2\.3 流水线式（Pipeline）

任务按流程流转，每个 Agent 负责一个环节。

**特点**：效率高、标准化、适合有固定流程的任务

**示例**：内容生产流水线（选题 → 写作 → 编辑 → 审核 → 发布）

### 6\.2\.4 混合式

结合以上多种模式，复杂系统通常采用混合架构。

## 6\.3 典型多 Agent 角色设计

| 角色               | 职责             | 核心能力       |
| ---------------- | -------------- | ---------- |
| 协调者（Coordinator） | 任务分配、进度跟踪、结果整合 | 规划能力、沟通能力  |
| 研究员（Researcher）  | 信息收集、资料检索、事实核查 | 检索能力、信息整合  |
| 分析师（Analyst）     | 数据分析、趋势洞察、报告生成 | 数据分析、逻辑推理  |
| 程序员（Coder）       | 代码编写、调试、代码审查   | 编程能力、问题排查  |
| 评论家（Critic）      | 质量评估、问题发现、优化建议 | 批判性思维、质量意识 |
| 执行者（Executor）    | 工具调用、操作执行、状态反馈 | 工具使用、执行力   |

## 6\.4 多 Agent 通信机制

- **消息队列**：Agent 之间通过消息队列传递信息

- **共享内存**：通过共享的工作空间交换数据

- **黑板模式**：所有 Agent 读写同一个"黑板"

- **事件驱动**：发布订阅模式，事件触发响应

## 6\.5 AutoGen 多 Agent 框架

AutoGen 是微软开源的多 Agent 框架，支持灵活的 Agent 协作：

```python
import autogen

# 配置 LLM
llm_config = {
    "config_list": [
        {
            "model": "gpt-4",
            "api_key": "your-api-key",
        }
    ],
    "temperature": 0.7,
}

# 创建用户代理（人类用户）
user_proxy = autogen.UserProxyAgent(
    name="User_proxy",
    system_message="人类用户",
    human_input_mode="TERMINATE",
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
    code_execution_config={"work_dir": "coding"},
)

# 创建产品经理 Agent
pm = autogen.AssistantAgent(
    name="Product_Manager",
    system_message="""你是产品经理，负责需求分析和产品设计。
    收到需求后，分析用户需求，输出产品需求文档（PRD）。
    完成后说 TERMINATE。""",
    llm_config=llm_config,
)

# 创建程序员 Agent
coder = autogen.AssistantAgent(
    name="Coder",
    system_message="""你是资深程序员，负责代码实现。
    根据产品需求编写高质量的 Python 代码。
    代码要完整、可运行、有注释。
    完成后说 TERMINATE。""",
    llm_config=llm_config,
)

# 创建测试工程师 Agent
tester = autogen.AssistantAgent(
    name="Tester",
    system_message="""你是测试工程师，负责代码质量。
    对代码进行审查，编写测试用例，确保代码质量。
    发现问题要指出并给出修改建议。
    完成后说 TERMINATE。""",
    llm_config=llm_config,
)

# 创建群组聊天
groupchat = autogen.GroupChat(
    agents=[user_proxy, pm, coder, tester],
    messages=[],
    max_round=20,
)

# 创建群组管理员
manager = autogen.GroupChatManager(
    groupchat=groupchat,
    llm_config=llm_config,
)

# 启动对话
user_proxy.initiate_chat(
    manager,
    message="我需要一个用户管理系统，支持用户的增删改查功能。",
)
```

---

# 七、Agent 评估与优化

## 7\.1 为什么需要评估

Agent 系统的输出具有不确定性，需要建立评估体系来：

- 衡量 Agent 的能力和效果

- 发现问题和短板

- 指导优化方向

- 确保质量稳定

- 对比不同方案的优劣

## 7\.2 评估维度

| 维度    | 指标            | 评估方法        |
| ----- | ------------- | ----------- |
| 回答质量  | 准确性、相关性、完整性   | 人工评估、LLM 评分 |
| 工具使用  | 调用准确率、参数正确率   | 自动化测试、日志分析  |
| 任务完成率 | 任务成功率、完成时间    | 任务集测试       |
| 安全性   | 越狱率、有害输出率     | 红队测试、安全扫描   |
| 性能    | 响应时间、token 消耗 | 性能测试、监控     |
| 用户体验  | 满意度、留存率       | 用户反馈、数据分析   |

## 7\.3 评估方法

### 7\.3\.1 自动化评估

- **基于规则**：关键词匹配、格式检查、逻辑校验

- **LLM 评分**：用更强的 LLM 作为裁判打分

- **向量相似度**：与标准答案的语义相似度

- **工具调用准确率**：工具选择和参数正确率

### 7\.3\.2 人工评估

- **专家评审**：领域专家打分

- **用户反馈**：点赞、点踩、评论

- **A/B 测试**：对比不同版本效果

### 7\.3\.3 评估数据集

建立标准化的测试集，包含：

- 常见问题（简单）

- 复杂问题（需要多步推理）

- 边界情况（异常输入）

- 对抗样本（越狱、诱导）

- 真实用户问题抽样

## 7\.4 常见优化方向

1. **提示词优化**：迭代优化系统提示词

2. **工具优化**：改进工具描述、增加工具数量

3. **记忆优化**：调整记忆策略和窗口大小

4. **RAG 优化**：分块、检索、重排序优化

5. **模型选型**：选择更合适的模型

6. **流程优化**：调整 Agent 工作流程

## 7\.5 可观测性

建立完善的可观测性体系：

- **日志**：完整记录每次交互的输入输出、工具调用、耗时

- **指标**：请求量、成功率、平均耗时、token 消耗

- **追踪**：分布式追踪，查看完整调用链

- **告警**：异常情况及时告警

- **看板**：可视化展示系统运行状态

```python
{
  "request_id": "req_abc123",
  "timestamp": "2024-01-01T12:00:00Z",
  "user_id": "user_001",
  "conversation_id": "conv_001",
  "input": "帮我查一下昨天的订单",
  "model": "gpt-4",
  "temperature": 0.7,
  "tool_calls": [
    {
      "tool_name": "query_orders",
      "parameters": {"date": "2023-12-31"},
      "result_count": 3,
      "execution_time": 0.15
    }
  ],
  "output": "您昨天有3个订单，分别是...",
  "total_time": 2.35,
  "input_tokens": 500,
  "output_tokens": 200,
  "total_tokens": 700,
  "cost": 0.0021,
  "user_rating": null,
  "error": null
}
```

---

# 八、企业级落地实践

## 8\.1 企业级 Agent 架构

生产环境的 Agent 系统需要考虑更多工程化因素：

| 模块       | 技术选型                  | 说明          |
| -------- | --------------------- | ----------- |
| API 网关   | Nginx / Kong          | 路由、限流、鉴权    |
| 应用服务     | FastAPI \+ Uvicorn    | 异步高性能服务     |
| Agent 框架 | LangChain / 自研        | Agent 编排与执行 |
| LLM 接入   | 多模型适配层                | 支持多家模型、可切换  |
| 向量数据库    | Milvus / Chroma       | 知识检索        |
| 关系数据库    | MySQL / PostgreSQL    | 业务数据存储      |
| 缓存       | Redis                 | 会话、限流、缓存    |
| 消息队列     | Kafka / RabbitMQ      | 异步任务、解耦     |
| 监控告警     | Prometheus \+ Grafana | 指标监控与可视化    |
| 日志系统     | ELK / Loki            | 日志收集与分析     |
| 链路追踪     | Jaeger / Zipkin       | 分布式追踪       |

## 8\.2 高可用设计

1. **多实例部署**：无状态服务，水平扩展

2. **多模型容灾**：主模型故障时自动切换备用模型

3. **降级策略**：LLM 不可用时降级到规则引擎

4. **限流熔断**：保护系统不被打垮

5. **重试机制**：网络波动自动重试

6. **超时控制**：所有外部调用设置超时

## 8\.3 安全与合规

### 8\.3\.1 数据安全

- 敏感数据脱敏

- 数据加密存储和传输

- 访问权限控制

- 操作审计日志

- 数据隔离（多租户）

### 8\.3\.2 内容安全

- 输入内容审核（防注入、防越狱）

- 输出内容审核（防有害内容）

- 敏感词过滤

- 合规性检查

### 8\.3\.3 隐私保护

- 用户数据最小化原则

- 用户数据可删除

- 明确的数据使用说明

- 符合 GDPR、等保等法规要求

## 8\.4 成本控制

LLM 调用成本是 Agent 系统的主要成本，需要重点优化：

| 优化方向     | 具体措施              | 效果          |
| -------- | ----------------- | ----------- |
| 缓存       | 常见问题缓存答案          | 减少重复调用      |
| 模型分层     | 简单问题用小模型，复杂问题用大模型 | 降低平均成本      |
| Token 优化 | 精简提示词、压缩上下文       | 减少 token 消耗 |
| 限流       | 用户级、系统级限流         | 控制总用量       |
| 批处理      | 合并多个请求批量处理        | 提高效率        |
| 私有化部署    | 使用开源模型本地部署        | 大规模场景更省     |

## 8\.5 典型落地场景

### 8\.5\.1 智能客服

- 7x24 小时在线服务

- 自动解答常见问题

- 复杂问题转人工

- 知识库持续学习

### 8\.5\.2 企业知识助手

- 内部文档问答

- 规章制度查询

- 新人培训辅助

- 知识沉淀与传承

### 8\.5\.3 代码助手

- 代码生成与补全

- 代码审查与优化建议

- Bug 定位与修复

- 技术文档生成

### 8\.5\.4 数据分析助手

- 自然语言查数据

- 自动生成 SQL

- 数据可视化

- 分析报告生成

---

# 九、Python Agent 开发环境搭建

本章详细介绍 Python Agent 开发环境的搭建步骤，与《Python 企业级开发技术白皮书》配套使用。更多 Python 工程化规范请参考配套文档。

## 9\.1 环境要求

| 软件     | 版本要求     | 说明               |
| ------ | -------- | ---------------- |
| Python | 3\.11\+  | 推荐 3\.11 或 3\.12 |
| uv     | 最新版      | 包管理工具            |
| Docker | 20\.10\+ | 容器化部署            |
| MySQL  | 8\.0\+   | 关系数据库            |
| Redis  | 7\.0\+   | 缓存               |
| Git    | 最新版      | 版本控制             |

## 9\.2 项目初始化

```bash
# 1. 创建项目目录
mkdir my-agent-project
cd my-agent-project

# 2. 使用 uv 初始化项目
uv init --python 3.12

# 3. 创建目录结构
mkdir -p app/{api,core,models,schemas,services,agents,tools,memory,utils}
mkdir -p tests/{unit,integration}
mkdir -p scripts docs data

# 4. 添加核心依赖
uv add fastapi uvicorn sqlalchemy aiomysql pydantic-settings
uv add langchain langchain-openai chromadb
uv add python-multipart python-jose[cryptography] passlib[bcrypt]
uv add redis python-dotenv loguru

# 5. 添加开发依赖
uv add --dev pytest pytest-asyncio pytest-cov
uv add --dev black isort flake8 mypy pre-commit
uv add --dev httpx testcontainers

# 6. 初始化 git
git init

# 7. 创建 .gitignore
cat > .gitignore << EOF
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual Environment
.venv/
env/
venv/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Environment
.env
.env.local

# Database
*.db
*.sqlite

# Logs
*.log
logs/

# Data
data/
chroma/

# OS
.DS_Store
Thumbs.db
EOF

# 8. 创建 .env.example
cat > .env.example << EOF
# 应用配置
APP_NAME=My Agent
APP_VERSION=1.0.0
DEBUG=false
ENV=development

# 服务配置
HOST=0.0.0.0
PORT=8000

# 数据库配置
DATABASE_URL=mysql+aiomysql://root:password@localhost:3306/agent_db

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# OpenAI 配置
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096
EMBEDDING_MODEL=text-embedding-ada-002

# 向量数据库
CHROMA_PERSIST_DIR=./data/chroma

# JWT 配置
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

# 日志配置
LOG_LEVEL=INFO
EOF

echo "项目初始化完成！"
```

## 9\.3 本地开发环境

使用 Docker Compose 一键启动本地依赖服务：

```yaml
version: "3.8"

services:
  mysql:
    image: mysql:8.0
    ports:
      - "3306:3306"
    environment:
      - MYSQL_ROOT_PASSWORD=password
      - MYSQL_DATABASE=agent_db
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  chroma:
    image: chromadb/chroma:latest
    ports:
      - "8001:8000"
    volumes:
      - chroma_data:/chroma/chroma
    environment:
      - IS_PERSISTENT=TRUE
      - ANONYMIZED_TELEMETRY=FALSE

volumes:
  mysql_data:
  redis_data:
  chroma_data:
```

启动命令：

```bash
# 启动所有服务
docker-compose up -d

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 9\.4 项目核心代码结构

### 9\.4\.1 配置模块

```python
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    """应用配置"""

    # 基础配置
    APP_NAME: str = "AI Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENV: str = "development"

    # 服务配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置
    DATABASE_URL: str = "mysql+aiomysql://root:password@localhost:3306/agent_db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM 配置
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 4096
    EMBEDDING_MODEL: str = "text-embedding-ada-002"

    # 向量数据库
    CHROMA_PERSIST_DIR: str = "./data/chroma"

    # JWT 配置
    JWT_SECRET_KEY: str = "your-secret-key"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Agent 配置
    AGENT_MAX_ITERATIONS: int = 10
    AGENT_MEMORY_WINDOW_SIZE: int = 20

    # 日志配置
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ORIGINS: List[str] = ["*"]

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

### 9\.4\.2 数据库模块

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
from app.config import settings

class Base(DeclarativeBase):
    """基础模型类"""
    pass

class DatabaseManager:
    """数据库管理器"""

    def __init__(self):
        self.engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=settings.DEBUG,
        )
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """获取数据库会话"""
        async with self.async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def create_tables(self):
        """创建所有表"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self):
        """删除所有表"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self):
        """关闭引擎"""
        await self.engine.dispose()

# 全局实例
db_manager = DatabaseManager()
```

### 9\.4\.3 Agent 服务

```python
from typing import Dict, Any, AsyncGenerator, List
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import BaseMessage
from app.config import settings
from app.core.llm import llm
from app.core.memory import conversation_memory
from app.tools import get_tools

class AgentService:
    """Agent 服务"""

    def __init__(self):
        self.tools = get_tools()
        self._agent_executor = self._create_agent()

    def _create_agent(self) -> AgentExecutor:
        """创建 Agent 执行器"""
        system_prompt = """你是一个智能助手，能够帮助用户解决各种问题。

你可以使用工具来获取信息和执行操作。
如果问题无法直接回答，请使用相关工具。
回答要简洁、准确、专业。
如果不确定，就说不知道，不要编造。"""

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])

        agent = create_openai_tools_agent(
            llm=llm,
            tools=self.tools,
            prompt=prompt,
        )

        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=settings.DEBUG,
            max_iterations=settings.AGENT_MAX_ITERATIONS,
            handle_parsing_errors=True,
        )

    async def chat(
        self, 
        user_id: str, 
        conversation_id: str, 
        message: str
    ) -> Dict[str, Any]:
        """对话接口"""
        # 获取历史消息
        chat_history = await conversation_memory.get_history(conversation_id)

        # 执行 Agent
        result = await self._agent_executor.ainvoke({
            "input": message,
            "chat_history": chat_history,
        })

        # 保存到记忆
        await conversation_memory.add_message(conversation_id, "user", message)
        await conversation_memory.add_message(conversation_id, "assistant", result["output"])

        return {
            "response": result["output"],
            "conversation_id": conversation_id,
        }

    async def chat_stream(
        self, 
        user_id: str, 
        conversation_id: str, 
        message: str
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        chat_history = await conversation_memory.get_history(conversation_id)

        full_response = ""
        async for chunk in self._agent_executor.astream({
            "input": message,
            "chat_history": chat_history,
        }):
            if "output" in chunk:
                full_response += chunk["output"]
                yield chunk["output"]

        # 保存完整响应
        await conversation_memory.add_message(conversation_id, "user", message)
        await conversation_memory.add_message(conversation_id, "assistant", full_response)

# 全局实例
agent_service = AgentService()
```

### 9\.4\.4 API 接口

```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from app.services.agent_service import agent_service
from app.api.deps import get_current_user

router = APIRouter()

class ChatRequest(BaseModel):
    """对话请求"""
    message: str
    conversation_id: Optional[str] = None

class ChatResponse(BaseModel):
    """对话响应"""
    response: str
    conversation_id: str

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """同步对话接口"""
    try:
        result = await agent_service.chat(
            user_id=current_user["id"],
            conversation_id=request.conversation_id or "default",
            message=request.message,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """流式对话接口"""
    async def event_generator():
        try:
            async for chunk in agent_service.chat_stream(
                user_id=current_user["id"],
                conversation_id=request.conversation_id or "default",
                message=request.message,
            ):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
```

## 9\.5 启动应用

```bash
# 1. 确保依赖服务已启动
docker-compose up -d mysql redis

# 2. 初始化数据库
uv run python scripts/init_db.py

# 3. 启动开发服务器
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. 访问 API 文档
# 打开浏览器访问 http://localhost:8000/docs
```

---

# 十、未来发展趋势

## 10\.1 技术发展方向

1. **模型能力增强**：推理能力更强、上下文更长、多模态

2. **Agent 自主化**：从工具调用到自主决策、长期规划

3. **多模态 Agent**：支持文本、图像、语音、视频等多种模态

4. **端侧 Agent**：端侧模型 \+ 云端协同，保护隐私

5. **Agent 生态**：工具市场、Agent 商店、能力交易

## 10\.2 应用场景扩展

- **数字员工**：替代重复性工作，7x24 小时工作

- **科学研究**：辅助科研、文献综述、实验设计

- **软件开发**：全流程 AI 辅助，从需求到部署

- **教育培训**：个性化学习、智能辅导

- **医疗健康**：辅助诊断、健康管理

- **金融服务**：智能投顾、风险控制

## 10\.3 挑战与机遇

| 挑战       | 机遇              |
| -------- | --------------- |
| 模型幻觉与可靠性 | Agent 评估与质量保障技术 |
| 数据安全与隐私  | 私有化部署、联邦学习      |
| 成本高昂     | 模型压缩、高效推理、小模型   |
| 可解释性差    | 可观测性、过程透明化      |
| 伦理与合规    | AI 治理、监管科技      |
| 人才短缺     | 低代码 Agent 开发平台  |

---

# 附录

## A\. 常用工具与框架

| 类别           | 工具/框架                     | 说明                  |
| ------------ | ------------------------- | ------------------- |
| Agent 框架     | LangChain                 | 最流行的 LLM 应用开发框架     |
| Agent 框架     | LlamaIndex                | 专注于 RAG 和数据连接       |
| Agent 框架     | AutoGen                   | 多 Agent 协作框架        |
| Agent 框架     | CrewAI                    | 角色扮演式多 Agent 框架     |
| 向量数据库        | ChromaDB                  | 轻量级向量数据库            |
| 向量数据库        | Milvus                    | 云原生向量数据库            |
| Embedding 模型 | text\-embedding\-ada\-002 | OpenAI 嵌入模型         |
| Embedding 模型 | bge\-zh                   | 中文开源嵌入模型            |
| 重排序模型        | bge\-reranker             | 中文重排序模型             |
| Web 框架       | FastAPI                   | 高性能异步 Python Web 框架 |

## B\. 学习资源

- LangChain 官方文档：https://python\.langchain\.com/

- OpenAI API 文档：https://platform\.openai\.com/docs

- Prompt Engineering Guide：https://www\.promptingguide\.ai/

- Andrew Ng Agent 课程：DeepLearning\.AI

- HuggingFace 文档：https://huggingface\.co/docs

## C\. 相关文档

- 《Python 企业级开发技术白皮书》\- Python 开发规范与最佳实践

- 《企业级 RAG 系统设计指南》\- 检索增强生成系统设计

- 《大模型应用安全规范》\- 安全与合规要求

> （注：部分内容可能由 AI 生成）
