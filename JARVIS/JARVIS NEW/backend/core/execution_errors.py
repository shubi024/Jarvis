from enum import Enum
from typing import Optional, Dict, Any

class ErrorClassification(str, Enum):
    """
    Strict taxonomy for execution failures. 
    Allows the TaskQueue to deterministically decide whether to retry, fail, or escalate.
    """
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"  # E.g., L4 required but not granted (Never retry)
    SECURITY_FAILURE = "SECURITY_FAILURE"            # E.g., Path outside allowed workspace (Never retry)
    VALIDATION_FAILURE = "VALIDATION_FAILURE"        # E.g., Bad input parameters (Never retry)
    TRANSIENT_PROVIDER = "TRANSIENT_PROVIDER"        # E.g., OpenAI API 502 Bad Gateway (Retryable)
    TIMEOUT = "TIMEOUT"                              # E.g., Tool took too long to respond (Retryable)
    NETWORK_FAILURE = "NETWORK_FAILURE"              # E.g., Connection dropped during API call (Retryable)
    UNKNOWN_ERROR = "UNKNOWN_ERROR"                  # Unhandled edge cases (Never retry)


class ExecutionError(Exception):
    """
    Canonical exception to be raised by AgentRuntime, API Engine, and Tools.
    """
    def __init__(
        self, 
        message: str, 
        classification: ErrorClassification, 
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.classification = classification
        self.details = details or {}

    def __str__(self):
        return f"[{self.classification.value}] {self.message}"