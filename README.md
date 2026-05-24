# LLM Wiki Agent

企业级 LLM Wiki 知识库问答系统，基于 WikiFs 虚拟文件系统实现 Agent 探索式问答。

## 项目简介

LLM Wiki Agent 将结构化的 Wiki 知识库以虚拟文件系统（WikiFs）的形式呈现给 LLM Agent。Agent 通过文件系统操作（ls、cat、grep、find、tree、head）自主探索知识库，定位相关信息并给出带引用来源的回答。

### 核心特性

- **WikiFs 虚拟文件系统**：将 Wiki 知识库映射为只读文件系统，Agent 可像浏览文件一样探索知识
- **LLM Agent + Tool Use**：基于 OpenAI Function Calling 实现 Agent 循环，自动调用工具探索知识库
- **SSE 流式问答**：支持实时流式输出，包括工具调用过程、回答内容、引用来源
- **版本包管理**：支持上传 `.wiki.tar.gz` 版本包，一键激活切换知识库版本
- **前端对话界面**：开箱即用的 Web 对话界面 + 管理后台

## 快速开始

### 1. 启动基础服务

```bash
docker-compose up -d
```

将启动 PostgreSQL、Redis、Qdrant 三个基础服务。

### 2. 安装依赖并运行数据库迁移

```bash
pip install -e ".[dev]"
alembic upgrade head
```

### 3. 启动 API 服务

```bash
uvicorn app.main:app --reload
```

### 4. 上传知识库

1. 访问 http://localhost:8000/admin.html
2. 创建知识库（如标识 `test`，名称 `测试知识库`）
3. 生成测试 wiki 包并上传：
   ```bash
   python scripts/create_test_wiki.py
   ```
4. 在管理界面上传生成的 `test_data/test-wiki.wiki.tar.gz`
5. 点击「激活」按钮激活版本

### 5. 开始问答

访问 http://localhost:8000 ，选择知识库后即可开始对话。

## API 文档

启动服务后访问：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/namespaces` | 创建知识库 |
| GET | `/api/v1/admin/namespaces` | 列出知识库 |
| POST | `/api/v1/admin/namespaces/{id}/versions` | 上传版本包 |
| GET | `/api/v1/admin/namespaces/{id}/versions` | 列出版本 |
| PUT | `/api/v1/admin/versions/{id}/activate` | 激活版本 |
| POST | `/api/v1/chat/chat` | 对话问答（SSE 流式） |
| GET | `/api/v1/chat/namespaces/{id}/active-version` | 获取当前活跃版本 |

## 架构说明

```
┌─────────────┐     ┌──────────────────────────────────────┐
│   Frontend  │────▶│            FastAPI Server             │
│  (HTML/JS)  │     │  ┌─────────┐  ┌──────────────────┐  │
└─────────────┘     │  │  Admin  │  │    Chat API      │  │
                    │  │   API   │  │  (SSE Streaming) │  │
                    │  └────┬────┘  └────────┬─────────┘  │
                    └───────┼────────────────┼─────────────┘
                            │                │
                    ┌───────▼───────┐  ┌─────▼──────┐
                    │ VersionService│  │  WikiAgent │
                    └───────┬───────┘  └─────┬──────┘
                            │                │
                    ┌───────▼───────┐  ┌─────▼──────┐
                    │    Storage    │  │   WikiFs   │
                    │  (tar.gz)     │  │ (Virtual   │
                    └──────────────┘  │  Filesys)  │
                                      └─────┬──────┘
                                            │
                              ┌──────────────┼──────────────┐
                              │              │              │
                       ┌──────▼──┐   ┌───────▼──┐   ┌──────▼──┐
                       │  Redis  │   │  Qdrant  │   │  PG     │
                       │Path Tree│   │ Chunks   │   │Metadata │
                       └─────────┘   └──────────┘   └─────────┘
```

### 数据存储

| 服务 | 用途 | 数据内容 |
|------|------|----------|
| **PostgreSQL** | 元数据管理 | 知识库命名空间、版本信息、对话历史 |
| **Redis** | 路径树存储 | Wiki 目录结构的压缩 JSON，支持 ls/find/tree 操作 |
| **Qdrant** | 向量检索 | Wiki 页面 chunk 及其 embedding，支持 cat/grep/head 操作 |
| **本地文件** | 版本包存储 | 原始 .wiki.tar.gz 文件 |

## 项目结构

```
llmWikiConsumer/
├── app/
│   ├── main.py           # FastAPI 入口 + 前端静态文件挂载
│   ├── config.py         # Pydantic Settings 配置
│   ├── api/v1/
│   │   ├── admin.py      # 管理员 API（知识库 + 版本管理）
│   │   └── chat.py       # 对话 API（SSE 流式问答）
│   ├── core/
│   │   └── wikifs.py     # WikiFs 虚拟文件系统核心
│   ├── db/
│   │   ├── postgres.py   # PostgreSQL 连接
│   │   ├── redis.py      # Redis 连接 + 路径树缓存
│   │   ├── vector.py     # Qdrant 向量存储
│   │   └── storage.py    # 本地文件存储
│   ├── models/
│   │   ├── db_models.py  # SQLAlchemy 数据模型
│   │   └── schemas.py    # Pydantic 请求/响应模型
│   └── services/
│       ├── agent.py      # LLM Agent（Tool Use 循环）
│       ├── llm_client.py # LLM API 客户端
│       └── version.py    # 版本包处理服务
├── frontend/
│   ├── index.html        # 对话界面
│   ├── admin.html        # 管理界面
│   ├── style.css         # 样式
│   └── app.js            # 交互逻辑
├── alembic/              # 数据库迁移
├── tests/                # 测试
├── scripts/              # 工具脚本
├── docker-compose.yml    # 基础服务编排
└── pyproject.toml        # 项目配置
```

## 开发指南

### 环境配置

复制 `.env.example` 为 `.env` 并填入配置：

```bash
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY 等配置
```

### 运行测试

```bash
pytest tests/ -v
```

### 代码检查

```bash
ruff check app/ tests/
```

### 数据库迁移

```bash
# 生成迁移
alembic revision --autogenerate -m "description"

# 执行迁移
alembic upgrade head
```

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy / Alembic
- **LLM**: OpenAI API (Function Calling)
- **数据库**: PostgreSQL / Redis / Qdrant
- **前端**: Vanilla HTML + JS + CSS / marked.js (Markdown 渲染)
- **部署**: Docker Compose / Uvicorn

## License

MIT
