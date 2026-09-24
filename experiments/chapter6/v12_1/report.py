"""Generate the Chinese offline report from recorded audit/test artifacts."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .audit_r2 import OUTPUT, REPO, write_json
from chapter6_demo.v12_1_controller import v121_source_fingerprint


def main():
    audit = json.loads((OUTPUT / "r2_audit.json").read_text(encoding="utf-8"))
    numeric = json.loads((OUTPUT / "numeric_verification_windows.json").read_text(encoding="utf-8"))
    junit_path = OUTPUT / "tests.xml"
    xml = ET.parse(junit_path)
    # Host names are unnecessary for reproducibility; Python/platform are
    # explicitly recorded in the numeric verification file.
    for suite in xml.iter("testsuite"):
        suite.attrib.pop("hostname", None)
    xml.write(junit_path, encoding="utf-8", xml_declaration=True)
    suites = list(xml.iter("testsuite"))
    tests = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
             for key in ("tests", "errors", "failures", "skipped")}
    assert tests["errors"] == tests["failures"] == tests["skipped"] == 0
    assert numeric["passed"]
    probe = audit["matched_history_probe"]
    rows = audit["cells"]
    lines = [
        "# 第六章 v1.2.1 技术报告：调度对照修正与离线机制验证", "",
        "**本轮新增模型调用：0。** 本版完成架构修正、构造历史测试、原r2审计、坐标快照及本地程序重执行。没有启动16次或40次新搜索。所有模型搜索质量数字仍来自旧r2；新控制器尚无模型搜索性能结果。", "",
        "## 1. 研究判断与前章承接", "",
        "已有r2证据支持识别局部改进—保留B分支—安排续开发—评价子代的执行链。新复核发现固定臂与关系臂的普通调度不同，且W仍参与关系路径，因此旧性能差异不是B内排序的独立效应。当前应研究有限执行证据下的已知重现与可继续开发改进，并把有效改进转化为有上限的执行机会。关系排序只是待检验模块。", "",
        "第三章的机制综述与DWD提供质量/分布分别评价的依据；第四章MSLS-MA支持多解维护与局部开发，RMC-CMSA支持按实际过程和终端结果分配资源；第五章HDADE支持双空间评价。程序空间没有完整模式参考集，不能直接将行为代表数当作真实模式召回。", "",
        "## 2. 本版架构与修正", "",
        "A保存质量/行为代表，B独立保存有限开发机会，M记录候选分类、亲子改进和决策证据。两个开发控制器共享niche普通调度、随机数消耗、准入条件、池容量3、每条2次额度和奇数开发时隙；W不参与决策。仅B内选择不同：固定臂FIFO耗尽最老条目，关系臂对同一可用集合按族证据与局部收益组合评分。", "",
        "族分数由实际评价事件计算：Beta(1,1)平滑的竞争性非重现父代改进比例，少于2个族样本回退共同统计。总分为q(tag)+0.25×parent_gain/(1+attempts)。两臂共同记录评分与可选集合，提示仅暴露decision_step；没有完整转移图或置信校准。", "",
        "日志分开准入资格/实际保留与尝试/成功深度。祖先深度在池淘汰后继续保存。重复节点不能扣款两次，allocation需匹配父代和剩余额度，失败与重复子代均消费一次机会。已知规则重现不能刷新额度。", "",
        "原r2控制器没有改动，v1.2.1使用独立模块；旧在线runner不会自动切到新控制器。未来接入新搜索需单独版本冻结。完整参数和数据流见[架构说明](../../../../../docs/chapter6/V12_ARCHITECTURE.md)。", "",
        "## 3. r2 按模型×控制器复核（事后诊断）", "",
        "| 模型 | 控制器 | 运行/无入池 | 续开发/有效 | 父代/全局改进 | 多分支时隙 | 重启/未尝试标签 | W独有引用 | 最大尝试/成功深度 | Test gap |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(f"| {r['model']} | {r['controller']} | {r['runs']}/{r['runs_without_admission']} | "
                     f"{r['followups']}/{r['valid_children']} | {r['parent_improvements']}/{r['global_improvements']} | "
                     f"{r['multi_branch_slots']} | {r['restarts']}/{r['restarts_to_untried_tags']} | "
                     f"{r['w_only_references']} | {r['max_attempt_depth']}/{r['max_success_depth']} | {100*r['mean_test_gap']:.3f}% |")
    lines += ["",
        "关系组共25次重启，其中23次指向尚未尝试的标签，28次参考来自W且不在A中。关系组3/6运行没有入池，只有4个开发时隙有至少两个可选分支。原最大深度4包含失败尝试；连续成功入池的改进链最大为3。", "",
        "30个续开发子代中6个改善父代，其中5个同时改善当时全局validation最优。平均test gap仍为niche 5.759%、固定开发5.569%、关系开发6.379%；关系组相对固定组平均高0.811个百分点，仅1/6配对更低。每模型仅3个配对区块，属于描述性筛查。", "",
        "原r2合并触发门槛保持原定义。r2_cells.csv额外给出新版最低暴露门槛的事后诊断，不能冒充原预注册结果。未来每个模型×控制器分别报告至少4次续开发、1个有效子代和2个多分支时隙的覆盖；即使父代改进为0也不得排除运行。", "",
        "还更正了一项展示口径：旧initial_best_seed_test_gap实际按test取共享种子最小损失，只能作种子上界，不能称为validation选出的种子基线。旧数值保留并明确标注。", "",
        "## 4. 本轮完成的离线验证", "",
        f"- 测试：{tests['tests']}项通过，覆盖v1.1/v1.2回归、v1.2.1真实choose路径以及数值比较器。新机制测试显式封禁模型客户端和socket。",
        f"- 原控制器：{audit['archived_runs_replayed']}/18运行、{audit['archived_decisions_replayed']}/144决策精确回放；原源码指纹仍匹配manifest。",
        f"- 修正版同历史探测：普通决策{probe['ordinary_equal']}/{probe['queries']}一致；非开发完整提示{probe['nondevelopment_prompts_equal']}/{probe['nondevelopment_queries']}一致；B内父代{probe['branch_choice_differences']}次不同。",
        f"- 使用实例快照重执行：{numeric['validation_programs']}个validation程序、{numeric['test_programs']}个test程序；Windows本机{numeric['strict_equal']}/{numeric['program_evaluations']}严格一致，控制器决策无差异。",
        "- 324个TSP实例坐标事后重建并匹配原9个split指纹；记录生成来源，不宣称它们是在r2搜索时同步保存的。", "",
        "构造夹具中两个可选分支的局部收益与使用次数相同，仅改变族历史便改变实际choose的父代。这证明该信号进入执行路径。另有失败第4层尝试而成功深度保持3的反例测试。", "",
        "同历史探测使用相同已归档候选及其评价；两个策略副本沿各自选择的allocation推进，以便开发额度和后续可用分支不会被遗漏。没有生成新选择对应的模型子代，不能估计其最终收益。全部局部评价都是历史程序重执行，没有发现新的模型候选。", "",
        "## 5. 当前缺陷与未解决问题", "",
        "1. 新控制器只经离线验证，尚不知道在新模型生成轨迹上是否更有效。小容量池和8提案短搜索可能仍缺少足够多的选择机会。",
        "2. 族标签统计仍是稀疏启发式；收益组合排序优于FIFO也不能单独证明关系信息的贡献，需加入纯局部收益/使用次数基线与打乱族证据的控制。",
        "3. 有限probe及validation上的精确重现不等价于程序语义等价；验证集自适应复用风险仍在。本轮未加入witness或更复杂关系图。",
        "4. 多个程序的应用价值尚未验证。需独立路由训练集、冻结选择器、初始规则集、同容量集合及最佳单规则对照，并计入选择成本。oracle只作上界。",
        "5. 新数值规范尚只有Windows本机验证；其他环境需另行运行。原用户提供的b4304f5审阅提交不在本地，无法宣称已逐项核验其全部跨平台差异。",
        "6. 当前任务是小型TSP受限规则，不能据此外推到通用AutoML、稳定多任务优势或博士级创新已确立。", "",
        "## 6. 后续技术路线与实验设计", "",
        "下一批仅拟定2个开发控制器×2个模型×4个全新区块3–6，共16次搜索；每运行8个总提案，记录实际token和执行成本。两臂同一普通调度，只比较B内FIFO与组合排序。完整设计、触发统计、失败保留、运行顺序和冻结要求见[协议草案](../../preregistration.md)。本轮不启动，也不扩至40次。", "",
        "若组合排序值得继续，独立增加收益排序/证据打乱基线；再用独立训练且冻结的选择器检验多算法集合用途。更大规模任务、witness、W和关系图仅在最小机制有增量证据后引入。若无增量，应删除相应创新主张。", "",
        "## 7. 复现与版本管理", "",
        "本包保留r2原始数据引用及各运行SHA-256、逐步选择诊断、分单元CSV、实例坐标、Windows数值回放、测试XML、源码指纹和SHA256SUMS。按[复现说明](../../README.md)运行离线脚本，任何新机器数值回放使用新输出路径。", "",
        "新分支experiment/v1.2.1-isolated-branch-policy，审计修订标签chapter6-v1.2.1-mechanism-only-20260924-r1。早期机制标签、旧r2标签、冻结源码及原始ZIP不改写；v1.2 r1仍作废。本报告与旧r2的质量结果分开解释。",
    ]
    (OUTPUT / "REPORT_ZH.md").write_bytes(("\n".join(lines) + "\n").encode())
    # This is a post-hoc release manifest, not a model-search preregistration.
    manifest = {"kind": "offline mechanism validation; post-hoc archival audit",
                "new_model_calls": 0, "new_search_runs": 0,
                "source_fingerprint_sha256": v121_source_fingerprint(),
                "historical_source_fingerprint_sha256": audit["source_fingerprint"],
                "tests": tests, "branch": "experiment/v1.2.1-isolated-branch-policy",
                "tag": "chapter6-v1.2.1-mechanism-only-20260924-r1",
                "future_16_run_search": "draft; not executed",
                "source_commit": "Resolve the immutable release tag; this post-hoc manifest is not a search freeze.",
                "r2_quality_results_changed": False}
    write_json(OUTPUT / "manifest.json", manifest)
    print(json.dumps({"tests": tests, "new_model_calls": 0, "report": "REPORT_ZH.md"}))


if __name__ == "__main__":
    main()
