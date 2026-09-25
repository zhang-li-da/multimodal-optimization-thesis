# readiness-20260925

这是 v1.2.2 启动准备阶段的离线结果包。它只包含工程 fixture、历史归档回放、身份兼容核查和未执行的数据草案。

本包没有真实模型调用，没有 16 次在线搜索，也没有性能优越性结论。fixture 的请求对象标记为 `fixture-no-api`；它们不是 Qwen、MiniMax 或其他 provider 的 API 响应。

目录说明：

- `niche_fixed_dev/`：固定 FIFO 开发臂的 8 步 fixture checkpoint、请求、响应和 readout。
- `relational_branch/`：关系/收益组合排序开发臂的 8 步 fixture checkpoint、请求、响应和 readout。
- `tests/`：fixture test 阶段的独立评价输出。
- `evidence/r2-replay/`：18 次历史 r2 运行、144 次决策和 330 项评价的离线审计及数值回放。
- `evidence/identity/`：Python 3.12/3.13 的 327 条历史程序身份核查。
- `evidence/draft-study/`：区块 3--6 的 16 项搜索任务草案及 432 个新实例快照。
- `REPORT_ZH.md`：中文技术报告和证据边界。
- `tests.xml`：本轮 pytest JUnit 输出。
- `SHA256SUMS.txt`：发布文件校验清单。

从仓库根目录验证结果包：

```powershell
python -m chapter6_demo.v12_2.archives experiments/chapter6/v12_2/results/readiness-20260925
```

真实搜索前必须另行冻结最终协议、provider/model 版本、收益阈值和源码提交。草案 manifest 的状态仍为 `DRAFT_NOT_EXECUTABLE`。
