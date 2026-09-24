# v1.2 version history

| Version | Commit | Meaning | Result status |
|---|---|---|---|
| Initial controller freeze | `2dedeb5d853e86f7c532b48d0446802c5c103f23` | First v1.2 source freeze used for `screening-20260924-r1`. | Excluded: the `niche` arm incorrectly admitted branch candidates, so it did not implement the registered no-development control. Raw artifacts and this source snapshot are preserved under `results/screening-20260924-r2/excluded-r1/`. |
| Corrected r2 freeze | `ba7386a7feafd338a204c64985185e67527b775f` | Prevents `niche` from admitting branches and adds attempted branch-depth accounting (later review found this still mixed attempts with successful depth). Its source fingerprint is recorded in the r2 manifest. | Frozen source for the only analyzed v1.2 model-search batch, r2. |
| Result package | Tag `chapter6-v1.2-screening-20260924` | Adds replay/analysis tooling, architecture and thesis updates, r2 outputs, checksums, and the excluded r1 audit bundle. The search-source fingerprint remains unchanged. | Mechanism trigger gate passed; relation-guided performance advantage not established. |

The r1 run is retained to make the correction auditable. It must not be pooled with r2, used to tune the method, or reported as a valid niche comparison.

## GitHub source references

The GitHub Git transport reset connections in this environment, so the same committed file trees were published through GitHub's Git Data API. GitHub normalized the original `+0800` commit timestamps to UTC, which changes commit IDs while preserving the exact tree objects and file contents. The manifests retain the original local source commit IDs; use the matching GitHub tags below to inspect those source snapshots.

| Manifest source commit | GitHub source tag | GitHub commit | Identical tree |
|---|---|---|---|
| `2dedeb5d853e86f7c532b48d0446802c5c103f23` | `chapter6-v1.2-initial-freeze-20260924` | `ef35c5c9bc94ead23c45583c91621032a4987ec7` | `858d322df1eb0ff48c80a323305fe97edd0d55f2` |
| `ba7386a7feafd338a204c64985185e67527b775f` | `chapter6-v1.2-r2-source-20260924` | `c0cc157cd01ebe9caa0dbbf53463d749dd866071` | `46844eeda3e31392555a39db5da84fbb9cf8a156` |

The public result tag is `chapter6-v1.2-screening-20260924`; the release branch is `experiment/v1.2-bounded-branch-development`. Both contain the same result files whose checksums are listed in the evidence package.


## v1.2.1 offline correction — 2026-09-24

Branch `experiment/v1.2.1-isolated-branch-policy`; tag `chapter6-v1.2.1-mechanism-only-20260924`. No model calls or new search results. New independent controller shares ordinary niche scheduling, excludes W from decisions, defines strict FIFO, uses real event-derived family statistics, and separates attempt/success depth. Tests cover multiple available branches and actual choose-path effects.

The original r2 source, manifest, raw ZIPs, numeric CSV/JSON and old tags are unchanged. Report prose and report-generation code include explicit errata; the updated package has a new checksum list. New post-hoc audit and numeric replay artifacts live under `v12_1/results/mechanism-only-20260924/`. The legacy r2 branch stays at its published revision; main and the new v1.2.1 branch carry this correction.

The 16-run follow-up is an unexecuted draft using fresh blocks 3–6. It requires a separate future freeze and result tag and cannot extend r2. Claims of effectiveness, independent relational information value, portfolio utility and doctoral novelty remain unestablished.
