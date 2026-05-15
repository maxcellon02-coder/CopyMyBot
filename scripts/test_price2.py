import asyncio, sys, io
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from app.rag.retriever import retrieve
from app.core.config import settings
import anthropic

async def main():
    sp = Path("data/system_prompt.txt").read_text(encoding="utf-8")
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    
    query = "5PzS575 48V narxi qancha?"
    ctx = await retrieve(query, top_k=3)
    
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": f"[Baza]\n{ctx}\n[/Baza]", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": query}
        ]
    }]
    
    resp = await client.messages.create(
        model="claude-sonnet-4-6", max_tokens=300,
        system=[{"type": "text", "text": sp, "cache_control": {"type": "ephemeral"}}],
        messages=messages
    )
    Path("data/price_test_reply.txt").write_text(resp.content[0].text, encoding="utf-8")
    print("saved to data/price_test_reply.txt")

asyncio.run(main())
