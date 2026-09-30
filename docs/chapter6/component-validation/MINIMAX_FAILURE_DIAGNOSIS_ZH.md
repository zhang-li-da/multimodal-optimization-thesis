# MiniMax 调用失败诊断

## 结论

2026-09-27 18:35 UTC 的独立 connectivity preflight 使用 MiniMax-M3 返回 HTTP 429，脱敏业务码为 `rate_limit_error`。请求只包含 `{"ready":true}`，没有复杂提示、长输出或并发因素，因此本次失败不能归因于实验 prompt 或控制器。

目前不能从提供方返回的信息进一步区分请求频率限制、连接限制、五小时/周额度窗口或其他 Coding Plan 限制。响应没有 `Retry-After`，token 用量未知。实验调度应保持全局暂停。

## 调用方式核对

历史成功的 S3 r3 preflight 与当前失败 preflight 均使用：

- provider：`minimax-cn-coding-plan`
- model：`MiniMax-M3`
- endpoint：`https://api.minimaxi.com/v1/chat/completions`
- protocol：OpenAI-compatible
- headers：`Content-Type: application/json` 与 `Authorization: Bearer ...`
- body：`model`、`max_tokens`、`temperature`、system/user 两条 message

本地对旧 `HTTPTransport` 与当前 `DiagnosticHTTPTransport` 做了请求拦截比较，URL、非敏感 headers 和 JSON body 一致。新 transport 只增加请求间隔、脱敏错误码和暂停信号，不改变请求语义。

## 时间线证据

- 同一配置的历史 preflight 曾成功返回 `MiniMax-M3`，包括 S3 r3 和 component r2。
- component 首次 preflight 在 14:32 UTC 失败，14:40 UTC 的 r1 成功，14:51 UTC 的 r2 成功。
- 18:35 UTC 的独立短请求再次返回 429。
- 更早的完整实验中，MiniMax 既有成功调用，也有跨任务、跨阶段的 429；降低并发后仍出现过失败。

这说明故障具有时间窗口或账户共享状态特征。模型别名、端点和请求格式不是目前最有力的根因解释。

## 后续门槛

恢复实验前必须重新完成：

1. 单请求 connectivity preflight；
2. 3 个真实 planner 和 3 个真实 coder 工作负载验收；
3. 保存脱敏 HTTP 状态、业务码、trace id、Retry-After 和已知/未知 token 状态；
4. 验收通过后才允许启动 E1，E1 完成后才允许启动修订后的 E2。

任何 429 都应停止派发并保留未启动任务，不能通过重复重试把基础设施失败混入方法结果。

证据归档：`experiments/chapter6/agent_search/component_validation/results/provider-preflight-20260928/`。
