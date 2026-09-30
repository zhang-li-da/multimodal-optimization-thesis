# S0：MiniMax M3 输出可靠性，版本化工程前置实验

设计来源：PR #4。此目录不是搜索算法效果实验。

- **r1 已工程中止**：一次响应已保存，781 tokens；原始源码见 `4716a07`，历史材料不改写。
- **r2 已完成，不通过**：84 请求、241,695 tokens；planner 独立验收14/18，coder18/18，端到端6/6；未达到 planner17/18，不能据此启动S1。
- [报告与成本](results/s0-minimax-output-calibration-20260926-r2/REPORT_ZH.md)
- [冻结 manifest](studies/s0-minimax-output-calibration-20260926-r2/manifest.json)

源码 r2：`bccb7f1`；冻结提交 `ce88a5a`。运行目录打包为结果下的 `raw-study.zip`，索引列每个文件的 SHA-256，可由 `chapter6_demo.v12_3.package unpack` 校验解包。运行器单次发送，已落盘响应不会重发；未知状态停止、不补位。

```powershell
python -m pytest experiments/chapter6/s0_output_calibration/test_calibration.py -q
python -m chapter6_demo.s0_output_calibration.calibration analyze --study experiments/chapter6/s0_output_calibration/studies/s0-minimax-output-calibration-20260926-r2 --output s0-analysis-new
```

分析不调用模型。运行命令需要 `--live` 且核验全部冻结源码/环境。源代码、协议或模板变化必须另立批次。
