"""Errors a source raises, so the coordinator does not depend on the API client."""


class SourceError(Exception):
    """The service could not be reached or answered with an error."""


class SourceAuthError(SourceError):
    """The credentials were refused."""


class SourceBusyError(SourceError):
    """The service asked us to slow down (429) or had a server error (5xx)."""

    retry_after: float | None = None
