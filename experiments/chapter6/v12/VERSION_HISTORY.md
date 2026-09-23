# v1.2 version history

| Version | Commit | Meaning | Result status |
|---|---|---|---|
| Initial controller freeze | `2dedeb5d853e86f7c532b48d0446802c5c103f23` | First v1.2 source freeze used for `screening-20260924-r1`. | Excluded: the `niche` arm incorrectly admitted branch candidates, so it did not implement the registered no-development control. Raw artifacts and this source snapshot are preserved under `results/screening-20260924-r2/excluded-r1/`. |
| Corrected r2 freeze | `ba7386a7feafd338a204c64985185e67527b775f` | Prevents `niche` from admitting branches and fixes branch-depth accounting. Its source fingerprint is recorded in the r2 manifest. | Frozen source for the only analyzed v1.2 model-search batch, r2. |
| Result package | Tag `chapter6-v1.2-screening-20260924` | Adds replay/analysis tooling, architecture and thesis updates, r2 outputs, checksums, and the excluded r1 audit bundle. The search-source fingerprint remains unchanged. | Mechanism trigger gate passed; relation-guided performance advantage not established. |

The r1 run is retained to make the correction auditable. It must not be pooled with r2, used to tune the method, or reported as a valid niche comparison.
