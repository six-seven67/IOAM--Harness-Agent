"""向量嵌入服务模块 - 基于 LangChain Embeddings 标准接口

Phase 5: 集成 Redis Embedding 缓存（静默降级，缓存不可用时正常调用 API）。
"""

import asyncio
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from openai import OpenAI
from loguru import logger

from app.config import config
from app.core.redis import redis_manager


def _run_async_safe(coro, fire_and_forget: bool = False):
    """在 sync 上下文中安全地运行 async 协程。

    优先复用运行中的 event loop（FastAPI 请求上下文），
    否则用 asyncio.run() 创建临时 loop。

    fire_and_forget=True 时不等待结果，静默吞掉异常。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的 loop（如 CLI 脚本）→ 创建临时 loop
        if fire_and_forget:
            return None
        try:
            return asyncio.run(coro)
        except Exception:
            return None

    # 有运行中的 loop → 创建 Task（fire-and-forget）或使用 nest_asyncio 方案
    if fire_and_forget:
        try:
            loop.create_task(coro)
        except Exception:
            pass
        return None

    # 需要在 sync 函数中等待 async 结果 → 使用 asyncio.run_coroutine_threadsafe
    # 但这需要 loop 在另一个线程运行。简单降级：跳过缓存
    logger.debug("运行中 event loop 不支持 sync→async 等待，跳过 Redis 缓存")
    return None


class DashScopeEmbeddings(Embeddings):
    """阿里云 DashScope Text Embedding (OpenAI 兼容模式)

    实现 LangChain 标准 Embeddings 接口:
    - embed_documents(texts: List[str]) → List[List[float]]: 批量嵌入文档
    - embed_query(text: str) → List[float]: 嵌入单个查询

    DashScope API 限制: 单次请求最多 20 条文本，超出自动分批。
    """

    _BATCH_SIZE = 6  # DashScope embedding API 单次最大输入条数（保守值，避免波动）

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ):
        """
        初始化 DashScope Embeddings
        
        Args:
            api_key: DashScope API Key
            model: 嵌入模型名称
            dimensions: 向量维度
        """
        if not api_key or api_key == "your-api-key-here":
            raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")
        
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model
        self.dimensions = dimensions
        
        # 打印初始化信息
        masked_key = self._mask_api_key(api_key)
        logger.info(
            f"DashScope Embeddings 初始化完成 - "
            f"模型: {model}, 维度: {dimensions}, API Key: {masked_key}"
        )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """掩码 API Key 用于日志"""
        if len(api_key) > 8:
            return f"{api_key[:8]}...{api_key[-4:]}"
        return "***"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档列表 (LangChain 标准接口)

        Phase 5: Redis 缓存加速 — 逐个检查缓存，仅对未命中项调用 API。

        Args:
            texts: 文本列表

        Returns:
            List[List[float]]: 嵌入向量列表
        """
        if not texts:
            return []

        try:
            # Phase 5: 检查缓存，找出未命中项
            cached_results: List[Optional[List[float]]] = []
            uncached_texts: List[str] = []
            uncached_indices: List[int] = []

            for i, text in enumerate(texts):
                vec = _run_async_safe(redis_manager.get_cached_embedding(text))
                cached_results.append(vec)
                if vec is None:
                    uncached_texts.append(text)
                    uncached_indices.append(i)

            if uncached_texts:
                total_api_calls = min(
                    len(uncached_texts),
                    (len(uncached_texts) + self._BATCH_SIZE - 1) // self._BATCH_SIZE,
                )
                logger.info(
                    f"批量嵌入 {len(texts)} 个文档 "
                    f"(缓存命中: {len(texts) - len(uncached_texts)}, "
                    f"API 调用: {len(uncached_texts)} 条 / {total_api_calls} 批)"
                )

                # DashScope API 限制单次 ≤20 条，超出自动分批
                for batch_start in range(0, len(uncached_texts), self._BATCH_SIZE):
                    batch_end = min(batch_start + self._BATCH_SIZE, len(uncached_texts))
                    batch_texts = uncached_texts[batch_start:batch_end]

                    response = self.client.embeddings.create(
                        model=self.model,
                        input=batch_texts,
                        dimensions=self.dimensions,
                        encoding_format="float",
                    )

                    for j, item in enumerate(response.data):
                        global_j = batch_start + j
                        idx = uncached_indices[global_j]
                        cached_results[idx] = item.embedding
                        # 写入缓存（fire-and-forget）
                        _run_async_safe(
                            redis_manager.set_cached_embedding(
                                uncached_texts[global_j], item.embedding, ttl=3600
                            ),
                            fire_and_forget=True,
                        )

                logger.debug(f"批量嵌入完成, 维度: {len(cached_results[0])}")
            else:
                logger.info(
                    f"批量嵌入 {len(texts)} 个文档全部缓存命中"
                )

            return cached_results  # type: ignore[return-value]

        except Exception as e:
            logger.error(f"批量嵌入失败: {e}")
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def embed_query(self, text: str) -> List[float]:
        """
        嵌入单个查询文本 (LangChain 标准接口)

        Phase 5: Redis 缓存加速 — 已缓存的文本跳过 API 调用。
        缓存不可用时静默降级，正常调用 API。

        Args:
            text: 查询文本

        Returns:
            List[float]: 嵌入向量
        """
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")

        try:
            # Phase 5: 先查缓存（sync-safe: 优先复用运行中的 event loop）
            cached = _run_async_safe(redis_manager.get_cached_embedding(text))
            if cached is not None:
                logger.debug(f"Embedding 缓存命中, 长度: {len(text)} 字符")
                return cached

            logger.debug(f"嵌入查询, 长度: {len(text)} 字符")

            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float"
            )

            embedding = response.data[0].embedding
            logger.debug(f"查询嵌入完成, 维度: {len(embedding)}")

            # Phase 5: 写入缓存（fire-and-forget，静默降级）
            _run_async_safe(
                redis_manager.set_cached_embedding(text, embedding, ttl=3600),
                fire_and_forget=True,
            )

            return embedding

        except Exception as e:
            logger.error(f"查询嵌入失败: {e}")
            raise RuntimeError(f"查询嵌入失败: {e}") from e


# 全局单例
vector_embedding_service = DashScopeEmbeddings(
    api_key=config.dashscope_api_key,
    model=config.dashscope_embedding_model,
    dimensions=1024
)
