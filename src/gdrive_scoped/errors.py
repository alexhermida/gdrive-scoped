"""Every failure this library raises deliberately.

One hierarchy, and none of it inherits from a builtin. An embedding adapter
has to decide what each failure becomes on its own wire — a `ValueError` the
model can correct, an operator-visible upstream error, a log line and a page —
and that decision differs per deployment. Inheriting `PermissionError` or
`ValueError` would pre-empt it, and worse, would let an unrelated builtin
raised somewhere in the call stack be caught by the same `except`.

So the rule is: catch `DriveError` to mean "this library refused, deliberately",
and catch a leaf to distinguish why.
"""

from __future__ import annotations


class DriveError(Exception):
    """Base for every deliberate failure raised by this library."""


class ScopeViolation(DriveError):
    """Current membership in the Authorized Subtree could not be proven.

    Deliberately not `PermissionError`. A refusal here means "outside the
    corpus", which for a model is a fact about *where it looked*, not an
    access failure to retry with different credentials.
    """


class DriveNotFound(DriveError):
    """Drive has no item with this ID, or the identity cannot see it.

    The two are indistinguishable from outside and must stay that way: saying
    which would confirm the existence of items outside the corpus.
    """


class UnsupportedContentType(DriveError):
    """Metadata is available but no extractor claims this MIME type."""


class EmptyDocument(DriveError):
    """Extraction succeeded and produced no text.

    Distinct from `UnsupportedContentType`: the format was understood. A
    scanned PDF is the common case, and answering "unsupported" for one would
    be a lie that sends the caller looking for a parser that already ran.
    """


class InvalidCursor(DriveError):
    """A pagination cursor was not one this library issued."""


class CredentialsRejected(DriveError):
    """Google refused the credentials themselves.

    Every tool fails together and no retry helps, so this is the one failure
    that is an operator's problem rather than a caller's. Adapters are
    expected to log it distinctly — a dead refresh token presents as a total
    outage and should be diagnosable from the logs alone.
    """


class DriveUnavailable(DriveError):
    """Drive answered with a failure that is not about this request's scope."""

    def __init__(
        self, message: str, *, status: int | None = None, reason: str | None = None
    ) -> None:
        super().__init__(message)
        #: The HTTP status Drive returned, when there was one.
        self.status = status
        #: Drive's own machine-readable `reason`, which is more specific than
        #: the status and is what distinguishes a quota failure from an outage.
        self.reason = reason


class ExportTooLarge(DriveError):
    """The item exceeds what Drive will export, or what this library will read."""


class EnumerationBudgetExceeded(DriveError):
    """The folder enumeration did not finish: its page budget ran out, or Drive
    itself reported it incomplete (`incompleteSearch`). The message says which.

    Raised rather than truncated. A partial folder map is *safe* — every
    missing folder becomes a refusal, never a leak — but it is silently wrong,
    and a corpus that has outgrown the budget needs an operator, not a corpus
    that quietly shrinks.
    """
