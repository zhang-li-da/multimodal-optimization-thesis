"""Fresh fixed splits, reusing the pilot's feature contract and execution rules."""
from __future__ import annotations

import hashlib
import json
import statistics
import time

import numpy as np
from sklearn.datasets import load_iris, load_wine, load_breast_cancer
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from chapter6_demo import benchmarks as base
from chapter6_demo.classification import _execute as classify
from chapter6_demo.programs import Program, ProgramError

VERSION = "prospective-splits-v1"


def classification_data(partition):
    """Same fit/probe/validation/test fractions; a new stratified split per block."""
    datasets = []
    shift = partition * 104729
    for di, (name, loader) in enumerate((("iris", load_iris), ("wine", load_wine),
                                        ("breast_cancer", load_breast_cancer))):
        data = loader()
        X, y = np.asarray(data.data, dtype=float), np.asarray(data.target, dtype=int)
        ids = np.arange(len(y))
        fit, rest = train_test_split(ids, test_size=.4, stratify=y, random_state=6217 + di + shift)
        probe, hold = train_test_split(rest, test_size=.75, stratify=y[rest], random_state=8331 + di + shift)
        val, test = train_test_split(hold, test_size=.5, stratify=y[hold], random_state=9559 + di + shift)
        splits = {"fit": fit, "probe": probe, "validation": val, "test": test}
        scaler = StandardScaler().fit(X[fit])
        scaled = scaler.transform(X)
        train, labels = scaled[fit], y[fit]
        classes = np.unique(labels)
        means = {int(c): train[labels == c].mean(axis=0) for c in classes}
        medians = {int(c): np.median(train[labels == c], axis=0) for c in classes}
        variances = {int(c): np.maximum(train[labels == c].var(axis=0), .05) for c in classes}
        priors = {int(c): float(np.mean(labels == c)) for c in classes}
        cases = {}
        for split, index in splits.items():
            if split == "fit":
                continue
            features = []
            for x in scaled[index]:
                squared = ((train - x) ** 2).mean(axis=1)
                order = np.argsort(squared, kind="stable")[:7]
                per_class = []
                for c in classes:
                    c = int(c)
                    mean, variance = means[c], variances[c]
                    diff = (x - mean) ** 2
                    per_class.append({
                        "centroid": float(diff.mean()),
                        "diagonal_nll": float((diff / variance + np.log(variance)).mean()),
                        "nearest": float(squared[labels == c].min()),
                        "knn_fraction": float(np.mean(labels[order] == c)),
                        "cosine": float(np.dot(x, mean) / max(1e-9, np.linalg.norm(x) * np.linalg.norm(mean))),
                        "prior": priors[c],
                        "robust_distance": float(np.abs(x - medians[c]).mean()),
                        "class_spread": float(variance.mean()),
                    })
                features.append(per_class)
            cases[split] = {"id": f"{name}-p{partition}-{split}", "family": name,
                            "features": features, "labels": y[index].tolist(),
                            "sample_ids": index.tolist(), "classes": classes.tolist()}
        datasets.append({"name": name, "cases": cases,
                         "split_ids": {k: v.tolist() for k, v in splits.items()}})
    return datasets


class Benchmark:
    def __init__(self, task, partition):
        if task not in base.TAGS or partition < 1:
            raise ValueError("Unsupported task or partition.")
        self.task, self.partition = task, partition
        self.data = {}
        self.classification = None
        if task == "classification":
            self.classification = classification_data(partition)
            for split in ("probe", "validation", "test"):
                self.data[split] = tuple(d["cases"][split] for d in self.classification)
        else:
            families = ("uniform", "clustered", "grid") if task == "tsp" else ("uniform", "bimodal", "complementary")
            for si, split in enumerate(("probe", "validation", "test")):
                # Search evaluation matches the pilot size; final test doubles
                # the number of independently generated instances per family.
                count = {"probe": 2, "validation": 4, "test": 16}[split]
                size = (10 if split == "probe" else 12) if task == "tsp" else (32 if split == "probe" else 64)
                self.data[split] = tuple(
                    base._make_instance(task, family, 1000000 + partition * 10000 + si * 1000 + fi * 100 + j, size)
                    for fi, family in enumerate(families) for j in range(count)
                )

    def fingerprint(self, split):
        return hashlib.sha256(json.dumps(self.data[split], sort_keys=True).encode()).hexdigest()

    def evaluate(self, code, split="validation", with_probes=True):
        start, cpu = time.perf_counter(), time.process_time()
        program, checks, attempted = None, 0, 0
        try:
            program = Program(code, self.task)
            outputs, probes = [], []
            for current_split, target in [(split, outputs)] + ([("probe", probes)] if with_probes else []):
                for instance in self.data[current_split]:
                    attempted += 1
                    if self.task == "classification":
                        value = classify(program, instance)
                        value["solution"] = value["predictions"]
                        value["value"] = 1 - value["loss"]
                        value["local_checks"] = 0
                    else:
                        value = base.execute(program, instance)
                    checks += value["local_checks"]
                    target.append(value)
            groups = {}
            for instance, out in zip(self.data[split], outputs):
                groups.setdefault(instance["family"], []).append(out["loss"])
            result = {"valid": True, "loss": statistics.fmean(x["loss"] for x in outputs),
                      "per_instance_loss": [x["loss"] for x in outputs],
                      "per_instance_value": [x["value"] for x in outputs],
                      "family_loss": {k: statistics.fmean(v) for k, v in groups.items()},
                      "behavior": [x["behavior"] for x in (probes or outputs)],
                      "trajectory_behavior": [x["trajectory_behavior"] for x in (probes or outputs)],
                      "trajectory_values": [x["trajectory_values"] for x in (probes or outputs)],
                      "solutions": [x["solution"] for x in outputs], "failure_type": None,
                      "program_hash": program.hash, "ast_nodes": program.ast_nodes}
        except (ProgramError, ValueError, ArithmeticError, KeyError) as exc:
            result = {"valid": False, "loss": None, "behavior": [], "trajectory_behavior": [],
                      "failure_type": type(exc).__name__, "error": str(exc)[:200]}
        result.update(split=split, features_called=program.calls if program else 0,
                      local_checks=checks, instance_evaluations=attempted,
                      wall_seconds=time.perf_counter() - start, cpu_seconds=time.process_time() - cpu)
        return result
