"""Stable errors crossing the code-understanding model boundary."""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias


ModelErrorType: TypeAlias = Literal[
    "model_execution_error",
    "invalid_model_output",
]


class ModelBoundaryError(RuntimeError):
    """Base class whose message and classification never expose provider data."""

    error_type: ClassVar[ModelErrorType]
    stable_message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.stable_message)


class ModelExecutionError(ModelBoundaryError):
    """The model provider could not complete the request."""

    error_type = "model_execution_error"
    stable_message = "model execution failed"


class InvalidModelOutputError(ModelBoundaryError):
    """The provider returned no valid, allowlisted model decision."""

    error_type = "invalid_model_output"
    stable_message = "invalid model output"
