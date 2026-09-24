# v1.2 r2 历史机制筛查与勘误

本目录保存18次r2模型搜索；r1因niche误建B池作废并单独归档。本次v1.2.1修订没有新增模型调用。原始源码、manifest、ZIP和质量统计保留，报告的实现描述由版本提交勘误。

均值test gap为niche 5.759%、固定开发5.569%、关系开发6.379%。30次续开发中6次改进父代，说明执行链运行过。r2固定臂普通步骤调用niche、关系臂调用relational且继承W引用，因此不能独立解释B内排序效果。固定臂实际是可用集合索引轮转，并非FIFO；旧深度字段最大4混入尝试，成功深度应为3。原合并门槛通过不等于每单元充分检验。

阅读[历史结果及勘误](results/screening-20260924-r2/TECHNICAL_REPORT_ZH.md)、[当前架构](../../../docs/chapter6/V12_ARCHITECTURE.md)、[v1.2.1离线工具](../v12_1/README.md)和[版本记录](VERSION_HISTORY.md)。原[预注册文件](preregistration.md)是不可追溯修改的历史文档；与实际实现不符处以勘误说明。

离线审计直接读取已归档ZIP：

```powershell
python -m chapter6_demo.v12_1.audit_r2 --output audit-local
python -m chapter6_demo.v12_1.verify_numeric --instances audit-local/r2_instances.json --output audit-local/numeric-replay.json
```

旧严格回放请按[结果包说明](results/screening-20260924-r2/README.md)先解压到新的工作目录，使用`python -m chapter6_demo.v12.verify_v12`。禁止将缺少runs的发布目录作为回放写入目标，以免覆盖原verification记录。

在线runner只作为历史源码保留，本轮不运行它。计划中的16次搜索使用新协议与全新区块，不能追加到r2。
