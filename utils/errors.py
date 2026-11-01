"""Shared error types."""


class UserError(Exception):
    """Error caused by user input — safe to show back to the user."""


class PermissionError_(UserError):
    """Insufficient permissions for the action."""


class ValidationError(UserError):
    """Invalid user input."""
