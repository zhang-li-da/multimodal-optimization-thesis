"""Validation-fitted, deployment-style selection among archived heuristics."""
from __future__ import annotations

import itertools
import time

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


SELECTOR_VERSION = "instance-feature-multioutput-ridge-alpha10-v1"
ALPHA = 10.0


def instance_features(task: str, instance: dict) -> np.ndarray:
    if task == "tsp":
        points=np.asarray(instance["points"],dtype=float)
        pair=np.asarray([np.linalg.norm(points[i]-points[j]) for i,j in itertools.combinations(range(len(points)),2)])
        nearest=np.sort(pair.reshape(-1))
        per_city=np.asarray([min(np.linalg.norm(points[i]-points[j]) for j in range(len(points)) if j!=i)
                              for i in range(len(points))])
        centered=points-points.mean(axis=0)
        eig=np.linalg.eigvalsh(np.cov(centered.T))
        radial=np.linalg.norm(points-points[0],axis=1)
        q=np.quantile(pair,[.1,.25,.5,.75,.9])
        nq=np.quantile(per_city,[.1,.5,.9])
        rq=np.quantile(radial,[.1,.5,.9])
        return np.asarray([
            len(points),pair.mean(),pair.std(),pair.min(),pair.max(),*q,
            per_city.mean(),per_city.std(),*nq,
            eig[0],eig[-1],eig[0]/max(eig[-1],1e-12),
            radial.mean(),radial.std(),*rq,
        ],dtype=float)
    if task == "binpack":
        items=np.asarray(instance["items"],dtype=float)
        q=np.quantile(items,[.05,.1,.25,.5,.75,.9,.95])
        bins=np.linspace(0,1,11)
        hist=np.histogram(items,bins=bins)[0]/max(1,len(items))
        half=max(1,len(items)//2)
        first,second=items[:half],items[half:]
        adjacent=np.corrcoef(items[:-1],items[1:])[0,1] if len(items)>2 else 0.0
        if not np.isfinite(adjacent): adjacent=0.0
        return np.asarray([
            len(items),items.sum(),items.mean(),items.std(),items.std()/max(items.mean(),1e-12),
            items.min(),items.max(),*q,first.mean(),second.mean(),second.mean()-first.mean(),
            adjacent,np.mean(items>.5),np.mean(items>.66),np.mean(items<.25),*hist,
        ],dtype=float)
    raise ValueError(f"Unsupported selector task: {task}")


def fit_and_evaluate_selector(result: dict, task: str, validation_instances, test_instances) -> dict:
    """Fit from validation outcomes only; report a separately selected test rule and oracle bound."""
    start=time.perf_counter()
    nodes={n["id"]:n for n in result["nodes"]}
    ids=[int(i) for i in result["archive_ids"]
         if int(i) in nodes and str(i) in result["test"] and nodes[int(i)]["evaluation"]["valid"]]
    if not ids:
        return {"selector_version":SELECTOR_VERSION,"status":"no_quality_gated_archive","candidate_count":0}
    x_train=np.vstack([instance_features(task,x) for x in validation_instances])
    x_test=np.vstack([instance_features(task,x) for x in test_instances])
    y_train=np.column_stack([nodes[i]["evaluation"]["per_instance_loss"] for i in ids])
    y_test=np.column_stack([result["test"][str(i)]["per_instance_loss"] for i in ids])
    if y_train.shape[0]!=len(x_train) or y_test.shape[0]!=len(x_test):
        raise ValueError("Instance outcomes do not align with frozen split order.")

    model=make_pipeline(StandardScaler(),Ridge(alpha=ALPHA))
    model.fit(x_train,y_train)
    predicted=np.asarray(model.predict(x_test))
    if predicted.ndim==1:
        predicted=predicted[:,None]
    choices=np.argmin(predicted,axis=1)
    selected=np.asarray([y_test[row,col] for row,col in enumerate(choices)])
    validation_means=np.asarray([nodes[i]["evaluation"]["loss"] for i in ids])
    best_index=int(np.argmin(validation_means))
    single=float(np.mean(y_test[:,best_index]))
    selected_loss=float(np.mean(selected))
    oracle=float(np.mean(np.min(y_test,axis=1)))
    return {
        "selector_version":SELECTOR_VERSION,
        "alpha":ALPHA,
        "status":"ok",
        "candidate_ids":ids,
        "candidate_count":len(ids),
        "validation_instance_count":len(x_train),
        "test_instance_count":len(x_test),
        "validation_selected_single_id":ids[best_index],
        "validation_selected_single_test_loss":single,
        "learned_selector_test_loss":selected_loss,
        "learned_selector_gain_vs_single":single-selected_loss,
        "test_instance_oracle_loss_upper_bound_only":oracle,
        "oracle_gain_upper_bound_only":single-oracle,
        "test_selection_counts":{str(ids[j]):int(np.sum(choices==j)) for j in range(len(ids))},
        "fit_and_predict_seconds":time.perf_counter()-start,
    }
