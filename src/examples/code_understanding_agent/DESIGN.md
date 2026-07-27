# Code-understanding agent trace

This package defines a small, in-memory event boundary for the agent harness.
Events describe a session, a step, a tool call/result pair, and one final
answer. TraceRecorder owns task_id and continuous sequence assignment; callers
supply deterministic elapsed_ms values when they need them.

The JSON form is an interchange format only: it restores the concrete event
models and validates the task ID, sequence, and terminal state. This layer does
not persist traces, measure wall-clock time, retry work, represent model
messages, or know how search_code and get_file_context are implemented. Those
concerns belong to a future Tool Router and its adapters.
