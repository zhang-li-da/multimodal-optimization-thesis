# v1.2.2 启动准备与离线集成验证报告

## 结论

本结果包完成了第六章下一轮真实搜索前的启动准备和离线验收。它证明新的运行器能够保存并恢复搜索状态、持久化请求和响应、隔离 validation 与 test，并对 ZIP 成员路径和程序身份哈希提供可复核的兼容处理。

本轮没有调用 Qwen、MiniMax 或任何其他真实模型 API，也没有执行 16 次搜索矩阵。因此，本报告不提供控制器性能优越性、关系排序有效性或博士章节创新性已经成立的证据。两个控制器的完整链路只使用 `fixture-no-api` 假响应，属于工程集成夹具。

## 验收范围

| 项目 | 本轮结果 |
|---|---|
| 代码版本 | `2a870dd45612a90ce8fc653f4276181a90b9d4cf` 的 v1.2.2 启动层工作区 |
| 新真实模型调用 | 0 |
| 新在线搜索 | 0 |
| fixture 控制器 | `niche_fixed_dev`、`relational_branch` |
| fixture 提案数 | 每个控制器 8 步 |
| fixture provider 请求 | 每个控制器 16 次（planner/coder），服务为 `fixture-no-api` |
| 测试 | 74 passed |
| 历史回放 | 18 次运行、144 次决策、330 项程序评价 |
| 新数据草案 | 16 个任务、区块 3--6、432 个新实例 |

fixture 使用历史 r2 block 0 的坐标，仅用于验证控制器和文件状态机；它不使用新 block 3--6，也不应被解释为新搜索结果。

## 已验证的运行链路

每个 fixture 控制器均完成了以下流程：

1. 从三个手写种子开始，只读取 probe/validation；
2. 逐步写入不可变的 decision、planner/coder request、response、candidate 和 checkpoint；
3. 在中断点重新加载 checkpoint，重放 `choose` 和 `observe`，得到与连续运行相同的控制器状态；
4. 冻结 validation 选择结果后，单独进程才读取 test；
5. 将 test 输出写入独立目录，搜索配置中 `test_access` 保持为 `false`。

两个控制器的关键夹具摘要如下。损失值来自固定夹具和旧 block 0，只用于状态链路检查。

| 控制器 | 有效生成候选 | B 准入 | B 开发尝试 | 成功扩展 | 最大尝试深度 | 最大成功深度 | validation 最佳损失 | 恢复一致 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `niche_fixed_dev` | 7/8 | 2 | 4 | 1 | 3 | 2 | 0.0582290764 | 是 |
| `relational_branch` | 7/8 | 2 | 4 | 1 | 3 | 2 | 0.0582290764 | 是 |

两臂共享 `shared_niche` 普通调度，关闭 W，开发池采用 `fifo_until_exhaustion`。夹具中 `family_differentiated_branch_slots` 和 `full_vs_gain_choice_differences` 均为 0；这只是说明夹具没有产生可用于性能归因的族排序差异，不能证明两个策略在真实模型下等价。

## 历史回放与身份兼容

`evidence/r2-replay/` 保存了旧 r2 的离线回放。回放没有重新生成程序，也没有发起模型请求。保存的结果包括：

- 18 次归档运行和 144 次控制器决策的审计；
- 330 项 validation/test 程序评价的本机数值回放；
- 原始严格结果与命名的数值兼容结果分开保存。

`evidence/identity/windows312.json` 和 `windows313.json` 对 327 条有程序哈希的历史评价执行了显式 AST 结构身份核查。两种 Python 版本都得到 327/327 的旧 3.13 哈希格式匹配，结构哈希字段完整，原始代码哈希与结构哈希序列一致。Python 3.12 的运行时旧哈希仍不同，这是 AST 展示格式差异，不能改写为原始严格验证通过。

原 Linux 严格验证失败文件保持不变；新报告只提供有名的兼容性诊断，不把哈希格式解释扩大为跨平台数值、路径或决策完全等价。

## 新搜索草案

`evidence/draft-study/manifest.json` 保留了尚未执行的任务草案：

- 两个 provider/model：`alibaba-token-plan-cn/qwen3.7-plus` 和 `minimax-cn-coding-plan/MiniMax-M3`；
- 两个开发控制器；
- 四个新数据区块 3、4、5、6；
- 每项 8 个 proposal；
- 旧数据共 324 个实例，新数据共 432 个实例；ID 和精确坐标碰撞数为 0；
- `geometric_equivalence_checked` 为 `false`，因此尚未声称分布或几何等价性已经证明；
- 状态为 `DRAFT_NOT_EXECUTABLE`，没有最终协议、provider 固定版本或决策阈值。

草案不允许 `search --live` 直接读取。只有在协议、源码和环境单独冻结后，才允许进入真实搜索。

## 当前能支持的研究判断

本轮支持以下工程判断：

- 质量保护分支可以被单独保留，并获得受限的后续开发机会；
- 请求不确定时会停止运行，不会自动重发可能已经到达服务端的请求；
- checkpoint、候选身份、配置和冻结 readout 可以相互校验；
- validation 选择与 test 评价已经分离；
- 旧归档能够在当前环境下被重放并进行显式身份诊断。

本轮不能支持以下结论：

- `relational_branch` 优于 FIFO 或 `niche_fixed_dev`；
- 族证据相对局部收益排序有独立增量；
- 多算法集合在新实例上比单一算法或初始规则集合更有用；
- 第六章方法已经具有稳定性能优势或已确立博士论文创新性。

下一步应先冻结最终协议、实际 provider/model 版本、实用收益阈值和源码提交，再按两个模型 × 两个控制器 × 四个新区块执行 16 次真实搜索，并保留收益排序、族证据打乱和独立选择器对照。

## 复现与证据索引

从仓库根目录执行：

```powershell
python -m pytest experiments/chapter6/v12_2 experiments/chapter6/v12_1 experiments/chapter6/v12/test_v12.py experiments/chapter6/v11/test_v11.py -q
python -m chapter6_demo.v12_2.archives experiments/chapter6/v12_2/results/readiness-20260925
```

结果包中的 `tests.xml` 是本轮 74 项测试的 JUnit 输出，`SHA256SUMS.txt` 对包内发布文件逐项校验。所有本轮证据均不含 API 密钥。
