# 版本与交付索引

本批次分支：experiment/v1.2.3-minimax-screening。结果标签：chapter6-v1.2.3-minimax-screening-20260926-r1。结果标签与协议标签分开，任何旧标签不移动。

| 层次 | 不可变引用 | 说明 |
|---|---|---|
| 运行算法 | 44bafd2344d6a25e247c93c62280e6bf20e5e508 | v1.2.2 运行器、V121 控制器及既有评价器 |
| 协议/调度 | 15caaa5 | 本轮单模型八区块方案 |
| 数据准备 | 628f0ad6f469269d5d6d7f7b69a9ea476cab4080 | 864 个新实例，旧草案块 3–6 字节不变 |
| 冻结清单 | b24b5dafe3a0ca5438fe4a0f236356431bf4f467 | 搜索开始前已推送 |
| 协议标签 | chapter6-v1.2.3-minimax-protocol-20260926-r1 | 精确指向冻结清单提交 |
| 评审合并 | c018aa6 | 保留 fbb7a7a 原独立评审历史 |
| 离线工具 | e75084f 及结果标签中的修订 | 分析、打包与演示，不被冻结运行器导入 |
| 实测交付 | chapter6-v1.2.3-minimax-screening-20260926-r1 | 全部真实记录、报告和最终演示 |

SHA256SUMS.txt 逐文件核验当前结果包；raw-study.index.json 逐成员记录 ZIP 内原始文件。manifest_sha256 是 manifest 内容去除自身字段后的规范 JSON 哈希，区别于整个 manifest 文件字节的 SHA256；二者不要混淆。

历史 r2、原始双模型草案、旧评审、旧标签和程序源文件不覆盖。旧稿保留并添加最新章节稿入口。临时未发布的展示预览保存在被 Git 忽略的 local-* 目录，最终 HTML、截图与视频以本目录哈希为准，研究记录没有因界面编码修正而变化。

首读顺序：REPORT_ZH.md → analysis/pairs.csv、cells.csv、runs.csv → factor_audit/ 与 response_diagnostics/ → demo/index.html。若要核对原请求和程序，按 v12_3/README.md 解包 raw-study.zip；此过程不调用模型。

所有 16 次搜索和后续本地评价均已完成。无新增模型调用的工具包括 analyze、audit_factor、diagnose_responses、verify_readout、package、build_demo 和 browser_check。不要为了查看结果执行带 --live 的搜索命令。
