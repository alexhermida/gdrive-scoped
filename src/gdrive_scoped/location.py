"""Configured Google Drive storage location."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DriveKind(StrEnum):
    """Supported Drive storage locations."""

    SHARED_DRIVE = "shared_drive"
    MY_DRIVE = "my_drive"


@dataclass(frozen=True, slots=True)
class DriveLocation:
    """One configured Drive location for a server process."""

    kind: DriveKind
    shared_drive_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is DriveKind.SHARED_DRIVE and not self.shared_drive_id:
            raise ValueError("A Shared Drive location requires a Shared Drive ID")
        if self.kind is DriveKind.MY_DRIVE and self.shared_drive_id is not None:
            raise ValueError("A My Drive location cannot have a Shared Drive ID")

    def contains(self, item_drive_id: str | None) -> bool:
        """Return whether Drive metadata belongs to this location."""

        if self.kind is DriveKind.MY_DRIVE:
            return item_drive_id is None
        return item_drive_id == self.shared_drive_id
