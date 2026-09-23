"""Contextual ML baselines on the same splits, separate from search comparisons."""
import json
from pathlib import Path
import statistics
import time

import numpy as np
from sklearn.base import clone
from sklearn.datasets import load_iris, load_wine, load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier, NearestCentroid
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .benchmarks import classification_data
from .protocol import PROTOCOL


def main():
    algorithms = {
        "nearest_centroid": make_pipeline(StandardScaler(), NearestCentroid()),
        "knn7": make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=7)),
        "gaussian_nb": make_pipeline(StandardScaler(), GaussianNB()),
        "logistic_C1": make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=1000)),
        "svc_rbf_C1": make_pipeline(StandardScaler(), SVC(C=1, gamma="scale")),
        "random_forest_200": RandomForestClassifier(n_estimators=200, random_state=61023, n_jobs=1),
    }
    datasets = {name: loader() for name, loader in (("iris", load_iris), ("wine", load_wine), ("breast_cancer", load_breast_cancer))}
    rows, picked = [], []
    for partition in PROTOCOL["partitions"]:
        descriptions = classification_data(partition)
        partition_rows = []
        for name, algorithm in algorithms.items():
            per_dataset = []
            start = time.perf_counter()
            for description in descriptions:
                dataset = datasets[description["name"]]
                ids = description["split_ids"]
                model = clone(algorithm).fit(dataset.data[ids["fit"]], dataset.target[ids["fit"]])
                losses = {}
                for split in ("validation", "test"):
                    prediction = model.predict(dataset.data[ids[split]])
                    losses[split] = float(np.mean(prediction != dataset.target[ids[split]]))
                per_dataset.append({"dataset": description["name"], **losses})
            row = {"partition": partition, "method": name,
                   "validation_loss": statistics.fmean(r["validation"] for r in per_dataset),
                   "test_loss": statistics.fmean(r["test"] for r in per_dataset),
                   "per_dataset": per_dataset, "seconds": time.perf_counter() - start}
            rows.append(row); partition_rows.append(row)
        # Select one shared algorithm by average validation loss, matching the
        # demo's one-rule-across-datasets selection structure. No test selection.
        best = min(partition_rows, key=lambda r: (r["validation_loss"], r["method"]))
        picked.append(best)
    summary = {name: {"validation_loss": statistics.fmean(r["validation_loss"] for r in rows if r["method"] == name),
                      "test_loss": statistics.fmean(r["test_loss"] for r in rows if r["method"] == name)} for name in algorithms}
    result = {"scope": "Contextual standard classifiers, not token-matched LLM-search controls; all preprocessing and fitting use only the same fit split. Hyperparameters are fixed and no additional tuning is performed.",
              "rows": rows, "aggregated": summary, "validation_selected": picked,
              "selected_test_mean": statistics.fmean(r["test_loss"] for r in picked)}
    output = Path("chapter6_validation/results"); output.mkdir(parents=True, exist_ok=True)
    (output / "classifier_context.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# 常规分类器参照", "", result["scope"], "",
             "使用和追加验证完全相同的 10 个 fit/validation/test 划分。每次采用同一个算法处理三个数据集，测试误差先按数据集等权平均，再按划分平均。", "",
             "| 固定分类器 | 平均验证错误率 | 平均测试错误率 |", "|---|---:|---:|"]
    for name, values in summary.items():
        lines.append(f"| {name} | {values['validation_loss']:.2%} | {values['test_loss']:.2%} |")
    lines += ["", f"每个划分按平均验证损失从六个算法选一，最终平均测试错误率：{result['selected_test_mean']:.2%}。", "",
              "这组结果用于判断受限评分规则的任务水平；它没有统一候选语言、训练成本与 API 成本，不能取代 niche/relational 的机制比较，也不是全量 AutoML 排名。"]
    (output / "classifier_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"partitions": len(picked), "model_fits": len(rows) * 3, "selected_test_mean": result["selected_test_mean"]}))


if __name__ == "__main__":
    main()
