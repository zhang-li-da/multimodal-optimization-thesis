# v1.2.3：MiniMax M3 真实配对筛查

本版按 2026-09-26 用户指定的本机 OpenCode MiniMax M3 coding plan 开展首轮真实筛查，并制作开题回放演示。原 2 模型 × 4 区块草案保持原样；本版明确修订为 **1 模型 × 2 控制器 × 8 个配对区块＝16 次搜索**。结论只适用于 MiniMax M3，不提供跨模型证据。

搜索继续使用 44bafd2 的 V121SearchState、v1.2.2 runner、提示、评分与评价器。新目录只提供任务准备和调度、结果分析与演示。旧代码/结果不改写；后续四臂归因与选择器实验另行版本管理。

最终设计见 [PROTOCOL_ZH.md](PROTOCOL_ZH.md) 和 [protocol.final.json](protocol.final.json)。源码及协议先提交；数据和不可变 manifest 再单独冻结提交；所有真实运行保留完整请求、响应与成本。preflight 是单独计费的一个工程请求，不是一次搜索。

按顺序运行（真实执行命令需要显式 live 参数）：

~~~powershell
python -m pytest experiments/chapter6/v12_3/test_study.py -q
python -m chapter6_demo.v12_3.study prepare --output experiments/chapter6/v12_3/studies/minimax-20260926-r1-draft
# 提交草案、源码及最终协议，使工作区干净，再执行 freeze。
python -m chapter6_demo.v12_3.study freeze --draft experiments/chapter6/v12_3/studies/minimax-20260926-r1-draft --output experiments/chapter6/v12_3/studies/minimax-20260926-r1
# 冻结 manifest 另行提交后，运行一次连通性请求，再按冻结顺序搜索。
python -m chapter6_demo.v12_3.study preflight --live-probe --output experiments/chapter6/v12_3/results/minimax-20260926-r1/preflight
python -m chapter6_demo.v12_3.study search-all --live --study experiments/chapter6/v12_3/studies/minimax-20260926-r1 --preflight experiments/chapter6/v12_3/results/minimax-20260926-r1/preflight
python -m chapter6_demo.v12_3.study test-all --study experiments/chapter6/v12_3/studies/minimax-20260926-r1
~~~

所有搜索结束或记录基础设施停止后，才统一执行 test。运行中的目录由 Git 忽略，最终归档到 results/；不能以部分运行或 fixture 替代完整任务表。真实运行数据与历史 r2 回放、合成控制用例分别标记。
