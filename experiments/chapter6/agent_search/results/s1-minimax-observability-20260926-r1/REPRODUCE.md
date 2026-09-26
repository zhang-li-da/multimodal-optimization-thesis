# S1 复现说明

本目录保存 MiniMax M3 S1 自然分支竞争筛查的审计、回放、图表和原始归档。复现报告与审计不需要模型凭据，也不会发送网络请求。

```powershell
cd multimodal-optimization-thesis
$study = "experiments/chapter6/agent_search/s1_observability/studies/s1-minimax-observability-20260926-r1"
$out = "experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/recheck"

python -m chapter6_demo.v12_3.package unpack `
  --archive experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/raw-study.zip `
  --destination experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/unpacked

python -m chapter6_demo.agent_search.review_s1 `
  --study $study --output "$out/audit" --numeric

python -m chapter6_demo.agent_search.report_s1 `
  --study $study --audit "$out/audit" --output "$out/report"

python -m chapter6_demo.agent_search.demo_s1 `
  --study $study --audit "$out/audit" --output "$out/demo"
```

浏览器检查需要本机安装 Microsoft Edge 和 `websocket-client`：

```powershell
python -m chapter6_demo.agent_search.browser_s1 `
  --demo "$out/demo" --output "$out/browser-check"
```

`search-all --live` 是真实模型调用入口，不能用于复现审计。S1 的 12 个搜索、test 读出和原始请求响应已经归档；重新搜索会产生新的调用和新的证据批次。

审计会核对 manifest、冻结源码、控制器状态、两阶段提示、原始响应 envelope、候选源码、validation/test 数值和开发池额度账本。浮点数使用预先声明的 `atol=1e-12`、`rtol=1e-10`，离散路线、行为和程序身份要求精确一致。
