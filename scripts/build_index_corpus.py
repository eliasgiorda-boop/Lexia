"""
Construye el indice de embeddings para el CORPUS COMPLETO de normativa
de San Martin de los Andes.

Procesa los 8.127 documentos:
  - Para cada .txt llama a chunk_document() (que ya hace parser + filtros + chunker)
  - Enriquece con derogacion_from_filename
  - Embebe en batches con OpenAI
  - Indexa en ChromaDB en la coleccion "digesto_sma"

ESTIMACIONES:
  - 24.843 chunks
  - ~6-8 millones de tokens
  - ~U$S 0.14
  - 8-12 minutos total

IDEMPOTENTE: upsert por chunk_id, correr de nuevo no duplica nada.
TOLERANTE A ERRORES: si un doc falla, lo skip y sigue.
CHECKPOINTS: guarda a DB cada N chunks, si se corta el wifi no perdes todo.

USO:
  python scripts/build_index_corpus.py
  python scripts/build_index_corpus.py --dry-run
  python scripts/build_index_corpus.py --limit 100        # test con 100 docs
  python scripts/build_index_corpus.py --batch-size 200
"""
import argparse
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chunker import chunk_document
from derogation_from_filename import es_derogada_por_filename
from embedder import embed_texts, estimar_costo_usd, estimar_tokens, MODEL_NAME
from indexer import get_collection, upsert_chunks

CORPUS_ROOT = Path(os.getenv("CORPUS_PATH", "."))


def listar_archivos_corpus():
    """Lista todos los .txt del corpus en orden alfabetico."""
    archivos = []
    for subdir in CORPUS_ROOT.iterdir():
        if subdir.is_dir():
            archivos.extend(subdir.glob("*.txt"))
    return sorted(archivos)


def procesar_documento(filepath):
    """
    Procesa un documento usando chunk_document().
    Enriquece cada chunk con derogacion_from_filename.

    Devuelve (lista_chunks, error_o_None).
    """
    try:
        chunks = chunk_document(filepath, verbose=False)
    except Exception as e:
        return [], f"chunk_document fallo: {e}"

    if not chunks:
        return [], "cero chunks generados"

    # Enriquecer con derogacion por filename
    derogada = es_derogada_por_filename(filepath.name)
    for c in chunks:
        if "metadata" not in c:
            c["metadata"] = {}
        c["metadata"]["es_derogada_por_filename"] = bool(derogada)
        c["metadata"]["fuente_archivo"] = filepath.name

    return chunks, None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Solo procesa y estima costo, no embebe.")
    p.add_argument("--limit", type=int, default=None,
                   help="Limitar a primeros N documentos (para tests).")
    p.add_argument("--batch-size", type=int, default=200,
                   help="Tamano del batch para OpenAI (default: 200).")
    p.add_argument("--checkpoint-every", type=int, default=2000,
                   help="Indexar a ChromaDB cada N chunks acumulados (default: 2000).")
    args = p.parse_args()

    print("=" * 70)
    print("CONSTRUCCION DEL INDICE - CORPUS COMPLETO")
    print("=" * 70)
    print(f"Modelo:       {MODEL_NAME}")
    print(f"Corpus:       {CORPUS_ROOT}")
    print(f"Dry-run:      {args.dry_run}")
    print(f"Batch size:   {args.batch_size}")
    print(f"Checkpoint:   cada {args.checkpoint_every} chunks")
    if args.limit:
        print(f"LIMITADO:     primeros {args.limit} documentos")
    print()

    if not CORPUS_ROOT.exists():
        print(f"ERROR: no se encuentra {CORPUS_ROOT}")
        sys.exit(1)

    # ===== FASE 1: PROCESAMIENTO =====
    print("FASE 1/3: Procesando documentos del corpus...")
    archivos = listar_archivos_corpus()
    if args.limit:
        archivos = archivos[:args.limit]

    total_docs = len(archivos)
    print(f"  Total documentos a procesar: {total_docs:,}")

    todos_los_chunks = []
    errores = []
    total_chunks_vacios_descartados = [0]  # mutable para uso en el loop
    inicio = time.time()

    for i, archivo in enumerate(archivos, 1):
        chunks, error = procesar_documento(archivo)
        if error:
            errores.append((archivo.name, error))
            continue
        # Filtrar chunks con texto vacio (edge case del corpus, ~0.1%)
        chunks_validos = [c for c in chunks if c.get("texto", "").strip()]
        chunks_descartados_por_vacios = len(chunks) - len(chunks_validos)
        if chunks_descartados_por_vacios > 0:
            total_chunks_vacios_descartados[0] += chunks_descartados_por_vacios
        todos_los_chunks.extend(chunks_validos)

        if i % 500 == 0 or i == total_docs:
            transcurrido = time.time() - inicio
            rate = i / transcurrido if transcurrido > 0 else 0
            print(f"  {i:>5}/{total_docs}  "
                  f"({transcurrido:>5.1f}s, {rate:>5.1f} docs/s, "
                  f"chunks: {len(todos_los_chunks):>6,}, "
                  f"errores: {len(errores)})")

    print(f"\n  Documentos OK:        {total_docs - len(errores):,}/{total_docs:,}")
    print(f"  Documentos con error: {len(errores)}")
    print(f"  Chunks vacios filtrados: {total_chunks_vacios_descartados[0]}")
    print(f"  Chunks utiles (a embebir): {len(todos_los_chunks):,}")
    print(f"  Tiempo fase 1:        {time.time() - inicio:.1f}s")

    if errores:
        print(f"\n  Primeros 5 errores:")
        for fname, err in errores[:5]:
            print(f"    {fname}: {err}")

    if not todos_los_chunks:
        print("\nERROR: no se generaron chunks. Abortando.")
        sys.exit(1)

    # ===== FASE 2: ESTIMACION =====
    print(f"\nFASE 2/3: Estimacion de costos...")
    textos = [c["texto"] for c in todos_los_chunks]
    tokens_estimados = estimar_tokens(textos)
    costo_estimado = estimar_costo_usd(textos)
    print(f"  Tokens estimados: {tokens_estimados:,}")
    print(f"  Costo estimado:   U$S {costo_estimado:.4f}")

    if args.dry_run:
        print("\n[DRY-RUN] No se embebe ni indexa. Salir.")
        return

    # ===== FASE 3: EMBEDDING + INDEXING POR CHECKPOINTS =====
    print(f"\nFASE 3/3: Embedding + indexing...")
    col = get_collection()
    cuenta_inicial = col.count()
    print(f"  Coleccion:                       {col.name}")
    print(f"  Chunks ya en DB (CO + previos):  {cuenta_inicial:,}")
    print(f"  Embebiendo en batches de {args.batch_size}, "
          f"indexando cada {args.checkpoint_every}...")

    chunk_size = args.checkpoint_every
    total_chunks = len(todos_los_chunks)
    inicio_emb = time.time()

    for chk_start in range(0, total_chunks, chunk_size):
        chk_end = min(chk_start + chunk_size, total_chunks)
        bloque = todos_los_chunks[chk_start:chk_end]
        bloque_textos = [c["texto"] for c in bloque]

        print(f"\n  Checkpoint {chk_start + 1:,}-{chk_end:,} de {total_chunks:,}")
        t0 = time.time()
        embeddings = embed_texts(
            bloque_textos,
            batch_size=args.batch_size,
            verbose=False,
        )

        # Anexar embeddings
        for c, emb in zip(bloque, embeddings):
            c["embedding"] = emb

        # Indexar
        upsert_chunks(col, bloque, verbose=False)
        t1 = time.time()

        transcurrido = time.time() - inicio_emb
        eta = (transcurrido / chk_end) * (total_chunks - chk_end) if chk_end > 0 else 0
        print(f"    Embebidos en {t1 - t0:.1f}s. "
              f"Total DB ahora: {col.count():,}. "
              f"ETA: {eta:.0f}s")

    # ===== RESUMEN =====
    duracion_total = time.time() - inicio
    duracion_emb = time.time() - inicio_emb

    print("\n" + "=" * 70)
    print("RESUMEN FINAL")
    print("=" * 70)
    print(f"Documentos procesados:    {total_docs - len(errores):,}/{total_docs:,}")
    print(f"Chunks generados:         {total_chunks:,}")
    print(f"Chunks indexados (total): {col.count():,}")
    print(f"  (incluye {cuenta_inicial} previos de la CO)")
    print(f"Tiempo total:             {duracion_total:.1f}s ({duracion_total / 60:.1f} min)")
    print(f"Tiempo embedding+index:   {duracion_emb:.1f}s ({duracion_emb / 60:.1f} min)")
    print(f"\nDB persistente en: {PROJECT_ROOT / 'data' / 'chromadb'}")


if __name__ == "__main__":
    main()
