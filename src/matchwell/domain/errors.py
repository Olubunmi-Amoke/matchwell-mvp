class MatchwellError(Exception):
    """Base class for safe, user-facing application errors."""


class AuthenticationError(MatchwellError):
    pass


class AuthorizationError(MatchwellError):
    pass


class ConflictError(MatchwellError):
    pass


class NotFoundError(MatchwellError):
    pass


class ValidationError(MatchwellError):
    pass
