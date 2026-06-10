"""
scripts/build_media_cache.py — (пере)собрать кэш медиафайлов SAPO SMM.

Запуск:
    .venv\\Scripts\\python.exe scripts/build_media_cache.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media.smm_cache import build_cache  # noqa: E402


def main() -> None:
    cache = build_cache(write=True)
    models = cache.get("models", {})
    print(f"Моделей: {len(models)} | файлов: {len(cache.get('items', []))}")
    for code in sorted(models)[:40]:
        slot = models[code]
        counts = {k: len(v) for k, v in slot.items() if v}
        print(f"  {code:14} {counts}")


if __name__ == "__main__":
    main()
