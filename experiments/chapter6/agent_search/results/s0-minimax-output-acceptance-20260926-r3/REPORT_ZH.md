# S0 r3 MiniMax M3 输出验收报告

r3 是在 r2 planner 独立验收 14/18 后单独冻结的固定容量工程批次。它不重新选择模型、不使用 TSP 实例、不比较搜索策略。

- 源码提交：`069b649f6cc9d61070d7799a848e28f30f2b76cb`
- 冻结提交：`d57c687`
- manifest SHA-256：`d592e9b76d8d9c0da4bf83a3012c191f027d579908bddd2f58fbdb9db1dd6d2a`
- MiniMax 配置：`minimax-cn-coding-plan/MiniMax-M3`，planner 16,384、coder 8,192、temperature 0.7、timeout 180 秒。

48 个请求均成功落盘，返回模型均为 `MiniMax-M3`，usage 完整，总 tokens 170,724。独立验收 planner 18/18，coder 18/18 且均通过受限解释器；6 个端到端上下文 6/6 完成 planner→coder 传递。所有 finish reason 为 `stop`。Wilson 95% 区间为独立验收 planner/coder [82.41%,100%]，端到端成对 [60.97%,100%]；这些小样本区间只用于描述，不是稳定率保证。

预设门槛全部达到，因此可以进入 S1。它只说明输出接口在这组固定配置下可用于后续实验；S1 仍需重新记录失败、成本、方向竞争和搜索质量。
