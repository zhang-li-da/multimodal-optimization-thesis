# S0：MiniMax M3 任务形态输出校准

本批次先解决 v1.2.3 暴露的任务输出问题。它只比较输出上限，不能把完整率提升解释成搜索算法收益，也不使用 TSP 实例或隐藏测试结果。

执行顺序：

~~~powershell
python -m pytest experiments/chapter6/s0_output_calibration/test_calibration.py -q
python -m chapter6_demo.s0_output_calibration.calibration freeze --output experiments/chapter6/s0_output_calibration/studies/s0-minimax-output-calibration-20260926-r1
# 提交源码和冻结 manifest 后再执行：
python -m chapter6_demo.s0_output_calibration.calibration run --study experiments/chapter6/s0_output_calibration/studies/s0-minimax-output-calibration-20260926-r1 --live
python -m chapter6_demo.s0_output_calibration.calibration analyze --study experiments/chapter6/s0_output_calibration/studies/s0-minimax-output-calibration-20260926-r1 --output experiments/chapter6/s0_output_calibration/results/s0-minimax-output-calibration-20260926-r1
~~~

S0 的最多 84 次请求分为 planner/coder 校准、独立验收和 6 个端到端诊断用例。若独立验收未通过，端到端结果只作为诊断，S1 不自动启动。所有原始请求和响应均保留；分析脚本不调用模型。
