## Day 1 交接
- 已完成：项目骨架、/health、LLM 裸调、护栏配置
- 关键决策：LLM 用 DeepSeek OpenAI 兼容接口；key 走环境变量；向量库暂定 Chroma
- 下一步：Day 2 实现流式 SSE + 前端对话界面
- 待解决：（写下今天没搞定的问题，比如某个报错的根因）

## 本地端口
- 前端：`http://localhost:10111`，在 `frontend/package.json` 的 `dev` 和 `start` 脚本中配置。
- 后端：默认 `http://127.0.0.1:10110`；可在根目录 `.env` 修改 `BACKEND_PORT`，从 `backend` 目录运行 `uv run --env-file ../.env python -m app` 启动。
- 前端 API 地址：默认 `http://localhost:10110`，配置在 `frontend/.env.local` 的 `NEXT_PUBLIC_API_BASE_URL`；若修改 `BACKEND_PORT`，需同步修改此地址。
## Day 2 交接
- 已完成：后端流式 SSE、前端对话界面、前后端联调
- 关键决策：（写下今天的技术取舍，比如 SSE 帧格式、前端读流方式）
- 下一步：Day 3 复习 + 投递；Day 4 多轮对话
- 待解决：（今天没搞定的问题，比如某个报错的根因）

## Day 4 交接
- 已完成：system prompt 模板外置（backend/app/prompts/hook_analysis.txt）+ 后端强制置顶插入 + 过滤客户端 system 防注入；response_format=json_object 约束 JSON 输出；前端 messages state 维护多轮历史；可观测性埋点（app/observability.py，track_llm_call + SQLite + /traces 列表与详情接口，流式用 stream_options include_usage 拿 usage）。
- 关键决策：见 docs/decisions.md（D1 客户端维护历史的分阶段理由、D5 system 外置、D6 自研埋点）。
- 下一步：Day 5 滑动窗口截断 + session_id 多会话隔离。
- 待解决：Day 5 尚未开始。

## Day 5 交接
- 已完成：滑动窗口按轮截断（truncate_history，保留窗口内完整轮次 + 当前 user）；session_id 会话隔离（前端生成、后端 SessionStore 内存 dict）；修复 /chat 被钩子分析 prompt 锁死——通用对话用 chat_general.txt，钩子分析拆到独立模块 analyzer.py（analyze_hook，返回解析后的 dict，供 Day 18 封装成工具）
- 当前结构：ChatRequest 从"完整 messages"改为 {session_id, message}；后端按 id 取历史 → 截断 → 置顶 system → 发模型；流正常结束后 session_store.append 落库一轮
- 关键决策：见 docs/decisions.md
- 下一步：Day 6 摘要压缩 + 持久化（Redis 或内存版顶上）
- 待解决：内存 dict 重启即丢（Day 6 解决）；切会话时流不取消（已决策，AbortController 留到 Day 20 停止生成功能）

## 当前进度
- [x] Day 1：项目初始化 + 第一次 LLM 裸调
- [x] Day 2：后端流式 SSE、前端对话界面、前后端联调
- [x] Day 3：复习 + 投递 + 面试卡片（docs/interview-cards.md）
- [x] Day 4：Prompt 工程 + 多轮对话 + 可观测性埋点
- [x] Day 5：滑动窗口 + 会话隔离 + 修复 /chat 锁死（commit 9350235）
- [x] Day 6：滚动摘要 + Redis 会话持久化 + 摘要埋点

## Day 6 交接
- 已完成：SessionStore 改为 Redis 存储 `summary` 和近期原文；默认保留 4 个已完成轮次，超出的最早轮次滚动压缩；摘要以 `[历史摘要]` user 消息进入上下文。
- 关键决策：见 docs/decisions.md（D10）。摘要失败保留超量原文并继续对话；Redis 不可用时读取阶段返回 503。
- 配置：`SESSION_BACKEND` 默认 `memory`，设为 `redis` 后使用 Redis 持久化；`REDIS_URL` 默认 `redis://127.0.0.1:6379/0`；`SESSION_TTL_SECONDS` 默认 86400 秒（24 小时）；`RECENT_TURNS` 默认 4。
- 追踪：`/traces` 增加 `call_type`；旧 SQLite 表启动时自动增量迁移，聊天标记 `chat`，摘要标记 `summarize`。
- 待解决：选择 `SESSION_BACKEND=redis` 时，本地和部署环境需提供 Redis 服务；摘要生成失败时历史原文会暂时超过窗口，之后的请求会重试压缩。
