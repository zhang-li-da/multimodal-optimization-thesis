# v1.2.1 mechanism-only evidence package

This package contains only deterministic, local work performed after the v1.2
r2 review. It contains no new model responses and no new search runs.

- `REPORT_ZH.md`: Chinese technical report and current limitations.
- `r2_audit.json`, `r2_cells.csv`, `r2_runs.csv`: post-hoc replay of the 18
  published r2 runs and per-model/controller diagnostics.
- `matched_history_decisions.json`: same-history policy probe; it is not a
  counterfactual quality experiment.
- `r2_instances.json`: coordinates reconstructed from the frozen split rules;
  every split fingerprint matches the r2 manifest. This snapshot was created
  after the search, not recorded during model calls.
- `numeric_verification_windows.json`: Windows 11 / Python 3.13 local replay of
  198 validation and 132 test program evaluations. It records exact and
  tolerance-based comparison results and uses no model/network calls.
- `tests.xml`: 31 passing local tests covering v1.1/v1.2 regressions, the
  v1.2.1 controller path, and the numeric comparison contract.
- `manifest.json`: provenance, zero-call scope, source fingerprint and future
  16-run draft status.
- `SHA256SUMS.txt`: hashes for every file in this package except itself.

The package is an audit release, not evidence that the corrected relation
policy improves test quality. Any future 16-run search needs a new source
freeze, manifest, output directory and tag.
