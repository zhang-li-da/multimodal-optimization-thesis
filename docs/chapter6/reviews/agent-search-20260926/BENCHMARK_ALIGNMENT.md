# MLEvolve 与 MLE-Bench：实验对齐核查

查阅日期：2026-09-26。范围：论文实验/附录、官方仓库文件树和下列实现文件。未运行 MLEvolve，未下载比赛数据，未复现作者成绩，也未执行新数学任务。未来执行前再次核对版本，但不得悄悄移动已冻结的来源。

## 1. 固定来源

| 来源 | 本次固定版本 | 用途 |
|---|---|---|
| MLEvolve 论文 | [arXiv:2606.06473v1](https://arxiv.org/html/2606.06473v1) | §4、附录 C/D 的实验设置 |
| MLEvolve 源码 | [9c5c8a3b23f0361708b59a401452dddc00f97189](https://github.com/InternScience/MLEvolve/tree/9c5c8a3b23f0361708b59a401452dddc00f97189) | 主配置、选择器、脚本和格式服务器 |
| MLE-Bench | [507f92e1138bb6e40dac5c6ee7a6758e6424bf97](https://github.com/openai/mle-bench/tree/507f92e1138bb6e40dac5c6ee7a6758e6424bf97) | 官方数据准备、评分、分组、dev 和已知问题 |
| 本项目 | [0da63acf486df508766b3bf33d7d0258644cd04f](https://github.com/zhang-li-da/multimodal-optimization-thesis/tree/0da63acf486df508766b3bf33d7d0258644cd04f) | v1.2.3 原型边界与失败证据 |

论文是预印本。本次核查用于准确建立近邻实验对照，不是全面文献新颖性检索，也不宣称新增反馈机制已区别于所有已有 bandit/调度方法。

## 2. 论文设置与当前代码不能混为一谈

论文报告 75 个 MLE-Bench 任务、3 个种子、12 小时/任务、最多 500 次扩展；骨干 Gemini-3.1-Pro-preview，temperature=1，21 vCPU、234GB RAM、单 H200；数学部分列出 15 个任务。[论文 §4.1、表1/2、附录D](https://arxiv.org/html/2606.06473v1)

当前 [config/config.yaml](https://github.com/InternScience/MLEvolve/blob/9c5c8a3b23f0361708b59a401452dddc00f97189/config/config.yaml) 的模型默认字段为 `gemini-3-pro-preview`，并非论文中的 3.1。时间为 43200 秒、steps=500；初始方向与并行设置应作为完整配置保存。不能仅运行 main 默认配置便称复现论文同款实验。

官方基准 [README](https://github.com/openai/mle-bench/blob/507f92e1138bb6e40dac5c6ee7a6758e6424bf97/README.md) 的标准资源建议与 MLEvolve 论文不同。今后的主对照采用哪套硬件、是否为 MiniMax 适配、是否复现论文设置，必须分别命名；不把异模型/异硬件排行榜数字作为同预算配对结果。

## 3. 三个直接影响新设计的源码事实

### 3.1 时间调度已是强近邻

已读 [engine/node_selection.py](https://github.com/InternScience/MLEvolve/blob/9c5c8a3b23f0361708b59a401452dddc00f97189/engine/node_selection.py)：选择器按已消耗时间调整 UCT/全局 top-K 的选择权重，top-K 有每分支上限。新设计不能只对比贪心，然后把“随时间转向开发”当创新。

因此需要 FB（固定）、TS（预定时间变化）、AD（实际进展反馈）三个可分离对照，完整 MLEvolve 另作端到端基线。若把 12h 原配置缩到 2h，固定小时数的触发条件可能不再触发；短 pilot 只验收接入。要研究短预算机制，需要统一且显式的归一化适配，再单列为适配实验。

### 3.2 默认脚本有搜索后的集成

已读 [run_single_task.sh](https://github.com/InternScience/MLEvolve/blob/9c5c8a3b23f0361708b59a401452dddc00f97189/run_single_task.sh)：限时 `run.py` 之后还调用 `utils/submission_fusion_utils.py`。本次仅核查调用顺序，未审计所有集成实现或作者历史运行的具体计时。

本研究两种读出必须分开：单一 validation 选中 pipeline 用于搜索机制比较；默认系统后处理用于端到端适配比较。后者要另记输入候选、选择依据、耗时与 GPU/LLM 成本，纳入统一总预算或明确单列。不能把搜索后免费处理算成搜索机制的优势。

### 3.3 格式验证不等于可见测试成绩

已读 [engine/validation/format_server.py](https://github.com/InternScience/MLEvolve/blob/9c5c8a3b23f0361708b59a401452dddc00f97189/engine/validation/format_server.py)：`/validate` 调用 `validate_submission` 返回合法性与消息。不能将脚本中的 grading-server 名称解读为允许搜索时查询隐藏测试得分。

拟接入系统保留可见训练/validation 与格式反馈；官方私有评价在输出冻结后独立执行。访问隔离、权限与路径在容器部署中实际检查，不能只看函数参数声称绝对隔离。

## 4. 端到端接入操作顺序

以下是已经从官方材料核对过的入口示例，不是本项目已经实现的新启动器。本次不执行这些命令。

1. 固定上述提交，记录镜像摘要、驱动、CUDA、可用 RAM/CPU/GPU 与依赖。
2. 按官方流程准备开发任务，先检查 public 目录实际数据和文件数量；例：`mlebench prepare -c spaceship-titanic`。数据授权和缓存完整性由运行环境满足。
3. 配置 MLEvolve 的数据路径、已可用模型端点和缓存。不得把凭据写进版本库。MiniMax 适配要测试实际 API 返回和用量字段，不根据模型名推定兼容。
4. 作者任务入口为 `bash run_single_task.sh <EXP_ID> <DATASET_DIR> [SERVER_ID]`。本研究的容器包装器需另行实现，并统一跟踪搜索及后处理费用。
5. 搜索结束按预定规则冻结提交；官方离线评分示例为 `mlebench grade-sample <PATH_TO_SUBMISSION> spaceship-titanic`。grade 进程与智能体分离，不反馈其私有分数。
6. 使用官方 low/medium/high/split75 分组汇总；子集另有 manifest，不能假称完整基准。

dev 清单来自 [experiments/splits/dev.txt](https://github.com/openai/mle-bench/blob/507f92e1138bb6e40dac5c6ee7a6758e6424bf97/experiments/splits/dev.txt)。本方案选择其中三个不同形态任务作接入。官方 README 已列出的数据/评分问题须写入环境预检；修复必须对所有方法一致，标注适配版本，不能静默将修复后的成绩与原版本混合。

## 5. 数学任务对齐的真实缺口

查阅固定提交的递归文件树与 README，未发现独立的 15 任务运行器及完整评价包。论文表格和 [作者任务名称表](https://github.com/InternScience/MLEvolve/blob/9c5c8a3b23f0361708b59a401452dddc00f97189/README.md) 能确定候选类别，但不能替代问题参数与评价器。

正式启动数学对照前应交付每任务 `definition`、维度/约束、初值、评价预算、可行性容差/证书、参考结果与来源、是否曾用于开发。若原始定义/脚本不能核实，先报告自建变体的机制实验；不得称已复现同一数学任务，更不能把表格中小数改善直接当作严格证明。

## 6. 与现有工作的差异必须怎样证明

新候选机制需要在共用执行框架下，说明方向描述与有限保护如何改变可选行动，并用 FB/TS/AD、保护开关和反馈对应对照证明增量。成本包含额外描述/记忆模块。若只是复现渐进调度、图引用和记忆，应作为可靠基线或系统改进，而不是新的多模态优化原理。

一个系统比较不能同时完成机制归因。新方案的完整实验分层、预算与负结果决策见 [实验计划](EXPERIMENT_PLAN.md)。
