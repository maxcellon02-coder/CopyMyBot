import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.rag.vector_store import QdrantStore

async def main():
    store = QdrantStore()
    qdrant = store._client_()
    results, _ = await qdrant.scroll(
        "knowledge_base",
        scroll_filter=Filter(must=[FieldCondition(key="doc_type", match=MatchValue(value="pricelist"))]),
        limit=10, with_payload=True
    )
    for r in results:
        title = r.payload.get("document_title","")
        idx = r.payload.get("chunk_index",0)
        text = r.payload.get("text","")
        with open(f"data/full_chunk_{idx}_{title[:25].replace(' ','_')}.txt", "w", encoding="utf-8") as f:
            f.write(f"=== {title} chunk {idx} ===\n{text}\n")

asyncio.run(main())
