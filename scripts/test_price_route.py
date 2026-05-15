import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from app.rag.retriever import retrieve, _is_price_query
from app.rag.embedder import embed_query
from app.rag.vector_store import QdrantStore

async def main():
    # Check doc_type distribution
    store = QdrantStore()
    qdrant = store._client_()
    all_pts, _ = await qdrant.scroll("knowledge_base", limit=100, with_payload=True)
    doctype_counts = {}
    for pt in all_pts:
        dt = pt.payload.get("doc_type", "MISSING")
        doctype_counts[dt] = doctype_counts.get(dt, 0) + 1
    print("doc_type distribution:", doctype_counts)
    
    # Test price routing
    query = "5PzS575 48V narxi"
    print(f"\nis_price_query('{query}'): {_is_price_query(query)}")
    ctx = await retrieve(query, top_k=3)
    if ctx:
        for chunk in ctx.split("---"):
            print(chunk[:200])
            print("---")

asyncio.run(main())
