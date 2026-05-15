"""
Run once before first launch to download the embedding model (~1 GB).
    python scripts/download_model.py
"""
from sentence_transformers import SentenceTransformer

MODEL = "paraphrase-multilingual-mpnet-base-v2"
print(f"Downloading '{MODEL}' ...")
SentenceTransformer(MODEL)
print("Done — model cached locally.")
