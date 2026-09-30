"""Publication-oriented figures from the saved block-level analysis; no searches."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from chapter6_demo.v12_2.common import file_sha, read_json, save_json


def plot(analysis, output):
    analysis, output = Path(analysis), Path(output)
    if output.exists():
        raise ValueError("Preserve prior figures; select a new directory.")
    output.mkdir(parents=True)
    summary = read_json(analysis / "summary.json")
    with (analysis / "pairs.csv").open(encoding="utf-8", newline="") as stream:
        pairs = list(csv.DictReader(stream))
    complete = [p for p in pairs if p["complete_pair"] == "True"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.dpi": 180, "pdf.fonttype": 42})
    blocks = np.asarray([int(p["block"]) for p in complete])
    delta = np.asarray([float(p["delta_pp"]) for p in complete])
    fig, (ax, right) = plt.subplots(1, 2, figsize=(10.4, 4.1), constrained_layout=True)
    if complete:
        ax.plot(blocks, [100*float(p["fixed_gap"]) for p in complete], "o-", color="#0b756b", label="FIFO")
        ax.plot(blocks, [100*float(p["relation_gap"]) for p in complete], "s-", color="#b77625", label="Full ranking")
        ax.set_xticks(blocks); ax.legend(frameon=False)
        right.bar(blocks, delta, color=["#0b756b" if d < 0 else "#b77625" for d in delta], width=.65)
        right.set_xticks(blocks)
    ax.set(xlabel="Paired data block", ylabel="Frozen-program test gap (%)", title="MiniMax M3: all complete pairs")
    right.axhline(0, color="#40525d", linewidth=1)
    right.axhline(-summary["predefined_practical_delta_pp"], color="#0b756b", linewidth=1, linestyle="--", label="Prespecified practical gain")
    right.set(xlabel="Paired data block", ylabel="Full ranking - FIFO (percentage points)", title="Negative differences favor full ranking")
    right.legend(frameon=False, loc="best", fontsize=8)
    fig.suptitle("Exploratory, equal 8-proposal budget; independent unit = data block", fontsize=11)
    for suffix in ("png", "pdf"):
        fig.savefig(output / ("paired_test_gap." + suffix))
    plt.close(fig)
    cells = summary["cells"]
    fields = ["valid_generated", "branch_admissions", "branch_evaluations", "valid_branch_children",
              "multi_branch_slots", "family_differentiated_slots", "full_vs_gain_differences", "branch_parent_improvements"]
    labels = ["Valid generated programs", "B admissions", "B continuation attempts", "Valid B offspring",
              "Multi-branch slots", "Different family priorities", "Full vs gain-only choice", "B offspring parent gains"]
    y = np.arange(len(fields))
    fig, ax = plt.subplots(figsize=(9.2, 5.0), constrained_layout=True)
    for i, (cell, color) in enumerate(zip(cells, ("#0b756b", "#b77625"), strict=True)):
        bars = ax.barh(y+(i-.5)*.34, [cell[k] for k in fields], height=.31,
                       label="FIFO" if i == 0 else "Full ranking", color=color)
        ax.bar_label(bars, padding=4, fontsize=9)
    ax.set_yticks(y, labels); ax.invert_yaxis(); ax.legend(frameon=False)
    ax.set(xlabel="Count across all eight planned runs per arm",
           title="Mechanism exposure: attempts are not independent search replications")
    ax.margins(x=.15)
    for suffix in ("png", "pdf"):
        fig.savefig(output / ("mechanism_exposure." + suffix))
    plt.close(fig)
    files = {p.name: file_sha(p) for p in sorted(output.iterdir())}
    save_json(output / "figure_provenance.json", {"analysis_summary_sha256": file_sha(analysis / "summary.json"),
        "paired_csv_sha256": file_sha(analysis / "pairs.csv"), "files": files,
        "new_model_calls": 0, "new_program_evaluations": 0}, immutable=True)
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(plot(args.analysis, args.output)))
