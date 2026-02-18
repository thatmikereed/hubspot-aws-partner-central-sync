"""
Custom exception classes for sync operations.
Provides structured error handling across all handlers.
"""


class SyncException(Exception):
    """Base exception for all sync operations"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


class HubSpotAPIException(SyncException):
    """Raised when HubSpot API calls fail"""

    pass


class PartnerCentralException(SyncException):
    """Raised when AWS Partner Central API calls fail"""

    pass


class ValidationException(SyncException):
    """Raised when data validation fails"""

    pass


class ConflictException(SyncException):
    """Raised when sync conflict is detected"""

    def __init__(self, message: str, field_name: str, local_value, remote_value):
        super().__init__(message)
        self.field_name = field_name
        self.local_value = local_value
        self.remote_value = remote_value


class ReviewStatusException(SyncException):
    """Raised when operation blocked by review status"""

    pass
