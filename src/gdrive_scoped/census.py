"""What is actually in a corpus, measured rather than assumed.

Two questions this answers before anyone tunes anything. How big is the
subtree — which decides whether search fits in one 400-parent batch, and
therefore whether Drive's relevance order arrives untouched or interleaved by rank.
And what share of it can currently be read — which is the only honest way to
prioritise extractors, because the formats a corpus actually holds are never
the formats one would guess.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from gdrive_scoped.extractors import ExtractorRegistry
from gdrive_scoped.scope import ScopedDrive

#: Drive caps a `files.list` query at 400 parent clauses.
PARENT_BATCH_SIZE = 400


@dataclass(frozen=True)
class Census:
    """One corpus, counted."""

    folders: int
    #: Depth of the deepest folder, counting the root as 0.
    max_depth: int
    files: int
    #: MIME type to file count, most common first.
    mime_counts: dict[str, int]
    #: The subset of `mime_counts` no extractor claims.
    unreadable_mime_counts: dict[str, int]

    @property
    def readable_files(self) -> int:
        return self.files - sum(self.unreadable_mime_counts.values())

    @property
    def readable_share(self) -> float:
        """Fraction of files an extractor claims, 0.0 when the corpus is empty."""

        return self.readable_files / self.files if self.files else 0.0

    @property
    def search_batches(self) -> int:
        """How many `files.list` calls one search costs.

        More than one means Drive's relevance order cannot be compared across
        them — there is no score to merge on — so search interleaves the batches
        by rank position rather than trusting either order over the other.
        """

        return -(-self.folders // PARENT_BATCH_SIZE) if self.folders else 0


async def take_census(
    scoped_drive: ScopedDrive, extractors: ExtractorRegistry | None = None
) -> Census:
    """Count the Authorized Subtree, without reading a single document."""

    registry = extractors or ExtractorRegistry()
    folder_paths = await scoped_drive.folder_paths()
    folder_ids = tuple(folder_paths)

    mime_counts: Counter[str] = Counter()
    for offset in range(0, len(folder_ids), PARENT_BATCH_SIZE):
        batch = folder_ids[offset : offset + PARENT_BATCH_SIZE]
        for item in await scoped_drive.gateway.list_descendants(batch):
            mime_counts[item.mime_type] += 1

    ordered = dict(mime_counts.most_common())
    return Census(
        folders=len(folder_paths),
        max_depth=max((_depth(path) for path in folder_paths.values()), default=0),
        files=sum(ordered.values()),
        mime_counts=ordered,
        unreadable_mime_counts={
            mime_type: count
            for mime_type, count in ordered.items()
            if not registry.supports(mime_type)
        },
    )


def format_census(census: Census) -> str:
    """A short human report, for the CLI and for pasting into a plan."""

    lines = [
        f"folders: {census.folders} (max depth {census.max_depth})",
        f"files:   {census.files}",
        f"search:  {census.search_batches} batch(es) of up to {PARENT_BATCH_SIZE} parents",
        f"readable: {census.readable_files}/{census.files} ({census.readable_share:.0%})",
        "",
        "by MIME type:",
    ]
    for mime_type, count in census.mime_counts.items():
        mark = " " if mime_type not in census.unreadable_mime_counts else "!"
        lines.append(f"  {mark} {count:>6}  {mime_type}")
    if census.unreadable_mime_counts:
        lines += ["", "! = no extractor claims this type"]
    return "\n".join(lines)


def _depth(relative_path: str) -> int:
    return 0 if relative_path == "." else relative_path.count("/") + 1
