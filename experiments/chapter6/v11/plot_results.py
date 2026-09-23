"""Publication-friendly plots from the frozen screening analysis; no API calls."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


METHODS=("niche","relational","relational_qp","relational_rr","relational_qp_rr")
LABELS=("Niche","00","10 (QP)","01 (RR)","11 (both)")
COLORS=("#64748b","#64748b","#0f766e","#b45309","#2563eb")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis")
    args=parser.parse_args()
    directory=Path(args.analysis)
    with (directory/"run_level.csv").open(encoding="utf-8-sig",newline="") as stream:
        rows=list(csv.DictReader(stream))
    with (directory/"paired_contrasts.csv").open(encoding="utf-8-sig",newline="") as stream:
        contrasts=list(csv.DictReader(stream))
    models=("qwen3.7-plus","MiniMax-M3")
    budgets=("slots8","tokens30000")
    tasks=("tsp","binpack")
    plt.rcParams.update({"font.size":9,"axes.spines.top":False,"axes.spines.right":False,
                         "figure.dpi":130,"savefig.dpi":220})
    figure,axes=plt.subplots(2,4,figsize=(14,7),sharey="row",layout="constrained")
    for task_index,task in enumerate(tasks):
        for model_index,model in enumerate(models):
            for budget_index,budget in enumerate(budgets):
                axis=axes[task_index,2*model_index+budget_index]
                cell=[r for r in rows if r["task"]==task and r["model"]==model and r["regime"]==budget and r.get("validation_selected_test_loss")]
                for block in range(5):
                    values=[next((100*float(r["validation_selected_test_loss"]) for r in cell
                                  if r["method"]==method and int(r["block"])==block),np.nan) for method in METHODS]
                    axis.plot(range(5),values,color="#cbd5e1",linewidth=.8,zorder=0)
                for index,method in enumerate(METHODS):
                    values=[100*float(r["validation_selected_test_loss"]) for r in cell if r["method"]==method]
                    if values:
                        axis.scatter([index]*len(values),values,s=19,color=COLORS[index],alpha=.7)
                        axis.plot([index-.15,index+.15],[np.mean(values)]*2,color="black",linewidth=2)
                axis.set_xticks(range(5),LABELS,rotation=30,ha="right")
                axis.set_title(f"{model} / {'8 slots' if budget=='slots8' else '30k tokens'}")
                axis.grid(axis="y",alpha=.2)
                if model_index==0 and budget_index==0:
                    axis.set_ylabel(("TSP exact-optimum gap" if task=="tsp" else "Bin-pack volume-bound gap")+" (%)")
    figure.suptitle("Validation-selected rule on held-out tests: five paired blocks",fontsize=14)
    figure.savefig(directory/"paired_test_loss.png")
    figure.savefig(directory/"paired_test_loss.pdf")
    plt.close(figure)

    figure,axes=plt.subplots(2,2,figsize=(11,7),layout="constrained")
    effect_names=("quality_protection_main_effect","restart_correction_main_effect","quality_by_restart_interaction")
    effect_labels=("QP main effect","RR main effect","Interaction")
    for task_index,task in enumerate(tasks):
        for budget_index,budget in enumerate(budgets):
            axis=axes[task_index,budget_index]
            for model_index,model in enumerate(models):
                for index,name in enumerate(effect_names):
                    match=next((r for r in contrasts if r["task"]==task and r["regime"]==budget
                                and r["model"]==model and r["metric"]=="validation_selected_test_loss"
                                and r["contrast"]==name and r["mean_difference"]),None)
                    if match:
                        mean=100*float(match["mean_difference"])
                        lower=100*float(match["bootstrap_95_low"])
                        upper=100*float(match["bootstrap_95_high"])
                        axis.errorbar(mean,index+(model_index-.5)*.2,
                            xerr=[[max(0,mean-lower)],[max(0,upper-mean)]],fmt="o",capsize=3,
                            color=("#0f766e","#2563eb")[model_index],
                            label=model if index==0 else None)
            axis.axvline(0,color="#94a3b8",linestyle="--")
            axis.set_yticks(range(3),effect_labels)
            axis.set_title(f"{task} / {budget}")
            axis.set_xlabel("Test loss difference (percentage points; negative favors added factor)")
            axis.grid(axis="x",alpha=.2)
            axis.legend(fontsize=8)
    figure.suptitle("Factorial effects with descriptive 95% paired-block bootstrap intervals",fontsize=13)
    figure.savefig(directory/"factorial_effects.png")
    figure.savefig(directory/"factorial_effects.pdf")
    plt.close(figure)

    figure,axes=plt.subplots(1,2,figsize=(11,4),layout="constrained")
    for axis,budget in zip(axes,budgets):
        for model_index,model in enumerate(models):
            for index,method in enumerate(METHODS):
                selected=[r for r in rows if r["task"]=="tsp" and r["model"]==model
                          and r["regime"]==budget and r["method"]==method and r.get("selector_gain_vs_single")]
                values=[100*float(r["selector_gain_vs_single"]) for r in selected]
                if values:
                    position=index+(model_index-.5)*.3
                    axis.scatter([position]*len(values),values,s=19,alpha=.6,color=("#0f766e","#2563eb")[model_index],
                                 label=model if index==0 else None)
                    axis.plot([position-.1,position+.1],[np.mean(values)]*2,color="black",linewidth=2)
        axis.axhline(0,color="#94a3b8",linestyle="--")
        axis.set_xticks(range(5),LABELS)
        axis.set_title("TSP / "+budget)
        axis.set_ylabel("Single rule loss - learned selector loss (pp)")
        axis.legend()
        axis.grid(axis="y",alpha=.2)
    figure.suptitle("Deployable TSP selection: positive gain favors the validation-fitted selector",fontsize=13)
    figure.savefig(directory/"selector_utility.png")
    figure.savefig(directory/"selector_utility.pdf")
    plt.close(figure)
    print(f"Wrote three PNG/PDF figure pairs to {directory}")


if __name__=="__main__":
    main()
