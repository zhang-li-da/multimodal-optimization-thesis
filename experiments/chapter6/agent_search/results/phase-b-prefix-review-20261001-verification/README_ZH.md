# 固定远端提交复核凭据

被验证的结果提交：`2c55b72022e581172bcb9bcb5a61fabb84258a21`。结果标签：`chapter6-phase-b-prefix-review-20261001`。本目录是随后生成的复核凭据，不修改该提交、前缀数据、检查点或任何调用记录。

已从 GitHub 官方 Contents API 以固定 `ref` 下载本轮 44 个交付文件及 HASHES.json，并核对全部字节。三个 ZIP 均可解包，319 个文件成员逐项 SHA-256 一致。manifest、8 个任务、16 个计划检查点、16 个有效提案、33 次请求、32 个持久化响应、127,470 已知 tokens 和 1 次未知请求均与本地一致。

方法及审查源码共 26 个文件通过校验：25 个逐文件下载；较大的 DATA_SOURCE_SHA256.tsv 从同一固定提交下载并校验过的 review-sources.zip 读取，和 SOURCE_SHA256.json 的成员摘要一致。没有下载全仓库 ZIP。

GitHub raw 文件域名出现连接重置/TLS 超时，因此改用官方 API。supporting-evidence.zip 首次 API 传输少 38,463 字节，重新下载成功；DATA_SOURCE_SHA256.tsv 的单独传输也不完整，改用上述远端 ZIP 的完整成员。未完成的响应没有作为完整文件参与校验，未从本地原稿补齐远端证据。最终 REMOTE_VERIFICATION.json 为通过记录；传输重试不涉及模型请求。

fetch_api.py 可用现有 Git 凭证进行同样复核，不保存或打印凭证；`--resume-files` 只续传缺失的 GitHub 文件，绝不恢复实验。主结果中的 verify_remote.py 提供公开 raw 域名的免凭证路径。

本轮没有新增模型调用或程序数值评价，没有恢复旧 halt，没有启动续开发或开放 Test。PROCESS_AUDIT.json 记录交付前检查结果。用户原工作区的未提交目录保留，修改在独立 worktree 中完成。

此凭据确认上传内容与本地交付一致，不代表 8 个公共前缀已全部完成，不提供方法有效性的独立证据。
