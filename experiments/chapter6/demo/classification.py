"""Small AutoML rule-discovery task using strictly training-derived features.

The language model writes a class-scoring rule. It may combine centroid,
diagonal likelihood and nearest-neighbor evidence. Evaluation labels are only
used by the evaluator, never as function features. Four disjoint sample sets
are fixed before search: fit, behavior probe, validation, final test.
"""
from functools import lru_cache
import hashlib
import json
import math
import statistics
import time

import numpy as np
from sklearn.datasets import load_iris, load_wine, load_breast_cancer
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from .programs import Program, ProgramError

CLASS_FEATURES=("centroid", "diagonal_nll", "nearest", "knn_fraction", "cosine",
                "prior", "robust_distance", "class_spread")
CLASS_TAGS=["centroid", "neighbors", "likelihood", "robust", "prior", "cosine", "nonlinear", "hybrid"]
CLASS_DESCRIPTION=(
    "Generate a multiclass classification rule. For each new sample, score each class "
    "using training-derived numeric features; HIGHEST priority wins. "
    "All preprocessing/class parameters/KNN are fit on the training partition only. "
    "Feature dictionary: centroid=mean squared standardized distance to class mean; "
    "diagonal_nll=mean of squared standardized difference/class variance plus log variance; "
    "nearest=mean squared standardized distance to nearest training sample of this class; "
    "knn_fraction=fraction of 7 globally nearest training samples belonging to this class; "
    "cosine=cosine similarity to class mean in training-standardized space; "
    "prior=training class frequency; robust_distance=mean absolute standardized distance "
    "to coordinate-wise class median; class_spread=mean standardized class variance. "
    "You cannot access labels, sample indices, dataset names or future information. "
    "Learn a rule balancing accuracy across Iris/Wine/Breast-Cancer tabular datasets, "
    "not a memorized label table. Ties use class ordinal. Loss is mean error rate across datasets."
)
CLASS_SEEDS=[
    ("nearest_centroid",["centroid"],'def priority(f):\n    return -f["centroid"]\n'),
    ("seven_neighbors",["neighbors"],'def priority(f):\n    return f["knn_fraction"]\n'),
    ("diagonal_gaussian",["likelihood","prior"],'def priority(f):\n    return -f["diagonal_nll"] + 0.05 * log(f["prior"] + 1e-9)\n'),
]


@lru_cache(maxsize=1)
def prepared():
    datasets=[]
    for di,(name,loader) in enumerate((("iris",load_iris),("wine",load_wine),("breast_cancer",load_breast_cancer))):
        data=loader()
        X=np.asarray(data.data,dtype=float)
        y=np.asarray(data.target,dtype=int)
        all_ids=np.arange(len(y))
        fit_idx,rest=train_test_split(all_ids,test_size=.4,stratify=y,random_state=6217+di)
        probe_idx,hold=train_test_split(rest,test_size=.75,stratify=y[rest],random_state=8331+di)
        val_idx,test_idx=train_test_split(hold,test_size=.5,stratify=y[hold],random_state=9559+di)
        scaler=StandardScaler().fit(X[fit_idx])
        Xs=scaler.transform(X)
        train=Xs[fit_idx]
        labels=y[fit_idx]
        classes=np.unique(labels)
        means={int(c):train[labels==c].mean(axis=0) for c in classes}
        medians={int(c):np.median(train[labels==c],axis=0) for c in classes}
        variances={int(c):np.maximum(train[labels==c].var(axis=0),.05) for c in classes}
        priors={int(c):float(np.mean(labels==c)) for c in classes}
        splits={"fit":fit_idx,"probe":probe_idx,"validation":val_idx,"test":test_idx}
        cases={}
        for split,ids in splits.items():
            if split=="fit":
                continue
            feats=[]
            for x in Xs[ids]:
                squared=((train-x)**2).mean(axis=1)
                order=np.argsort(squared,kind="stable")[:7]
                sample=[]
                for c in classes:
                    c=int(c);mean=means[c];var=variances[c]
                    difference=(x-mean)**2
                    sample.append({
                        "centroid":float(difference.mean()),
                        "diagonal_nll":float((difference/var+np.log(var)).mean()),
                        "nearest":float(squared[labels==c].min()),
                        "knn_fraction":float(np.mean(labels[order]==c)),
                        "cosine":float(np.dot(x,mean)/max(1e-9,np.linalg.norm(x)*np.linalg.norm(mean))),
                        "prior":priors[c],"robust_distance":float(np.abs(x-medians[c]).mean()),
                        "class_spread":float(var.mean()),
                    })
                feats.append(sample)
            cases[split]={"id":name+"-"+split,"family":name,"features":feats,
                          "labels":y[ids].tolist(),"sample_ids":ids.tolist(),"classes":classes.tolist()}
        datasets.append({"name":name,"cases":cases,"fit_ids":fit_idx.tolist(),
                         "split_ids":{k:v.tolist() for k,v in splits.items()}})
    return tuple(datasets)


def cases(split):
    return tuple(d["cases"][split] for d in prepared())


def _execute(program,case):
    predictions=[];score_vectors=[]
    for features in case["features"]:
        scores=[program(f) for f in features]
        chosen=max(range(len(scores)),key=lambda k:(scores[k],-k))
        predictions.append(case["classes"][chosen])
        score_vectors.append(scores)
    loss=statistics.fmean(int(a!=b) for a,b in zip(predictions,case["labels"]))
    behavior=[int(pred==c) for pred in predictions for c in case["classes"]]
    # W is a prefix of the observed decisions, kept distinct from full output.
    prefix_len=max(1,len(predictions)//2)*len(case["classes"])
    return {"loss":loss,"behavior":behavior,"trajectory_behavior":behavior[:prefix_len],
            "trajectory_values":[statistics.fmean(int(a!=b) for a,b in zip(predictions[:k],case["labels"][:k]))
                                  for k in [max(1,len(predictions)//2),len(predictions)]],
            "predictions":predictions}


def evaluate_classification(code,split="validation",with_probes=True):
    start=time.perf_counter();cpu=time.process_time();program=None;count=0
    try:
        program=Program(code,"classification")
        outputs=[]
        for case in cases(split):
            count+=1;outputs.append(_execute(program,case))
        probes=[]
        if with_probes:
            for case in cases("probe"):
                count+=1;probes.append(_execute(program,case))
        result={"valid":True,"loss":statistics.fmean(x["loss"] for x in outputs),
            "per_instance_loss":[x["loss"] for x in outputs],
            "per_instance_value":[1-x["loss"] for x in outputs],
            "family_loss":{case["family"]:out["loss"] for case,out in zip(cases(split),outputs)},
            "behavior":[x["behavior"] for x in (probes or outputs)],
            "trajectory_behavior":[x["trajectory_behavior"] for x in (probes or outputs)],
            "trajectory_values":[x["trajectory_values"] for x in (probes or outputs)],
            "solutions":[x["predictions"] for x in outputs],
            "failure_type":None,"program_hash":program.hash,"ast_nodes":program.ast_nodes}
    except (ProgramError,ValueError,ArithmeticError,KeyError) as exc:
        result={"valid":False,"loss":None,"behavior":[],"trajectory_behavior":[],
                "failure_type":type(exc).__name__,"error":str(exc)[:200]}
    result.update(split=split,features_called=program.calls if program else 0,local_checks=0,
                  instance_evaluations=count,wall_seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu)
    return result
