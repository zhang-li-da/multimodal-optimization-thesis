# v1.2.1：共享普通调度的离线机制验证

本版本按 r2 复核意见修正架构，**新增模型调用为0**。它包含独立控制器模块、机制测试、旧日志审计、实例数据快照及跨平台数值比较工具。16次针对性模型搜索是后续协议草案，尚未执行；本版本没有在线启动器。

优先阅读[中文报告](results/mechanism-only-20260924/REPORT_ZH.md)、[架构](../../../docs/chapter6/V12_ARCHITECTURE.md)、[未来协议草案](preregistration.md)和[数值规范](NUMERIC_REPLAY.md)。

## 已完成修正

- 两开发组共用普通 niche 调度、准入、容量、额度、证据和提示 schema；只改变 B 内选择规则。
- 固定组严格按创建顺序 FIFO；关系组按已观测族证据与局部收益组合评分，少样本退回共同统计。
- W 不参与父代和参考选择；决策日志保存完整可选集合和评分，模型侧不暴露处理组名。
- 分开尝试深度、成功改进链深度、准入资格和实际保留；重复候选不能续期，失败子代消费机会。
- 测试通过实际 `choose()` 验证多分支选择，不依赖手工注入评分。构造失败的第4层尝试，验证成功深度仍为3。

## 不调用模型的复核

在仓库根目录运行，Python 环境需具备原项目依赖（pytest、numpy、scikit-learn、matplotlib；继承模块还使用 requests）。下列命令均不加载模型凭据。离线脚本显式拦截模型客户端与 socket 连接。

```powershell
python -m pytest experiments/chapter6/v12_1 experiments/chapter6/v12/test_v12.py experiments/chapter6/v11/test_v11.py -q
python -m chapter6_demo.v12_1.audit_r2 --output audit-local
python -m chapter6_demo.v12_1.verify_numeric --instances audit-local/r2_instances.json --output audit-local/numeric-replay.json
```

审计直接读取已发布ZIP，无需解压或调用旧的在线 runner。结果包含18个旧运行和144次控制器决策回放，以及修正版的同历史选择探测；它不估计更换父代后模型会生成什么。数值复核逐一执行198个validation程序及132个test程序，并报告每个差异和选择改变。

`results/mechanism-only-20260924/` 保存本次输出、Windows本机数值回放、测试记录、源码指纹和SHA-256清单。另一操作系统需写入新复核文件，不能覆盖本机记录或宣称未运行平台已通过。

## 版本边界

- v1.2 r2 冻结源码、原始运行ZIP、manifest、CSV/JSON质量数值和旧标签保持原样；其报告勘误更新可由Git追踪。
- `v12_1_controller.py` 不进入旧r2的源码指纹，也不被旧在线runner使用。
- 当前分支：`experiment/v1.2.1-isolated-branch-policy`。
- 当前标签：`chapter6-v1.2.1-mechanism-only-20260924`。
- 后续搜索使用全新区块3、4、5、6和单独版本，不追加到r2，不汇总为旧实验的重复样本。

本版本确认的是实现与机制可检验性。修正版关系/收益排序是否改善新搜索质量，以及多算法集合是否有实际用途，仍待新实验回答。
