"""Account lookups."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Profile:
    email: str


@dataclass
class User:
    name: str
    profile: Profile | None = None


def contact_email(user: User | None) -> str:
    """SPEC: the user's contact email, or '' when there is nothing to show.

    A user with no profile is an ordinary state: accounts created by the
    import job have no profile until the welcome flow completes.
    """
    profile = user.profile if user is not None else None
    if user is None:
        return ""
    return profile.email
