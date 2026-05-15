"""
Ingests files from a Google Drive folder (Docs, Sheets, PDFs, DOCX, XLSX).
Google Docs/Sheets are exported on-the-fly to DOCX/XLSX before parsing.
Tracks file modifiedTime in data/drive_ingest_state.json for delta ingestion.
"""
import asyncio
import io
import json
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from app.core.config import settings
from app.rag.chunk import Chunk
from app.rag.sources.parser import chunk_text, parse_document
from app.rag.vector_store import QdrantStore

STATE_FILE = Path("data/drive_ingest_state.json")

# Google Workspace types → export as Office format
_EXPORT_MAP = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
}
# Binary types we can download directly
_BINARY_MAP = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _build_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(
        settings.service_account_json,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _download_file(service, file_id: str, mime: str) -> Tuple[Optional[bytes], Optional[str]]:
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()

    if mime in _EXPORT_MAP:
        export_mime, ext = _EXPORT_MAP[mime]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    elif mime in _BINARY_MAP:
        ext = _BINARY_MAP[mime]
        request = service.files().get_media(fileId=file_id)
    else:
        return None, None

    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue(), ext


class GoogleDriveIngester:
    def __init__(self, store: QdrantStore):
        self.store = store
        self.state = _load_state()

    async def ingest_all(self) -> int:
        loop = asyncio.get_event_loop()
        service = await loop.run_in_executor(None, _build_service)

        results = await loop.run_in_executor(
            None,
            lambda: service.files()
            .list(
                q=f"'{settings.drive_folder_id}' in parents and trashed = false",
                fields="files(id, name, mimeType, modifiedTime)",
                pageSize=100,
            )
            .execute(),
        )

        total = 0
        for f in results.get("files", []):
            file_id: str = f["id"]
            name: str = f["name"]
            mime: str = f["mimeType"]
            modified: str = f["modifiedTime"]

            if self.state.get(file_id) == modified:
                continue  # unchanged since last ingest

            data, ext = await loop.run_in_executor(
                None,
                lambda fid=file_id, m=mime: _download_file(service, fid, m),
            )
            if data is None:
                logger.debug(f"[Drive] Skipping unsupported type: {name} ({mime})")
                continue

            filename = f"{name}.{ext}" if "." not in name else name
            parsed = parse_document(data, filename)
            if not parsed:
                continue

            chunks: List[Chunk] = [
                Chunk(
                    text=piece,
                    source_type="google_drive",
                    source_id=file_id,
                    document_title=name,
                    chunk_index=i,
                    metadata={"modified": modified, "mime": mime},
                )
                for i, piece in enumerate(chunk_text(parsed.text))
            ]

            n = await self.store.upsert_chunks(chunks)
            total += n
            self.state[file_id] = modified
            logger.info(f"[Drive] '{name}': {n} chunks indexed")

        _save_state(self.state)
        return total
