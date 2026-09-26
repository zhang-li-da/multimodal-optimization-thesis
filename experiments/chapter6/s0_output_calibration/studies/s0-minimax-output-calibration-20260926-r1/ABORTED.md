# S0 r1 aborted execution

This frozen study was stopped after the first MiniMax M3 request because the
runner accessed the durable response dictionary as an object (`response.text`).
The request was sent once and its raw response, usage, request id, and finish
metadata remain under `runs/`; it was not retried. No calibration outcome was
completed for this study, and its single request is not pooled with a later
study. The reader should use the corrected r2 study for the planned calibration.
