"""RAG Agent 服务子包

拆分自 rag_agent_service.py（1209 行），按职责拆为：
- agent.py   — 核心生命周期 (init, checkpointer, agent, MCP, session, cleanup)
- detector.py — 静态检测方法 (代码修改 / 诊断查询 / 来源提取 / 工具提取)
- query.py   — 非流式查询
- stream.py  — 流式查询

全局单例 rag_agent_service 从 agent.py 导入。
"""

from app.services.rag.agent import RagAgentService, rag_agent_service

__all__ = ["RagAgentService", "rag_agent_service"]
