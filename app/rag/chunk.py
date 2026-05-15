"""Shared Chunk dataclass used by all ingesters."""
from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    source_type: str        # telegram_channel | telegram_group | google_drive
    source_id: str          # channel username, group id, or Drive file id
    document_title: str     # message subject or filename
    chunk_index: int        # position within the original document
    metadata: dict = field(default_factory=dict)
