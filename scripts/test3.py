import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from app.rag.retriever import retrieve
from app.core.config import settings
import anthropic

async def main():
    sp = Path("data/system_prompt.txt").read_text(encoding="utf-8")
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    
    for query in ["3 PzS 375 24V narxi?", "24 volt 375 amper narxi qancha"]:
        ctx = await retrieve(query, top_k=3)
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": f"[Baza]\n{ctx}\n[/Baza]", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": query}
        ]}]
        resp = await client.messages.create(model="claude-sonnet-4-6", max_tokens=200,
            system=[{"type": "text", "text": sp, "cache_control": {"type": "ephemeral"}}],
            messages=msgs)
        with open("data/test3.txt", "a", encoding="utf-8") as f:
            f.write(f"Q: {query}\nA: {resp.content[0].text}\n\n")

asyncio.run(main())
