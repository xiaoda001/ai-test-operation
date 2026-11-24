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

## 当前进度
- [x] Day 1：项目初始化 + 第一次 LLM 裸调
- [x] Day 2：后端流式 SSE、前端对话界面、前后端联调
- [x] Day 3：复习 + 投递 + 面试卡片（docs/interview-cards.md）
- [x] Day 4：Prompt 工程 + 多轮对话 + 可观测性埋点
- [ ] Day 5：滑动窗口 + 会话隔离
