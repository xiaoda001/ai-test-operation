# 项目：AI 内容运营 Agent 平台

## 项目目标
把已有的视频钩子检测、链接采集、运营文档整合成一个带 RAG 知识库 + 多步工具调用的 Agent 平台，
前端流式对话 + 任务看板，可部署可监控。用途：简历作品 + 面试讲解。

## 技术栈（版本锁定）
- 后端：Python 3.11 + FastAPI + Pydantic
- 依赖与环境：**uv**（`uv add` 加依赖、`uv run` 跑命令，锁文件 uv.lock 入库；禁止裸用 pip）
- 前端：Next.js 15 + TypeScript strict + Tailwind
- LLM：DeepSeek API（OpenAI 兼容接口），通过环境变量 DEEPSEEK_API_KEY 读取
- RAG：LangChain + bge embedding + Chroma + bge-reranker
- Agent：Function Calling + LangGraph
- 存储：Redis（会话）；向量库 Chroma 本地持久化
- 部署（第 23–26 天）：Docker + K8s(kind) + GitHub Actions + Prometheus/Grafana

## 目录结构
backend/app/        FastAPI 应用、路由、schema
backend/app/chat.py 对话与多轮逻辑（后续建）
backend/app/tools/  Agent 工具（后续建）
backend/tests/       pytest 测试
frontend/            Next.js 应用
docs/                架构图、交接笔记、评测报告

## 代码规范
- Python：函数/公共方法必须有类型注解；字符串用双引号；每轮跑 mypy + ruff + pytest
- TypeScript：strict 模式，禁止 any（除非注明原因）
- 每个模块一个职责，测试与代码同目录或放 tests/
- 提交粒度：一个功能一个 commit，message 用中文说明做了什么

## 禁止事项（未经我确认不要做）
- 不要把 API key、密钥写进代码或提交到 git
- 不要擅自改动数据库/schema 或已有接口签名
- 不要一次性重构多个模块，不要跨文件大范围改动（先给计划）
- 不要引入新依赖，除非在计划里说明理由并得到确认；加依赖一律用 `uv add`，运行一律用 `uv run`，不要裸用 pip
- 不要为了让代码跑起来而吞掉异常或跳过错误处理

## 开发流程（每轮必走）
1. 我先给目标 + 约束 + 验收标准，你先返回实现计划，不要直接写代码（用 /plan）
2. 计划确认后再实现，改动保持最小 diff
3. 实现后跑 mypy + ruff + pytest，报告结果
4. 关键代码解释：做了什么、为什么这么设计、有什么取舍和风险
5. 收尾时给出建议的 commit message

## 当前进度
- [ ] Day 1：项目初始化 + 第一次 LLM 裸调