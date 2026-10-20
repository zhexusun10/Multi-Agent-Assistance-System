from .requirements import Contract


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool = False


class RecommendationError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.detail = ErrorDetail(code=code, message=message, retryable=retryable)
