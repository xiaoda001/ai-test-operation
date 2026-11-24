A. LLM API 调用（Day 1）
1. 一次 LLM API 调用，messages / temperature / max_tokens 分别起什么作用？
  - messages：对话上下文，role 分 system/user/assistant；temperature：随机性（越低越确定）；max_tokens：输出长度上限。
2. 为什么 API key 不能写进代码？你怎么管理？
  - 泄露/滥用风险；用环境变量 + .env（不入库）+ .gitignore；代码里 os.getenv() 读取。
B. 流式输出原理（Day 2）——重点
1. 流式和非流式的区别？为什么要用流式？
  - 非流式等整段生成完（秒级），流式每生成一帧就推（首字几百毫秒）；体验好、首字延迟低。
2. 什么是"增量文本"（delta.content）？为什么它可能是 None？
  - 每帧只带本次新增的一小段；首帧常只有 role 没 content，控制帧也可能空，所以要跳过空值。
3. SSE 帧为什么必须以 \n\n 结尾？
  - SSE 协议规定双换行是一帧的结束标志；少了前端切不开帧，会粘在一起。
4. 前端为什么不能用 EventSource，要用 fetch 读流？
  - EventSource 只支持 GET，而 /chat 是 POST（要传 messages）；fetch 能读 response.body 字节流。
5. 什么是粘包？你怎么处理？
  - TCP 可能一次读到半帧或多帧，不能假设"一次 read = 一帧"；用 buffer 累积，按 \n\n 切，frames.pop() 把不完整段留到下次。
6. 流式接口中途报错怎么处理？（这是 Codex 计划阶段问过你的题）
  - 流开始后 HTTP 200 已发出，改不回 500；方案1 保留异常中断不发 [DONE]（当前采用），方案2 发 error 帧（Day 20 兜底阶段再做）。[DONE] 语义是"正常说完"，出错硬发是撒谎。
C. 封装设计（你这几天深挖的）
1. 如果让你封装 SSE 客户端，怎么设计？
  - 两端封装 + 中间帧格式契约；后端 stream_answer 生成器，前端 streamChat + onToken 回调；返回 {id, fullText, delta, status} 让上层零状态。
2. 按 id 缓存 + 自动组装增量有什么用？
  - id 是缓存键，多会话并发不串台；重复订阅回放缓存（解决 React 严格模式 effect 跑两次）；多组件订阅同一条流；断线可续。取舍：done 后 session 留不留（历史回放 vs 内存泄漏）。