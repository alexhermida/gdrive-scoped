"""Normalized structures shared by every content extractor."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextBlock:
    location: str
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    blocks: tuple[TextBlock, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)
