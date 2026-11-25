# AI 内容运营 Agent 平台

一个用于内容运营场景的 AI 助手，包含流式聊天、后端会话历史、滑动窗口和调用追踪。

## 环境要求

- Python 3.11 和 [uv](https://docs.astral.sh/uv/)
- Node.js 和 npm
- DeepSeek API Key

## 配置

在项目根目录运行以下命令，创建本地环境配置文件：

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```

在根目录 `.env` 中填写 `DEEPSEEK_API_KEY`。默认后端地址为 `http://127.0.0.1:10110`，前端地址为 `http://localhost:10111`。如修改后端端口，请同步修改 `frontend/.env.local` 中的 `NEXT_PUBLIC_API_BASE_URL`。

## 安装依赖

在项目根目录打开终端：

```powershell
cd backend
uv sync
cd ../frontend
npm install
```

## 启动

在项目根目录打开两个终端窗口。

终端一启动后端：

```powershell
cd backend
uv run --env-file ../.env python -m app
```

终端二启动前端：

```powershell
cd frontend
npm run dev
```

启动后访问 <http://localhost:10111>。可通过 <http://127.0.0.1:10110/health> 检查后端是否正常运行。

## 常用配置

- `DEEPSEEK_API_KEY`：DeepSeek API 密钥，必填。
- `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`：模型服务地址和模型名称。
- `BACKEND_PORT`：后端端口，默认 `10110`。
- `FRONTEND_ORIGINS`：允许访问后端的前端来源。
- `RECENT_TURNS`：保留为原文的最近已完成轮数，默认 `4`；当前输入另计。
- `SESSION_BACKEND`：会话存储后端，`memory` 或 `redis`，默认 `memory`。设为 `redis` 可在重启后保留会话。
- `REDIS_URL`：会话 Redis 地址，默认 `redis://127.0.0.1:6379/0`；使用 Redis 后端前需启动 Redis。
- `SESSION_TTL_SECONDS`：会话滑动过期时间，默认 `86400` 秒（24 小时）；每次读取或追加后续期。

会话摘要和近期原文由所选后端一起保存；Redis 后端不可用时 `/chat` 返回 503。
