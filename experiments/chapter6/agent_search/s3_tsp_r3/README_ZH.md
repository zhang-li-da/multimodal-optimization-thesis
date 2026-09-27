# S3 r3：方向试用、有限保护与探索—开发调度

本版本以单模型 MiniMax M3、TSP14 受限构造规则空间进行机制与策略筛查。它与历史 r1/r2 使用独立目录、提交和数据块，不把旧批次结果并入新结果。最终输出为 validation 选出的一个最佳程序。

## 从 P1b 到可执行方法

新方向的准入不再要求父代：有效、与共同初始最好规则相差不超过 0.035 gap、且呈现新 probe 行为的探索候选，可以获得两次有限开发资格。已有方向仅在其代表取得至少 0.0001 的改善、且不是已知执行结果重现时续额。B 容量为 6，每条方向谱系累计最多授予 8 个 B 开发槽位；这不是对全部 incumbent 开发的上限。

方向采用在线最近行为关联，距离不超过 0.08 则加入既有方向，否则创建新 ID。这不是连通分量合并，也不是对真实优化盆地的识别。修改产生的新方向继承父方向谱系；返回既有方向使用该方向账本。标签和 AST 不决定方向身份。有限 probe 仍可能误判，这是本轮保留的研究限制。

两种固定调度组均实际使用 B：普通开发先选最早的可用 B 条目，没有条目则开发当前最好程序。FB_U 没有最低执行保障；FB_P 通过优先调度并防止未兑现条目被淘汰来提供保障。该对照同时检验调度优先与淘汰保护，不能分别归因于其中一项。额度消耗的是提案机会，包含无效输出；报告单列实际有效评价和进步。

六组为 SP（始终开发当前最好程序）、WR（无父代探索）、FB_U、FB_P、TS_P（开发概率 0.15→0.85）、AD_P。AD 使用两个普通提案组成一个单元，按其直接全局改善/千 token 更新开发概率，强制保护步骤不进入单元长度、收益或成本。这个反馈没有估计探索的延迟价值，不解释为校准成功概率。

## 公平性与执行完整性

- 数据块 32–39，各组同区块和外层种子：六组 × 八块 = 48 次。
- 每次最多 32 提案、64 请求、300,000 token、3,600 秒；planner/coder 上限为 16,384/8,192。相同上限不等于实际 token 消耗相同。
- 最多六个独立进程。评价器直接读取显式快照，不修改全局 loader 或环境变量；每次评价记录实例 ID 和快照哈希。
- 控制器私有概率、分支池大小、保护状态不进模型提示。父代、参考、操作与目标标签的实际变化仍会改变提示，这是调度的作用路径。
- 不重试模型请求或替换任务。达到预算或请求异常时，冻结已完成候选中的最好程序作为可用输出；主分析保留这些终态任务并报告失败与未知用量。没有输出的任务显式缺失。
- 全部任务终态之后才执行 test。代码或并发正确性错误的批次不得用来推断方法效果。

## 版本与失效记录

r1 在 3 次搜索完成、test 尚未评价时因 AD 反馈口径等问题停止。r2 的线程调度与全局快照切换冲突：串行重执行确认 probe 行为混用了数据块。两批保留原始调用、错误、未执行任务及成本，但不作为方法有效性证据。r3 修复数据隔离及普通 B 使用，并改用新数据块。筛查结果无论正负均报告，不依据 test 调参补跑。

## 执行与复现

```powershell
python -m pytest -q experiments/chapter6/agent_search/s3_tsp_r3
python -m experiments.chapter6.agent_search.s3_tsp_r3.study verify --study <study>
# 仅真实执行需要本人配置的 OpenCode MiniMax M3 凭据
python -m experiments.chapter6.agent_search.s3_tsp_r3.study preflight --output <study>/preflight
python -m experiments.chapter6.agent_search.s3_tsp_r3.study search-all --study <study> --preflight <study>/preflight --live
# 以下步骤不调用模型
python -m experiments.chapter6.agent_search.s3_tsp_r3.study test-all --study <study>
python -m experiments.chapter6.agent_search.s3_tsp_r3.analyze --study <study> --output <results>
```

完整配置见 [protocol.final.json](protocol.final.json)。按区块报告全部八个配对差值、bootstrap 描述性区间、实际成本、失败率和保护分支收益。TSP14 单模型筛查不构成通用算法发现有效性或博士创新已确立的证明。
