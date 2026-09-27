"""Live runner wrapper bound to the component-validation controller."""
from __future__ import annotations

from chapter6_demo.v12_2.calls import no_hook

from ..s3_tsp_r3 import controller as frozen_controller
from ..s3_tsp_r3 import runner as frozen_runner
from .controller import ComponentSearchState, restore_state


def run_search(job, snapshot, directory, binding, parameters, transport, *, mode="live", hook=no_hook):
    """Reuse the audited durable runner while swapping only the state machine.

    The swap is process-local.  Each study job runs in an isolated worker, and
    the original S3 source remains untouched and hash-verifiable.
    """
    old_state = frozen_runner.SearchState
    old_restore = frozen_runner.restore_state
    old_controller_state = frozen_controller.SearchState
    frozen_runner.SearchState = ComponentSearchState
    frozen_runner.restore_state = restore_state
    frozen_controller.SearchState = ComponentSearchState
    try:
        return frozen_runner.run_search(job, snapshot, directory, binding,
                                        parameters, transport, mode=mode, hook=hook)
    finally:
        frozen_runner.SearchState = old_state
        frozen_runner.restore_state = old_restore
        frozen_controller.SearchState = old_controller_state
