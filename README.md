# IOAM Harness — 智能运维 Harness Agent

> 企业级智能运维 Agent 约束平台。用 LLM + 向量知识库 + MCP 工具协议，让 AI 能自主排查生产故障——并且可控、可验证、可积累。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## ✨ 核心特性

- 🤖 **智能对话** — LangGraph Agent + 流式 SSE 输出，支持多轮上下文记忆
- 📚 **RAG 问答** — Milvus 向量检索增强，支持文档上传 + 自动索引 + 增量更新
- 🔧 **AIOps 诊断** — Plan-Execute-Replan 自动故障诊断和根因分析
- 🛡️ **四层防线** — 幻觉检测 + 证据链评分 + SOP 合规 + 置信度评估，回答质量可控
- ⚡ **缓存限流** — Redis 查询语义缓存 + Embedding 向量缓存 + ASGI 速率限制
- 🔐 **用户认证** — JWT + bcrypt 注册登录，前端 auth 守卫，API 强制认证
- 🌐 **Web 界面** — 现代化 UI，流式对话渲染，服务端历史记录
- 🔌 **MCP 集成** — 日志查询和监控数据工具接入

## 🛠️ 技术栈

- **框架**: FastAPI + LangChain + LangGraph + LangGraph Checkpoint
- **LLM**: 阿里云 DashScope (通义千问 Qwen) + text-embedding-v4
- **向量库**: Milvus 2.x (MilvusVectorStore)
- **存储**: MySQL 8 (消息持久化) + SQLite (检查点) + Redis (缓存/限流)
- **工具协议**: MCP (Model Context Protocol)
- **前端**: 原生 HTML/JS/CSS (无框架依赖)

## 🚀 快速开始

### 环境要求
- Python 3.10+
- 阿里云 DashScope API Key ([获取地址](https://dashscope.aliyun.com/))

### 安装和启动

#### Linux/macOS 环境

```bash
# 1. 克隆项目
git clone <repository_url>
cd super_biz_agent_py

# 2. 安装依赖（推荐使用 uv）
# 方式 1: 使用 uv（推荐，更快）
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e .

# 方式 2: 使用 pip
pip install -e .

# 3. 编辑配置文件
# 首次使用需要编辑 .env 文件，填入你的 DASHSCOPE_API_KEY
vim .env  # 或使用其他编辑器

# 4. 一键初始化（启动 Docker + 服务 + 上传文档）
make init

# 5. 一键启动
make start
```

#### Windows 环境（PowerShell/CMD）

如果Windows 不支持 `make` 命令，可以手动执行以下步骤以启动服务：

```powershell
# 1. 克隆项目
git clone <repository_url>
cd super_biz_agent_py

# 2. 创建虚拟环境并安装依赖
# 方式 1: 使用 uv（推荐，更快）
pip install uv
# 创建虚拟环境
uv venv
# 激活虚拟环境
.venv\Scripts\activate
# 安装所有依赖
uv pip install -e .

# 方式 2: 使用 pip
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 3. 编辑配置文件
# 使用记事本或其他编辑器打开 .env 文件，填入你的 DASHSCOPE_API_KEY
notepad .env

# 4. 启动 Docker Desktop
# 确保 Docker Desktop 已安装并正在运行

# 5. 启动 Milvus 向量数据库（Docker Compose）
docker compose -f vector-database.yml up -d

# 6. 等待 Milvus 启动完成（约 5-10 秒）
timeout /t 10

# 7. 启动 MCP 服务
# 启动 CLS 日志查询服务（新开一个 PowerShell 窗口）
python mcp_servers/cls_server.py

# 启动 Monitor 监控服务（新开一个 PowerShell 窗口）
python mcp_servers/monitor_server.py

# 8. 启动 FastAPI 主服务（新开一个 PowerShell 窗口）
# 注意：日志会自动输出到 logs\app_YYYY-MM-DD.log
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900

# 9. 上传文档到向量库（新开一个 PowerShell 窗口）
# 等待服务启动完成后执行
timeout /t 5
python -c "import requests, os, time; [requests.post('http://localhost:9900/api/upload', files={'file': open(f'aiops-docs/{f}', 'rb')}) or time.sleep(1) for f in os.listdir('aiops-docs') if f.endswith('.md')]"
```

**Windows 一键启动脚本**（推荐）

使用启动脚本：

```powershell
# 启动所有服务
.\start-windows.bat

# 停止所有服务
.\stop-windows.bat
```

### 访问服务
- **Web 界面**: http://localhost:9900
- **API 文档**: http://localhost:9900/docs

## 📡 API 接口

### 核心接口

| 功能 | 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|------|
| 普通对话 | POST | `/api/chat` | 需要 | 一次性返回完整答案 |
| 流式对话 | POST | `/api/chat_stream` | 需要 | SSE 流式输出 |
| 对话历史 | GET | `/api/chat/history` | 需要 | 服务端历史列表 |
| 会话详情 | GET | `/api/chat/session/{sid}` | 需要 | 单会话消息历史 |
| 清空会话 | POST | `/api/chat/clear` | 需要 | 重置会话上下文 |
| 注册 | POST | `/api/auth/register` | 无需 | 创建账号 |
| 登录 | POST | `/api/auth/login` | 无需 | 获取 JWT |
| AIOps 诊断 | POST | `/api/aiops` | 可选 | 自动故障诊断（流式） |
| 文件上传 | POST | `/api/upload` | 可选 | 上传并索引文档 |
| 健康检查 | GET | `/api/health` | 无需 | 服务状态检查 |

### 使用示例

```bash
# 普通对话
curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"你好"}'

# 流式对话
curl -X POST "http://localhost:9900/api/chat_stream" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"你好"}' \
  --no-buffer

# AIOps 诊断
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-123"}' \
  --no-buffer
```

## 📁 项目结构

```
IOAM-Harness/
├── app/                                    # 应用核心
│   ├── __init__.py                         # 包初始化
│   ├── main.py                             # FastAPI 应用入口 (CORS→RateLimit→路由→静态文件)
│   ├── config.py                           # 配置管理 (Pydantic Settings, .env)
│   ├── api/                                # API 路由层
│   │   ├── auth.py                         #   注册/登录/刷新/登出
│   │   ├── chat.py                         #   对话 + 流式 + 历史 + 清空
│   │   ├── aiops.py                        #   AIOps 诊断 SSE
│   │   ├── file.py                         #   文件上传
│   │   └── health.py                       #   健康检查
│   ├── services/                           # 业务服务层
│   │   ├── rag/                            #   RAG Agent (按职责拆分)
│   │   │   ├── agent.py                    #     核心生命周期
│   │   │   ├── detector.py                 #     代码/诊断检测
│   │   │   ├── query.py                    #     非流式查询
│   │   │   └── stream.py                   #     流式查询
│   │   ├── vector/                         #   向量服务 (5 模块)
│   │   │   ├── embedding.py                #     Embedding + 缓存
│   │   │   ├── store.py                    #     向量存储
│   │   │   ├── search.py                   #     相似搜索
│   │   │   ├── index.py                    #     文件索引
│   │   │   └── splitter.py                 #     文档分割
│   │   ├── aiops_service.py                #   AIOps Plan-Execute-Replan
│   │   ├── context_manager.py              #   Token 感知裁剪
│   │   ├── prompt_builder.py               #   动态提示词组装
│   │   └── message_store.py                #   MySQL 消息持久化
│   ├── harness/                            # 质量保障层 (四层防线)
│   │   ├── hallucination.py                #   Layer 1: 幻觉检测
│   │   ├── evidence.py                     #   Layer 3: 证据链评分
│   │   ├── sop.py                          #   Layer 3: SOP 合规
│   │   ├── confidence.py                   #   Layer 3: 置信度评估
│   │   ├── feedback.py                     #   编排器 + 代码验证反馈循环
│   │   ├── validator.py                    #   ruff/mypy/pytest 门禁
│   │   └── memory.py                       #   MEMORY.md 经验写入
│   ├── middleware/                          # ASGI 中间件
│   │   └── rate_limit.py                   #   速率限制 (Redis 滑动窗口)
│   ├── agent/                              # Agent 决策层
│   │   ├── mcp_client.py                   #   MCP 多服务器客户端
│   │   └── aiops/                          #   AIOps 三节点
│   │       ├── planner.py                  #     计划制定
│   │       ├── executor.py                 #     步骤执行
│   │       └── replanner.py                #     重规划
│   ├── tools/                              # LangChain 工具集
│   │   ├── knowledge_tool.py               #   知识库检索
│   │   ├── query_metrics_alerts.py         #   Prometheus 告警
│   │   └── time_tool.py                    #   时间工具
│   ├── models/                             # 数据模型
│   │   ├── db.py                           #   SQLAlchemy ORM
│   │   ├── user.py                         #   用户模型
│   │   ├── request.py                      #   请求 Pydantic
│   │   └── response.py                     #   响应 Pydantic
│   ├── core/                               # 基础设施
│   │   ├── auth.py                         #   JWT + bcrypt
│   │   ├── db.py                           #   MySQL 异步连接
│   │   ├── redis.py                        #   Redis 客户端
│   │   ├── llm_factory.py                  #   LLM 工厂
│   │   └── milvus_client.py                #   Milvus 连接管理
│   └── utils/                              # 工具函数
│       └── logger.py                       #   Loguru 日志
├── static/                                 # Web 前端
│   ├── index.html                          #   主界面
│   ├── login.html                          #   登录/注册
│   ├── app.js                              #   前端核心
│   └── styles.css                          #   样式表
├── aiops-docs/                             # 运维知识库 (Markdown SOP)
├── docs/                                   # 项目文档
│   ├── project-overview.md                 #   系统全景概览
│   ├── architecture.md                     #   架构文档
│   └── phase1~5-implementation-record.md   #   各阶段实施记录
├── tests/                                  # 测试
├── Makefile                                # 项目管理
├── pyproject.toml                          # 项目配置
├── infrastructure.yml                      # Docker: MySQL + Redis
└── vector-database.yml                     # Docker: Milvus + etcd + MinIO
```

## ⚙️ 配置说明

通过 `.env` 文件配置：

```bash
# 阿里云LLM DashScope 配置（必填）
# 秘钥管理： https://bailian.console.aliyun.com/cn-beijing/?spm=5176.29597918.J_SEsSjsNv72yRuRFS2VknO.2.61ac133ccTVQLw&tab=demohouse#/api-key
DASHSCOPE_API_KEY=your-api-key （配置你自己的秘钥）
DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1  # 不配置则默认会使用新加坡站点
DASHSCOPE_MODEL=qwen-max

# Milvus 配置
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RAG 配置
RAG_TOP_K=3
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100
```

## 🎯 AIOps 智能运维

基于 **Plan-Execute-Replan** 模式实现自动故障诊断。

### 核心特性
- ✅ 自动制定诊断计划（Planner）
- ✅ 智能工具调用（Executor）
- ✅ 动态调整步骤（Replanner）
- ✅ 流式输出诊断过程
- ✅ 生成结构化报告

### 快速测试

```bash
# 服务已通过 make init 自动启动
# 如需重启服务：make restart

# 访问 Web 界面，点击"智能运维与诊断工具"
# 或使用 API
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test"}' \
  --no-buffer
```

### 诊断流程
```
1. Planner 制定计划 → 生成 4-6 个诊断步骤
2. Executor 执行步骤 → 调用 MCP 工具（日志查询、监控数据）
3. Replanner 评估结果 → 决定继续/调整/生成报告
4. 输出诊断报告 → 根因分析 + 运维建议
```

## 📝 开发指南

### 常用命令

```bash
# 项目管理
make init              # 一键初始化（Docker + 服务 + 文档）
make start             # 启动所有服务
make stop              # 停止所有服务
make restart           # 重启所有服务

# 依赖管理
make install-dev       # 安装开发依赖
make sync              # 同步依赖

# Docker 管理
make up                # 启动 Docker 容器
make down              # 停止 Docker 容器
make status            # 查看容器状态

# 代码质量
make format            # 格式化代码
make lint              # 代码检查 (ruff)
make fix               # 自动修复问题
make type-check        # 类型检查 (mypy)
make test              # 运行测试 (pytest + coverage)
make check-all         # 运行所有检查 (format + lint + test)
make security          # 安全检查 (bandit)

# 验证命令对齐 (Phase 6)
# make lint             → ruff check app/
# make test             → pytest tests/ -v --cov=app
# make check-all        → format + lint + test 全流程
```


## 🐛 常见问题

### Windows 环境问题

#### 1. `make` 命令不可用
Windows 不支持 `make` 命令，请使用提供的批处理脚本：
```powershell
# 启动服务
.\start-windows.bat

# 停止服务
.\stop-windows.bat
```

#### 2. PowerShell 执行策略限制
如果遇到 "无法加载文件，因为在此系统上禁止运行脚本" 错误：
```powershell
# 临时允许脚本执行（管理员权限）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# 或者使用 CMD 而不是 PowerShell
cmd
.\start-windows.bat
```

#### 3. 端口被占用（Windows）
```powershell
# 查看占用端口的进程
netstat -ano | findstr :9900

# 结束进程（替换 PID 为实际进程 ID）
taskkill /F /PID <PID>
```

### 通用问题

### API Key 错误
```bash
# 检查环境变量
cat .env | grep DASHSCOPE_API_KEY    # Linux/macOS
type .env | findstr DASHSCOPE_API_KEY  # Windows
```

### Milvus 连接失败
```bash
# 确保本机有 Docker 服务并且已经启动（可以使用 Docker Desktop）

# 检查 Milvus 状态
docker ps | grep milvus

# 重启 Milvus（使用 docker compose）
docker compose -f vector-database.yml restart

# 或者重启单个服务
docker compose -f vector-database.yml restart standalone
```

### 服务无法启动

**Linux/macOS:**
```bash
# 查看服务日志
tail -f logs/app_$(date +%Y-%m-%d).log  # FastAPI 主服务（Loguru 日志）
tail -f mcp_cls.log                      # CLS MCP 服务
tail -f mcp_monitor.log                  # Monitor MCP 服务

# 检查端口占用
lsof -i :9900  # FastAPI
lsof -i :8003  # CLS MCP
lsof -i :8004  # Monitor MCP
```

**Windows:**
```powershell
# 查看服务日志（获取今天的日期）
$today = Get-Date -Format "yyyy-MM-dd"
type logs\app_$today.log  # FastAPI 主服务（Loguru 日志）
type mcp_cls.log          # CLS MCP 服务
type mcp_monitor.log      # Monitor MCP 服务

# 或者查看最新的日志文件
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 50

# 检查端口占用
netstat -ano | findstr :9900  # FastAPI
netstat -ano | findstr :8003  # CLS MCP
netstat -ano | findstr :8004  # Monitor MCP
```

## 📚 参考资源

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [LangChain 文档](https://python.langchain.com/)
- [LangGraph Plan-Execute](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/)
- [阿里云 DashScope](https://dashscope.aliyun.com/)
- [MCP 协议](https://modelcontextprotocol.io/)

## 📄 许可证
author： chief

MIT License
