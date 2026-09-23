# 常规分类器参照

Contextual standard classifiers, not token-matched LLM-search controls; all preprocessing and fitting use only the same fit split. Hyperparameters are fixed and no additional tuning is performed.

使用和追加验证完全相同的 10 个 fit/validation/test 划分。每次采用同一个算法处理三个数据集，测试误差先按数据集等权平均，再按划分平均。

| 固定分类器 | 平均验证错误率 | 平均测试错误率 |
|---|---:|---:|
| nearest_centroid | 9.28% | 7.75% |
| knn7 | 5.72% | 4.40% |
| gaussian_nb | 6.09% | 4.87% |
| logistic_C1 | 3.89% | 3.51% |
| svc_rbf_C1 | 3.70% | 3.55% |
| random_forest_200 | 5.45% | 3.92% |

每个划分按平均验证损失从六个算法选一，最终平均测试错误率：3.41%。

这组结果用于判断受限评分规则的任务水平；它没有统一候选语言、训练成本与 API 成本，不能取代 niche/relational 的机制比较，也不是全量 AutoML 排名。
