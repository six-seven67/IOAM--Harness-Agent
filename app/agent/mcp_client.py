"""
MCP 客户端管理模块

提供全局单例的 MCP (Model Context Protocol) 客户端，避免重复初始化。
支持多个 MCP 服务器的连接和管理，包括：
- 监控服务器 (monitor_server): 提供系统监控数据查询功能
- CLS 服务器 (cls_server): 提供日志查询和分析功能

主要特性：
1. 单例模式：确保整个应用只有一个 MCP 客户端实例
2. 重试机制：自动重试失败的 MCP 工具调用（指数退避策略）
3. 错误处理：优雅地处理连接失败和工具调用异常
4. 延迟初始化：仅在首次使用时创建客户端实例
"""

import asyncio
from typing import Optional, Dict, Any, List, Union

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent
from loguru import logger


# ==================== 全局变量 ====================
# 全局 MCP 客户端实例（延迟初始化）
# 使用 Optional 类型标注，初始为 None，在首次调用 get_mcp_client 时初始化
_mcp_client: Optional[MultiServerMCPClient] = None


# ==================== 工具函数 ====================
def format_exception_chain(exc: BaseException) -> str:
    """
    展开 ExceptionGroup / TaskGroup 异常链，便于日志定位真实子异常
    
    递归遍历异常链（包括 __cause__、__context__ 和 exceptions 属性），
    将嵌套的异常信息格式化为可读的字符串。
    
    Args:
        exc: 要格式化的异常对象
        
    Returns:
        str: 格式化后的异常链字符串，每行显示一个异常及其层级关系
        
    Example:
        >>> try:
        ...     raise ValueError("inner error")
        ... except ValueError as e:
        ...     raise RuntimeError("outer error") from e
        >>> # 输出: "RuntimeError: outer error\n  caused by: ValueError: inner error"
    """
    # 检查是否为异常组（ExceptionGroup/TaskGroup）
    sub_exceptions = getattr(exc, "exceptions", None)
    if sub_exceptions is not None:
        # 处理异常组：递归格式化每个子异常
        lines = [str(exc)]
        for i, sub in enumerate(sub_exceptions):
            lines.append(f"  [{i}] {format_exception_chain(sub)}")
        return "\n".join(lines)
    
    # 普通异常：格式化当前异常并递归处理原因异常
    msg = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return f"{msg}\n  caused by: {format_exception_chain(cause)}"
    return msg


async def load_mcp_tools_safe(
    client: MultiServerMCPClient,
) -> tuple[list[Union[BaseTool, Any]], str | None]:
    """
    安全地加载 MCP 工具，失败时不抛出异常
    
    封装 client.get_tools() 调用，捕获所有异常并返回友好的错误信息。
    用于应用启动时的工具加载阶段，避免因单个服务器不可用导致整个应用崩溃。
    
    Args:
        client: MCP 客户端实例
        
    Returns:
        tuple[list[Union[BaseTool, Any]], str | None]: 
            - 第一个元素：成功时返回工具列表，失败时返回空列表
            - 第二个元素：成功时为 None，失败时为错误信息字符串
            
    Example:
        >>> tools, error = await load_mcp_tools_safe(client)
        >>> if error:
        ...     logger.warning(f"加载工具失败: {error}")
        >>> else:
        ...     logger.info(f"成功加载 {len(tools)} 个工具")
    """
    try:
        tools = await client.get_tools()
        return tools, None
    except BaseException as e:
        # 捕获所有异常（包括 BaseException），确保不会向上抛出
        return [], format_exception_chain(e)


async def retry_interceptor(
    request: MCPToolCallRequest,
    handler,
    max_retries: int = 3,
    delay: float = 1.0,
):
    """
    MCP 工具调用重试拦截器
    
    当 MCP 工具调用失败时，使用指数退避策略自动重试。
    如果所有重试都失败，返回包含错误信息的结果而不是抛出异常，
    确保 Agent 流程不会因为单个工具调用失败而中断。
    
    指数退避策略：
    - 第 1 次重试：等待 1 秒 (delay * 2^0)
    - 第 2 次重试：等待 2 秒 (delay * 2^1)
    - 第 3 次重试：等待 4 秒 (delay * 2^2)
    
    MCPToolCallRequest 结构：
    - name: str - 工具名称（如 "query_cpu_metrics"）
    - args: dict[str, Any] - 工具参数（如 {"host": "server1", "period": "5m"}）
    - server_name: str - 服务器名称（如 "monitor_server"）
    
    Args:
        request: MCP 工具调用请求对象，包含工具名、参数和服务器信息
        handler: 实际的工具调用处理器（由 langchain-mcp-adapters 提供）
        max_retries: 最大重试次数（默认 3 次）
        delay: 初始延迟时间（秒，默认 1 秒），每次重试按指数增长
        
    Returns:
        CallToolResult: 
            - 成功时：返回工具的正常执行结果
            - 失败时：返回包含错误信息的 CallToolResult（isError=True）
            
    Note:
        此拦截器会记录每次重试的详细信息，便于调试和监控 MCP 服务的稳定性。
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # 记录工具调用尝试
            logger.info(
                f"调用 MCP 工具: {request.name} "
                f"(服务器: {request.server_name}, 第 {attempt + 1}/{max_retries} 次尝试)"
            )
            # 调用实际的处理器
            result = await handler(request)
            logger.info(f"MCP 工具 {request.name} 调用成功")
            return result
            
        except Exception as e:
            # 记录失败信息
            last_error = e
            logger.warning(
                f"MCP 工具 {request.name} 调用失败 "
                f"(第 {attempt + 1}/{max_retries} 次): {str(e)}"
            )
            
            # 如果不是最后一次尝试，等待后重试（指数退避）
            if attempt < max_retries - 1:
                wait_time = delay * (2 ** attempt)  # 指数退避计算
                logger.info(f"等待 {wait_time:.1f} 秒后重试...")
                await asyncio.sleep(wait_time)
    
    # 所有重试都失败，返回错误结果而不是抛出异常
    # 这样可以让 Agent 继续执行其他任务，而不是完全中断
    error_msg = f"工具 {request.name} 在 {max_retries} 次重试后仍然失败: {str(last_error)}"
    logger.error(error_msg)
    return CallToolResult(
        content=[TextContent(type="text", text=error_msg)],
        isError=True
    )


# ==================== 配置加载 ====================
# 从配置文件读取 MCP 服务器配置
from app.config import config

# 使用配置文件中定义的完整 MCP 服务器配置
# 配置示例：
# {
#     "monitor_server": {
#         "transport": "streamable-http",
#         "url": "http://localhost:8001/mcp"
#     },
#     "cls_server": {
#         "transport": "sse",
#         "url": "https://cls-api.tencentcloud.com/sse"
#     }
# }
DEFAULT_MCP_SERVERS = config.mcp_servers


# ==================== 核心客户端管理函数 ====================
async def get_mcp_client(
    servers: Optional[Dict[str, Dict[str, str]]] = None,
    tool_interceptors: Optional[List] = None,
    force_new: bool = False
) -> MultiServerMCPClient:
    """
    获取或初始化 MCP 客户端（不带重试拦截器）
    
    这是一个单例模式实现，确保整个应用只有一个 MCP 客户端实例（除非 force_new=True）。
    从 langchain-mcp-adapters 0.1.0 开始，MultiServerMCPClient 不再支持作为上下文管理器使用，
    直接创建实例即可使用。
    
    Args:
        servers: MCP 服务器配置字典
                 格式: {server_name: {"transport": "...", "url": "..."}}
                 如果为 None，则使用 DEFAULT_MCP_SERVERS
        tool_interceptors: 自定义工具拦截器列表
                          拦截器会在工具调用前后执行，可用于日志记录、权限检查等
        force_new: 是否强制创建新实例（用于特殊场景，如需要不同配置）
                  - True: 每次都创建新实例（不缓存）
                  - False: 使用单例模式（默认行为）
    
    Returns:
        MultiServerMCPClient: MCP 客户端实例，可用于调用 get_tools() 获取工具列表
        
    Note:
        - 此函数创建的客户端不包含重试拦截器
        - 如需重试功能，请使用 get_mcp_client_with_retry()
        - 单例实例存储在 _mcp_client 全局变量中
    """
    global _mcp_client
    
    # 如果请求新实例，直接创建并返回（不缓存）
    if force_new:
        logger.info("创建新的 MCP 客户端实例（非单例）")
        client = _create_mcp_client(
            servers or DEFAULT_MCP_SERVERS, 
            tool_interceptors
        )
        # 不再需要 __aenter__()，直接返回即可
        return client
    
    # 单例模式：如果已存在，直接返回
    if _mcp_client is None:
        logger.info("初始化全局 MCP 客户端...")
        _mcp_client = _create_mcp_client(
            servers or DEFAULT_MCP_SERVERS, 
            tool_interceptors
        )
        # 不再需要 __aenter__()，直接使用即可
        logger.info("全局 MCP 客户端初始化完成")
    
    return _mcp_client


async def get_mcp_client_with_retry(
    servers: Optional[Dict[str, Dict[str, str]]] = None,
    tool_interceptors: Optional[List] = None,
    force_new: bool = False
) -> MultiServerMCPClient:
    """
    获取或初始化带重试功能的 MCP 客户端
    
    这是一个单例模式实现，确保整个应用只有一个 MCP 客户端实例（除非 force_new=True）。
    与 get_mcp_client() 的区别是会自动添加重试拦截器，使工具调用更加稳定。
    
    重试拦截器会自动添加到拦截器列表的开头，确保在其他拦截器之前执行。
    
    Args:
        servers: MCP 服务器配置字典
                 格式: {server_name: {"transport": "...", "url": "..."}}
                 如果为 None，则使用 DEFAULT_MCP_SERVERS
        tool_interceptors: 自定义工具拦截器列表
                          这些拦截器会在重试拦截器之后添加
                          执行顺序：retry_interceptor → 自定义拦截器1 → 自定义拦截器2 → ...
        force_new: 是否强制创建新实例（用于特殊场景，如需要不同配置）
                  - True: 每次都创建新实例（不缓存）
                  - False: 使用单例模式（默认行为）
    
    Returns:
        MultiServerMCPClient: 带重试功能的 MCP 客户端实例
        
    Example:
        >>> # 获取带重试功能的客户端
        >>> client = await get_mcp_client_with_retry()
        >>> tools = await client.get_tools()
        >>> # 工具调用失败时会自动重试最多 3 次
        
    Note:
        - 推荐使用此函数而非 get_mcp_client()，以提高工具调用的可靠性
        - 重试策略：最多 3 次，初始延迟 1 秒，指数退避
    """
    # 构建拦截器列表：重试拦截器在最前面
    interceptors = [retry_interceptor]
    if tool_interceptors:
        interceptors.extend(tool_interceptors)
    
    return await get_mcp_client(
        servers=servers,
        tool_interceptors=interceptors,
        force_new=force_new
    )


def _create_mcp_client(
    servers: Dict[str, Dict[str, str]],
    tool_interceptors: Optional[List] = None
) -> MultiServerMCPClient:
    """
    创建 MCP 客户端实例（内部辅助函数）
    
    封装 MultiServerMCPClient 的创建逻辑，处理参数传递和类型检查。
    此函数仅创建客户端实例，不进行连接或初始化操作。
    
    Args:
        servers: MCP 服务器配置字典
                 格式: {server_name: {"transport": "...", "url": "..."}}
                 示例:
                 {
                     "monitor_server": {
                         "transport": "streamable-http",
                         "url": "http://localhost:8001/mcp"
                     }
                 }
        tool_interceptors: 工具拦截器列表（可选）
                          拦截器可以是函数或类，需要符合 MCP 拦截器规范
    
    Returns:
        MultiServerMCPClient: 未初始化的客户端实例
                             调用 get_tools() 时才会真正建立连接
        
    Note:
        - MultiServerMCPClient 的第一个参数直接接收 servers 配置字典
        - transport 类型支持：
          * "streamable-http": 本地 FastMCP 服务（推荐）
          * "sse": Server-Sent Events，用于腾讯云等托管端点
        - type: ignore[arg-type] 用于抑制类型检查器的警告（langchain-mcp-adapters 的类型定义不完整）
    """
    # MultiServerMCPClient 的第一个参数直接接收 servers 配置字典
    # 格式: {server_name: {"transport": "...", "url": "..."}}
    kwargs: Dict[str, Any] = {}
    
    if tool_interceptors:
        kwargs["tool_interceptors"] = tool_interceptors
    
    # 第一个参数是 servers 配置，直接传递
    return MultiServerMCPClient(servers, **kwargs)  # type: ignore[arg-type]


def suggest_mcp_transport(url: str, transport: str) -> str | None:
    """
    检测 URL 与 transport 类型的匹配性，并在明显不匹配时给出建议
    
    此函数不会自动修改配置，仅返回建议信息供开发者参考。
    常用于配置验证和启动时的健康检查。
    
    常见场景：
    1. URL 包含 "/sse/" 但 transport 是 "streamable-http"
       → 建议使用 transport="sse"（腾讯云等托管端点）
    2. URL 是本地 FastMCP 路径（如 "/mcp"）但 transport 是 "sse"
       → 建议使用 transport="streamable-http"（本地服务）
    
    Args:
        url: MCP 服务器的 URL 地址
             示例: "http://localhost:8001/mcp" 或 "https://cls-api.tencentcloud.com/sse"
        transport: 传输协议类型
                   - "streamable-http": 本地 FastMCP 服务（推荐）
                   - "sse": Server-Sent Events，用于托管端点
    
    Returns:
        str | None: 
            - 如果不匹配：返回建议字符串，说明问题和推荐的 transport 类型
            - 如果匹配：返回 None
            
    Example:
        >>> suggest_mcp_transport("https://api.example.com/sse", "streamable-http")
        'MCP URL 含 /sse/ 但 transport=\'streamable-http\'，腾讯云等托管端点应使用 transport=sse'
        
        >>> suggest_mcp_transport("http://localhost:8001/mcp", "streamable-http")
        None  # 匹配，无需建议
    """
    lower_url = url.lower()
    
    # 场景 1: URL 包含 /sse/ 但使用了 streamable-http
    if "/sse" in lower_url and transport.replace("_", "-") in (
        "streamable-http",
        "http",
    ):
        return (
            f"MCP URL 含 /sse/ 但 transport={transport!r}，"
            "腾讯云等托管端点应使用 transport=sse"
        )
    
    # 场景 2: URL 是本地 FastMCP 路径但使用了 sse
    if transport == "sse" and "/mcp" in lower_url and "/sse" not in lower_url:
        return (
            f"MCP URL 为本地 FastMCP 路径但 transport={transport!r}，"
            "本地服务通常应使用 transport=streamable-http"
        )
    
    return None
