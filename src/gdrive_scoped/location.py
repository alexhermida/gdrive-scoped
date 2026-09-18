"""The Google Drive storage location one corpus lives in."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DriveKind(StrEnum):
    """Supported Drive storage locations."""

    SHARED_DRIVE = "shared_drive"
    MY_DRIVE = "my_drive"


@dataclass(frozen=True, slots=True)
class DriveLocation:
    """One Drive location, measured from the Configured Root Folder."""

    kind: DriveKind
    shared_drive_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is DriveKind.SHARED_DRIVE and not self.shared_drive_id:
            raise ValueError("A Shared Drive location requires a Shared Drive ID")
        if self.kind is DriveKind.MY_DRIVE and self.shared_drive_id is not None:
            raise ValueError("A My Drive location cannot have a Shared Drive ID")

    @classmethod
    def of(cls, item_drive_id: str | None) -> DriveLocation:
        """The location the Drive metadata of one item places it in.

        Drive fills `driveId` for Shared Drive items and leaves it out
        everywhere else, so the field answers the question by itself: absent is
        My Drive, present names the Shared Drive that owns the item. Reading it
        off the Configured Root Folder is what lets the location be measured
        rather than configured and then asserted against the root.
        """

        if item_drive_id is None:
            return cls(DriveKind.MY_DRIVE)
        return cls(DriveKind.SHARED_DRIVE, item_drive_id)

    def contains(self, item_drive_id: str | None) -> bool:
        """Return whether Drive metadata belongs to this location."""

        if self.kind is DriveKind.MY_DRIVE:
            return item_drive_id is None
        return item_drive_id == self.shared_drive_id
