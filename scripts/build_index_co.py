"""
Construye el indice de embeddings para la Carta Organica Municipal.

Lee data/carta_organica/_chunks.json, embebe los 231 chunks con OpenAI,
y los inserta en ChromaDB (data/chromadb/) en la coleccion "digesto_sma".

Este es el script piloto de Fase D: si funciona bien con la CO,
extendemos al corpus completo.

USO:
  python scripts/build_index_co.py
  python scripts/build_index_co.py --dry-run   # solo estima costo, no embebe
"""
import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from embedder import embed_texts, estimar_tokens, estimar_costo_usd, MODEL_NAME
from indexer import get_collection, upsert_chunks

CHUNKS_PATH = PROJECT_ROOT / "data" / "carta_organica" / "_chunks.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo estima costo y muestra resumen, NO embebe ni indexa.")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Tamano del batch para la API de OpenAI (default: 100).")
    args = parser.parse_args()

    print("=" * 70)
    print("CONSTRUCCION DEL INDICE - CARTA ORGANICA MUNICIPAL")
    print("=" * 70)
    print(f"Modelo: {MODEL_NAME}")
    print(f"Source: {CHUNKS_PATH}")
    print(f"Dry-run: {args.dry_run}")
    print()

    if not CHUNKS_PATH.exists():
        print(f"ERROR: no se encuentra {CHUNKS_PATH}")
        print("       Corre primero: python scripts/parse_carta_organica.py")
        sys.exit(1)

    print("Leyendo chunks...")
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"  {len(chunks)} chunks cargados.")

    textos = [c["texto"] for c in chunks]
    tokens_estimados = estimar_tokens(textos)
    costo_estimado = estimar_costo_usd(textos)

    print(f"\nEstimaciones:")
    print(f"  Tokens estimados: {tokens_estimados:,}")
    print(f"  Costo estimado:   U$S {costo_estimado:.4f}")

    if args.dry_run:
        print("\n[DRY-RUN] No se embebe nada. Salir.")
        return

    print(f"\nEmbebiendo {len(chunks)} chunks (batches de {args.batch_size})...")
    inicio = time.time()
    embeddings = embed_texts(textos, batch_size=args.batch_size, verbose=True)
    duracion = time.time() - inicio

    # Anexar embeddings a los chunks
    for c, emb in zip(chunks, embeddings):
        c["embedding"] = emb

    # Indexar en ChromaDB
    print(f"\nIndexando en ChromaDB...")
    col = get_collection()
    insertados = upsert_chunks(col, chunks, verbose=True)

    # Resumen final
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"Chunks procesados:  {len(chunks)}")
    print(f"Embeddings creados: {len(embeddings)}")
    print(f"Indexados en DB:    {insertados}")
    print(f"Tiempo total:       {duracion:.1f}s")
    print(f"Coleccion:          {col.name}")
    print(f"Total en coleccion: {col.count()}")
    print(f"\nDB persistente en: {PROJECT_ROOT / 'data' / 'chromadb'}")


if __name__ == "__main__":
    main()
