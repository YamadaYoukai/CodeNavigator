"""Provider-independent trace wrapper for any ``ModelClient``."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import cast
from uuid import uuid4

from .context import ModelInput
from .events import ModelRequest, ModelResult
from .model_boundary import (
    FinalAnswerDecision,
    ModelClient,
    ModelDecision,
    ToolCallDecision,
)
from .model_errors import (
    InvalidModelOutputError,
    ModelErrorType,
    ModelExecutionError,
)
from .trace import TraceRecorder


class TracedModelClient:
    """Record one correlated request/result pair around any model client."""

    def __init__(
        self,
        *,
        client: ModelClient,
        model: str,
        trace: TraceRecorder,
        request_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(trace, TraceRecorder):
            raise TypeError("trace must be a TraceRecorder")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._client = client
        self._model = model
        self._trace = trace
        self._request_id_factory = request_id_factory or (lambda: str(uuid4()))
        self._clock = clock or perf_counter

    def decide(self, model_input: ModelInput) -> ModelDecision:
        if not isinstance(model_input, ModelInput):
            raise TypeError("model_input must be a ModelInput")

        request_id = self._request_id_factory()
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id_factory must return a non-empty string")

        self._trace.append(
            ModelRequest(
                request_id=request_id,
                model=self._model,
                model_input=model_input.to_payload(),
            )
        )

        started_at = self._clock()
        try:
            decision = self._client.decide(model_input)
        except InvalidModelOutputError:
            self._record_error(
                request_id,
                "invalid_model_output",
                self._measure_elapsed_ms(started_at),
            )
            raise InvalidModelOutputError() from None
        except ModelExecutionError:
            self._record_error(
                request_id,
                "model_execution_error",
                self._measure_elapsed_ms(started_at),
            )
            raise ModelExecutionError() from None
        except Exception:
            self._record_error(
                request_id,
                "model_execution_error",
                self._measure_elapsed_ms(started_at),
            )
            raise ModelExecutionError() from None

        if not isinstance(decision, (ToolCallDecision, FinalAnswerDecision)):
            self._record_error(
                request_id,
                "invalid_model_output",
                self._measure_elapsed_ms(started_at),
            )
            raise InvalidModelOutputError() from None

        result = ModelResult(
            request_id=request_id,
            status="success",
            decision=decision.model_dump(mode="json"),
            elapsed_ms=self._measure_elapsed_ms(started_at),
        )
        self._trace.append(result)
        return decision.model_copy(deep=True)

    def _record_error(
        self,
        request_id: str,
        error_type: ModelErrorType,
        elapsed_ms: int,
    ) -> ModelResult:
        result = ModelResult(
            request_id=request_id,
            status="error",
            error_type=error_type,
            elapsed_ms=elapsed_ms,
        )
        return cast(ModelResult, self._trace.append(result))

    def _measure_elapsed_ms(self, started_at: float) -> int:
        """Measure only the wrapped model boundary using a monotonic clock."""

        elapsed_seconds = self._clock() - started_at
        return max(0, round(elapsed_seconds * 1000))
