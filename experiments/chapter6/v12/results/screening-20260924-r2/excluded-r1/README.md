# Excluded v1.2 r1 batch

These 18 runs are preserved for audit and are excluded from every v1.2 performance analysis. They were launched from source commit `2dedeb5d853e86f7c532b48d0446802c5c103f23` with source fingerprint `b30f6b1aa23afd335210015aff07c1101a8a0ab759cd1d6bed50948d3ac885a6`.

The frozen controller admitted `competitive_local_improvement` candidates into the branch pool for every arm, including `niche`. This violated the registered control definition: `niche` was required to have no development queue. The correction in commit `ba7386a7feafd338a204c64985185e67527b775f` added an explicit `self.method != "niche"` admission guard. That source change also corrected development-depth accounting. The full diff is available in Git history.

The defect changes the treatment represented by the `niche` arm and invalidates causal comparison of r1; rerunning only selected arms or statistically adjusting the r1 outcomes cannot repair it. The complete corrected experiment was run as r2 and is reported separately in the parent package. These files are retained to disclose the failed batch, not as valid search evidence.

Contents are the original r1 manifest and completion status, unmodified per-run prompts/responses/usage/checkpoints/logs grouped by provider, and a source archive at the exact r1 commit. No r1 aggregate report is provided because its registered contrast is invalid.
