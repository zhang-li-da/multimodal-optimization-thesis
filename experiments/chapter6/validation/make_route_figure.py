"""Export a standalone vector technical route with implementation status."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def main():
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13.5, 8.2))
    fig.patch.set_facecolor("#fbfcf9")
    ax.set_xlim(0, 13.5); ax.set_ylim(0, 8.2); ax.axis("off")
    dark, teal, amber, muted = "#263d38", "#17695e", "#ab712b", "#5d726c"

    def box(x, y, w, h, title, details, proposed=False):
        color = amber if proposed else teal
        patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.10",
                               facecolor="#faf2e4" if proposed else "#eaf2ec", edgecolor=color,
                               linewidth=1.2, linestyle="--" if proposed else "-")
        ax.add_patch(patch)
        ax.text(x + .16, y + h - .25, title, fontsize=12, fontweight="bold", color=color, va="top")
        ax.text(x + .16, y + h - .61, details, fontsize=9.6, color=dark, va="top", linespacing=1.65)

    def arrow(a, b, color=teal, dashed=False):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=14,
                                    color=color, linewidth=1.4, linestyle="--" if dashed else "-"))

    ax.text(.35, 7.78, "第六章技术路线：执行辨识与质量保护的多模态程序搜索", fontsize=18,
            fontweight="bold", color=dark)
    ax.text(.35, 7.4, "实线绿色：已实现并复核的 v1　　虚线棕色：待实现与独立验证的下一版机制", fontsize=11, color=muted)
    box(.4, 5.55, 2.65, 1.35, "任务、规则接口与预算", "固定拟合 / probe / 验证划分\n有界函数语言；硬 token 上限")
    box(3.6, 5.55, 2.65, 1.35, "规划 → 编码 → 执行", "模型生成真实程序\n统一任务评价与失败记录")
    box(6.8, 5.55, 2.65, 1.35, "质量与行为初筛", "质量改进、行为距离、互补用途\n相似与无收益重复分开定义", True)
    box(10, 5.55, 3.05, 1.35, "冻结输出 → 独立测试", "共同质量门槛、模式泛化\n质量 / 用途 / 完整成本")
    arrow((3.05, 6.23), (3.55, 6.23)); arrow((6.25, 6.23), (6.75, 6.23))
    arrow((9.45, 6.23), (9.95, 6.23))

    box(.4, 3.25, 3.55, 1.50, "质量保护的资源分配", "保留有益的模式内开发\n收益 / 成本估计与探索保底\n继续、引用、补测、重启、激活", True)
    box(4.55, 3.25, 4.15, 1.50, "A / W / M 与条件转移关系", "A：质量合格的行为代表\nW：有条件增量的真实中间轨迹\nM：起始模式—操作—终端与实际成本", True)
    box(9.35, 3.25, 3.70, 1.50, "独立 witness 辨识", "确认接近 / 确认分离 / 未决\n新区间证据、误拆 / 误并与执行费用\n不假设相似关系传递", True)
    arrow((8.2, 5.52), (10.4, 4.82), amber, True)
    arrow((9.27, 4.03), (8.78, 4.03), amber, True)
    arrow((4.48, 4.03), (4.03, 4.03), amber, True)
    arrow((2.18, 4.83), (4.2, 5.50), amber, True)
    ax.text(6.75, 2.88, "原 v1 已有 A/W/M；此处新增的是关系可靠性、质量保护与成本决策，尚未证明有效。",
            fontsize=9.2, color=muted, ha="center")

    box(.4, .75, 3.75, 1.60, "阶段 1：冻结原型验证", "2 模型 × 3 任务 × 10 新划分\n原 relational 与 niche；同 token 上限\n预定优效 / 非劣与多重比较门槛")
    box(4.75, .75, 3.75, 1.60, "阶段 2：机制与因果归因", "等价类可枚举基准、误判和费用\n图边打乱、质量 UCB、固定大 probe\nW 删除 / 顺序打乱 / 真轨迹", True)
    box(9.10, .75, 3.95, 1.60, "阶段 3：博士章节验收", "最近邻完整实现 + 独立任务泛化\n真实机器学习算法 / 模块发现\n条件理论性质、失效案例、可复现证据", True)
    arrow((4.18, 1.55), (4.7, 1.55), amber, True)
    arrow((8.53, 1.55), (9.05, 1.55), amber, True)
    ax.text(.4, .28, "推进原则：每阶段按证据通过；未通过不继承有效性主张，不把生成程序的工程可行性等同于博士创新成立。",
            fontsize=10, color=muted)
    out = Path("chapter6_validation/results"); out.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(out / ("technical_route." + ext), dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print("Exported technical_route.png / .pdf / .svg")


if __name__ == "__main__":
    main()
