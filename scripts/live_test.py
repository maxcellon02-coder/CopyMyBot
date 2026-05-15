import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from dotenv import load_dotenv; load_dotenv()
from app.rag.retriever import retrieve
from app.core.config import settings
import anthropic

async def test(query, label):
    sp = Path("data/system_prompt.txt").read_text(encoding="utf-8")
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    ctx = await retrieve(query, top_k=3)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": f"[Baza]\n{ctx}\n[/Baza]", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": query}
    ]}]
    resp = await client.messages.create(model="claude-sonnet-4-6", max_tokens=300,
        system=[{"type": "text", "text": sp, "cache_control": {"type": "ephemeral"}}],
        messages=msgs)
    answer = resp.content[0].text.replace("[NOTIFY_MANAGER]","").strip()
    with open("data/live_test.txt","a",encoding="utf-8") as f:
        f.write(f"[{label}]\nQ: {query}\nA: {answer}\n\n")

async def main():
    open("data/live_test.txt","w").close()
    await test("5PzS575 48V narxi qancha?", "5PzS575 48V narx")
    await test("48 volt 375 amper narxi", "48V 375Ah narx")
    await test("FT48-400 texnik parametrlari", "FT48-400 texnika")
    await test("FT48-500 цена", "FT48-500 цена (rus)")

asyncio.run(main())
