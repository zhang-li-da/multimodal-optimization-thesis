# S3 r3 下载、复核和演示

当前目录是完成后的结果，主报告是 [TECHNICAL_REPORT_ZH.md](TECHNICAL_REPORT_ZH.md)。冻结源码 51e5d5e；冻结协议与实例 62cf707；规模迁移协议 343b13f。阅读、回放和离线程序评价无需 API。

## 1. 获取本分支

~~~powershell
git clone https://github.com/zhang-li-da/multimodal-optimization-thesis.git
cd multimodal-optimization-thesis
git switch --track origin/experiment/chapter6-agent-search-s3-tsp-20260927
python -m pip install -r experiments/chapter6/v12_2/requirements.lock.txt
~~~

仓库没有真实 API 凭据。已有历史实验、失败批次和标签全部保留。默认分支可能落后于实验分支，克隆后务必切换分支。不要将“已完成报告”误读成博士方法有效性已通过。

## 2. 解压原始结果

[RAW_PARTS.json](RAW_PARTS.json) 列出九个 ZIP、字节数和 SHA-256；其中 common.zip 含协议、数据与汇总，block-32.zip 至 block-39.zip 含对应区块的六组运行、测试和派发状态。它们是独立 ZIP，解压至同一目录即可，不需要拼接字节。原单 ZIP 约 67 MB，发布为较小分包，所有成员均保留。

先核验归档：

~~~powershell
python -m experiments.chapter6.agent_search.s3_release verify --results experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927
~~~

可以用任意 ZIP 工具把九个分包解压到仓库内 local-s3-unpacked。解压后会生成 study/，包含 manifest.json、data/、runs/、tests/ 等。以下是 Python 标准库解压示例：

~~~python
from pathlib import Path
import zipfile

source = Path("experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/raw")
target = Path("experiments/chapter6/agent_search/local-s3-unpacked").resolve()
target.mkdir(parents=True, exist_ok=True)
for archive in sorted(source.glob("*.zip")):
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (target / name).resolve().is_relative_to(target):
                raise ValueError("Unexpected archive path")
        z.extractall(target)
~~~

## 3. 离线复核

~~~powershell
python -m experiments.chapter6.agent_search.s3_evidence --study experiments/chapter6/agent_search/local-s3-unpacked/study --output experiments/chapter6/agent_search/local-s3-unpacked/execution-audit.json --reexecute --workers 4
python -m experiments.chapter6.agent_search.s3_test_audit --study experiments/chapter6/agent_search/local-s3-unpacked/study --output experiments/chapter6/agent_search/local-s3-unpacked/test-audit.json --workers 4
python -m experiments.chapter6.agent_search.s3_tsp_r3.analyze --study experiments/chapter6/agent_search/local-s3-unpacked/study --output experiments/chapter6/agent_search/local-s3-unpacked/readout
~~~

以上命令不会调用模型。已有 test 文件与选中程序、实例绑定后复用；不要为复核去运行 preflight 或 search-all --live。主分析器可以读取不完整目录，本次发布额外核验 48/48 个 test 齐全；独立复现也应先确认这一点。运行 analyze 会生成一个便于本地留存的单 ZIP，公开交付仍使用已校验分包。

算法评价采用绝对/相对 1e−12 数值容差，但路线、probe 行为、实例身份必须一致。跨平台差异应逐项说明，不可直接降低标准掩盖路线不同。代码身份跨 Python AST 版本有历史风险，环境详情在 manifest.json。原始文件自身 SHA-256 必须精确一致，不适用浮点容差。

## 4. 规模迁移

冻结数据和结果位于 agent_search/studies/s3-transfer-20260927，参考为启发式上界，不是最优值。每个 evaluation JSON 保留逐实例路线、失败、局部检查数和 reference kind。查看结果不需要再执行程序。

若按同样冻结程序重建迁移评价，可将已归档 evaluations/ 保留在另一个目录，复制冻结 manifest.json 与 data/ 到新的输出目录后运行以下命令，避免覆盖原始证据：

~~~powershell
python -m experiments.chapter6.agent_search.s3_transfer run --study experiments/chapter6/agent_search/local-s3-unpacked/study --output <复制的迁移冻结目录> --workers 4
~~~

目录已含 evaluations/ 时，工具验证绑定并复用；不是重复模型搜索。规模迁移工具的源码哈希被冻结，改动依赖后应拒绝执行，不能静默使用另一种评价器。

## 5. 开题演示（约 4 分钟）

下载后直接打开 [demo/index.html](demo/index.html)。GitHub 文件页显示 HTML 源码，因此应在本机浏览器打开。页面没有服务器、模型调用、外部脚本或网络请求。

1. 约 40 秒展示顶部六组完整结果，先说清楚“执行链已成立，但主要对比未通过”。
2. 约 90 秒选择 fb_p-b37-s0，从第 1 步开始：展示新候选获正额度、B 状态、第 3 步落后父代得到修改并改善。说明这是单条历史，不是平均效果证明。
3. 约 60 秒切换 fb_p-b34-s0 的第 6/7 步：父代质量改善却没有全局增量，解释为什么局部进步并不自动意味着预算有收益。
4. 约 40 秒展示同状态关闭优先的下一动作变化，说明没有生成替代子代，不能展示不存在的反事实收益。
5. 约 30 秒回到六组平均结果与限制，说明接下来应简化保障对象与反馈归因，而不是继续堆叠模块。

浏览器自动检查遍历全部 48×32 状态、下一步按钮及移动布局，无运行时异常，无外部请求。证据见 [browser-check/browser_check.json](browser-check/browser_check.json)。
