class MatchwellError(Exception):
    """Base class for safe, user-facing application errors."""


class AuthenticationError(MatchwellError):
    pass


class AccountDisabledError(AuthenticationError):
    """Raised when a resolved account's status is disabled.

    Deliberately a subtype of ``AuthenticationError`` so existing callers
    that already catch ``MatchwellError`` (or ``AuthenticationError``) render
    a safe message without any structural change.
    """


class AuthorizationError(MatchwellError):
    pass


class ConflictError(MatchwellError):
    pass


class NotFoundError(MatchwellError):
    pass


class ValidationError(MatchwellError):
    pass
