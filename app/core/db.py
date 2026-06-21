"""SQLAlchemy 异步数据库管理器

提供延迟连接的 DatabaseManager 单例，通过 aiomysql 驱动访问 MySQL。
当 config.mysql_url 为空时自动降级为 no-op，不影响系统正常运行。

使用方式（Phase 2+）:
    from app.core.db import db_manager

    async with db_manager.get_session() as session:
        result = await session.execute(...)
"""

from __future__ import annotations

from typing import AsyncGenerator, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import config


class DatabaseManager:
    """SQLAlchemy 异步数据库管理器（模块级单例）

    关键设计：Engine 不在 import 时创建，首次访问 .engine 时才连接。
    当 mysql_url="" 时，_enabled=False，所有方法自动降级为 no-op。
    """

    def __init__(self) -> None:
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._enabled: bool = bool(config.mysql_url)

    # ── engine 属性（延迟创建）──────────────────────────────────────────

    @property
    def engine(self) -> Optional[AsyncEngine]:
        """延迟创建并返回异步 engine。未配置 MySQL 时返回 None。"""
        if not self._enabled:
            return None
        if self._engine is None:
            logger.info(f"创建 MySQL 异步引擎 -> {config.mysql_url}")
            self._engine = create_async_engine(
                config.mysql_url,
                echo=config.debug,
                pool_size=10,          # 连接池大小
                max_overflow=20,       # 超出 pool_size 后可额外创建的连接数
                pool_recycle=1800,     # 30 分钟后回收连接（避免 MySQL wait_timeout）
                pool_pre_ping=False,   # aiomysql 的 ping() 签名不兼容，用 pool_recycle 保证连接有效性
            )
            self._session_factory = async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False,  # 提交后不过期属性，FastAPI 依赖注入友好
            )
        return self._engine

    # ── session_factory 属性 ────────────────────────────────────────────

    @property
    def session_factory(self) -> Optional[async_sessionmaker[AsyncSession]]:
        """延迟获取 session 工厂（随 engine 一起创建）。"""
        if not self._enabled:
            return None
        _ = self.engine  # 触发延迟创建
        return self._session_factory

    # ── FastAPI 依赖：获取数据库会话 ─────────────────────────────────────

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """异步生成器，用于 FastAPI Depends() 注入数据库会话。

        用法:
            @router.get("/items")
            async def get_items(db: AsyncSession = Depends(db_manager.get_session)):
                ...

        当 MySQL 未配置时抛出 RuntimeError，由调用方处理。
        """
        factory = self.session_factory
        if factory is None:
            raise RuntimeError(
                "MySQL 未配置。请在 .env 中设置 MYSQL_URL 以启用数据库功能。"
            )
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()

    # ── 健康检查 ─────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """检查数据库是否可达。未配置 MySQL 时返回 False。"""
        if not self._enabled or self._engine is None:
            return False
        try:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning(f"MySQL 健康检查失败: {e}")
            return False

    # ── 关闭连接 ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        """释放 engine，断开所有数据库连接。"""
        if self._engine is not None:
            logger.info("释放 MySQL engine")
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # ── 用户更新（Phase 6 bugfix: 用户信息修改）──────────────────────────

    async def update_user(self, user_id: int, **kwargs) -> bool:
        """更新用户字段。只更新非 None 的 kwargs。

        调用方负责密码哈希等预处理，此处仅执行 SQL UPDATE。
        """
        if not self._enabled:
            return False

        # 过滤掉 None 值
        updates = {k: v for k, v in kwargs.items() if v is not None}
        if not updates:
            return True  # 无需更新

        try:
            from app.models.db import User
            from sqlalchemy import update

            session_iter = self.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return False

        try:
            await session.execute(
                update(User).where(User.id == user_id).values(**updates)
            )
            await session.commit()
            logger.info(f"用户 {user_id} 已更新字段: {list(updates.keys())}")
            return True
        except Exception as e:
            await session.rollback()
            logger.warning(f"更新用户 {user_id} 失败: {e}")
            return False
        finally:
            try:
                await session.close()
            except Exception:
                pass


# 全局单例（同 milvus_manager / config 模式）
db_manager = DatabaseManager()
