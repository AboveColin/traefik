"""Errors raised by the Traefik client."""

from __future__ import annotations

__all__ = [
    "TraefikAuthenticationError",
    "TraefikConnectionError",
    "TraefikError",
    "TraefikNotFoundError",
    "TraefikResponseError",
]


class TraefikError(Exception):
    """Base class for every error this library raises."""


class TraefikConnectionError(TraefikError):
    """The instance could not be reached at all."""


class TraefikAuthenticationError(TraefikError):
    """The instance refused the request.

    Traefik's API has no authentication of its own; it is normally protected by
    an ipAllowList or a basicAuth middleware. Both answer 401 or 403, and the
    fix in either case is a change the user has to make on the Traefik side.
    """


class TraefikNotFoundError(TraefikError):
    """The endpoint does not exist on this instance."""


class TraefikResponseError(TraefikError):
    """The instance answered with something unusable."""
