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

## 查看交付与离线复核

- [技术报告与完整结果](results/minimax-20260926-r1/REPORT_ZH.md)
- [架构与方法边界](../../../docs/chapter6/V123_ARCHITECTURE.md)
- [离线 HTML 演示](results/minimax-20260926-r1/demo/index.html)；下载后直接打开，无需模型或 Python
- [开题演示说明](../../../docs/chapter6/V123_DEMO_GUIDE.md)

下列命令只读取归档、评价已冻结程序或生成演示，**不调用模型**。先验证外层文件校验，再解包 ZIP。ZIP 内使用可移植相对路径，成员逐一校验；与本地同路径文件不一致时拒绝覆盖。

~~~powershell
python -m chapter6_demo.v12_2.archives experiments/chapter6/v12_3/results/minimax-20260926-r1
python -m chapter6_demo.v12_3.package unpack --archive experiments/chapter6/v12_3/results/minimax-20260926-r1/raw-study.zip --destination .
python -m chapter6_demo.v12_3.analyze --study experiments/chapter6/v12_3/studies/minimax-20260926-r1 --output experiments/chapter6/v12_3/local-analysis --preflight experiments/chapter6/v12_3/results/minimax-20260926-r1/preflight
python -m chapter6_demo.v12_3.verify_readout --study experiments/chapter6/v12_3/studies/minimax-20260926-r1 --output experiments/chapter6/v12_3/local-numeric.json
python -m chapter6_demo.v12_3.build_demo --study experiments/chapter6/v12_3/studies/minimax-20260926-r1 --analysis experiments/chapter6/v12_3/local-analysis --output experiments/chapter6/v12_3/local-demo
~~~

首次真实运行严格检查冻结环境；离线复核记录当前环境和数值差异，不会重发任何模型请求。跨平台复核不能靠删除失败记录来获得“通过”。如果只想观看演示，完全不需要运行上面的 Python 命令。

可选浏览器/视频再生成依赖：websocket-client、Pillow、numpy 和 ffmpeg（只用于离线画面导出，不是搜索依赖）。本机可使用 Edge；其他电脑通过 --edge 指定兼容的 Chromium 可执行文件。

~~~powershell
python -m chapter6_demo.v12_3.browser_check --demo experiments/chapter6/v12_3/local-demo --output experiments/chapter6/v12_3/local-browser-check --edge "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
# 附加 --ffmpeg <ffmpeg可执行文件> 可生成 4分20秒字幕视频；不是现场调用录屏。
~~~

历史 r2、独立评审、原双模型草案、协议标签都保持不变。本次分析输出、视频和结果标签另行发布；原始任务 manifest 的 FROZEN_PENDING_EXECUTION 是其冻结时状态，不会为更新执行进度而改写。实际完成状态由 runs/status.json、独立 tests/ 和 results/analysis/runs.csv 联合给出。
