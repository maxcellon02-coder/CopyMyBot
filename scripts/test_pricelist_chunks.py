import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from app.rag.embedder import embed_query
from app.rag.vector_store import QdrantStore

async def main():
    store = QdrantStore()
    qdrant = store._client_()
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    results, _ = await qdrant.scroll(
        "knowledge_base",
        scroll_filter=Filter(must=[FieldCondition(key="doc_type", match=MatchValue(value="pricelist"))]),
        limit=10,
        with_payload=True
    )
    for r in results:
        print(f"=== {r.payload.get('document_title')} chunk {r.payload.get('chunk_index')} ===")
        print(r.payload.get('text', '')[:600])
        print()

asyncio.run(main())
