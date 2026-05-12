"""
Orquestador del chunking sobre el piloto de 100 documentos.

Lee la carpeta data/samples/pilot/ (poblada por select_pilot.py),
corre el chunker sobre cada documento, y genera:

  - data/samples/pilot/_chunks.json   : todos los chunks generados
  - data/samples/pilot/_stats.json    : estadisticas agregadas
  - data/samples/pilot/_errors.log    : errores y warnings durante la corrida

El JSON de chunks es el insumo de la siguiente fase (embeddings).
"""
import json
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from time import time

# Asegurar que podamos importar desde src/
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chunker import chunk_document


PILOT_DIR = PROJECT_ROOT / "data" / "samples" / "pilot"
MANIFEST_PATH = PILOT_DIR / "_manifest.json"
CHUNKS_PATH = PILOT_DIR / "_chunks.json"
STATS_PATH = PILOT_DIR / "_stats.json"
ERRORS_PATH = PILOT_DIR / "_errors.log"


def cargar_manifest():
    """Carga el manifest generado por select_pilot.py."""
    if not MANIFEST_PATH.exists():
        print(f"ERROR: no se encuentra {MANIFEST_PATH}")
        print("       Corre primero: python scripts\\select_pilot.py")
        sys.exit(1)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def chunkear_doc(doc_info, errores):
    """
    Chunkea un documento del piloto. Devuelve (chunks, info_extra).
    info_extra contiene metricas por documento.
    Si falla, registra en errores y devuelve ([], dict_con_error).
    """
    nombre = doc_info["nombre"]
    path = doc_info.get("path_destino") or str(PILOT_DIR / nombre)

    info_extra = {
        "nombre": nombre,
        "tipo_esperado": doc_info["tipo"],
        "tamano_clase": doc_info["tamano_clase"],
        "bytes_archivo": doc_info["bytes"],
        "anio_nombre": doc_info["anio"],
    }

    try:
        chunks = chunk_document(path, verbose=False)
        info_extra["status"] = "ok"
        info_extra["num_chunks"] = len(chunks)
        if chunks:
            info_extra["chars_totales"] = sum(c["metadata"]["char_count"] for c in chunks)
            info_extra["tipos_chunk"] = list(set(c["metadata"]["tipo_chunk"] for c in chunks))
            # Para validar que metadata del header se extrajo
            meta = chunks[0]["metadata"]
            info_extra["tipo_norma_parseado"] = meta.get("tipo_norma")
            info_extra["doc_id"] = meta.get("doc_id")
        else:
            info_extra["num_chunks"] = 0
            info_extra["chars_totales"] = 0
            info_extra["tipos_chunk"] = []
        return chunks, info_extra

    except Exception as e:
        msg = f"{nombre}: {type(e).__name__}: {e}"
        errores.append(msg)
        errores.append(traceback.format_exc())
        info_extra["status"] = "error"
        info_extra["error"] = str(e)
        return [], info_extra


def main():
    print("=" * 70)
    print("CHUNKING DEL PILOTO")
    print("=" * 70)

    manifest = cargar_manifest()
    docs = manifest["documentos"]
    total = len(docs)

    print(f"\nDocumentos a procesar: {total}")
    print(f"Corpus piloto: {PILOT_DIR}")
    print()

    all_chunks = []
    docs_info = []
    errores = []
    t0 = time()

    for i, doc in enumerate(docs, start=1):
        chunks, info = chunkear_doc(doc, errores)
        all_chunks.extend(chunks)
        docs_info.append(info)

        # Progress cada 20 docs o al final
        if i % 20 == 0 or i == total:
            elapsed = time() - t0
            print(f"  Procesados {i}/{total} ({elapsed:.1f}s) "
                  f"-- chunks acumulados: {len(all_chunks)}")

    elapsed = time() - t0

    # Calcular estadisticas
    stats = {
        "total_docs_procesados": total,
        "total_docs_ok": sum(1 for d in docs_info if d["status"] == "ok"),
        "total_docs_error": sum(1 for d in docs_info if d["status"] == "error"),
        "total_chunks_generados": len(all_chunks),
        "tiempo_segundos": round(elapsed, 2),
        "chunks_por_doc_promedio": round(len(all_chunks) / total, 2) if total else 0,
    }

    # Distribucion por tipo_chunk
    por_tipo_chunk = defaultdict(int)
    chars_por_tipo_chunk = defaultdict(int)
    for c in all_chunks:
        t = c["metadata"]["tipo_chunk"]
        por_tipo_chunk[t] += 1
        chars_por_tipo_chunk[t] += c["metadata"]["char_count"]
    stats["distribucion_tipos_chunk"] = dict(por_tipo_chunk)
    stats["chars_por_tipo_chunk"] = dict(chars_por_tipo_chunk)

    # Distribucion de chunks por documento (para detectar outliers)
    chunks_por_doc = sorted(
        [(d["nombre"], d.get("num_chunks", 0)) for d in docs_info if d["status"] == "ok"],
        key=lambda x: x[1],
        reverse=True,
    )
    stats["top_10_docs_mas_chunks"] = chunks_por_doc[:10]
    stats["docs_sin_chunks"] = [d["nombre"] for d in docs_info
                                 if d["status"] == "ok" and d.get("num_chunks", 0) == 0]

    # Docs donde el tipo_norma no se parseo (indicador de problemas)
    docs_sin_tipo = [d["nombre"] for d in docs_info
                     if d["status"] == "ok" and not d.get("tipo_norma_parseado")]
    stats["docs_sin_tipo_norma_parseado"] = docs_sin_tipo

    # Docs por tipo_norma parseado
    por_tipo_norma = defaultdict(int)
    for d in docs_info:
        if d["status"] == "ok":
            t = d.get("tipo_norma_parseado") or "<sin_parsear>"
            por_tipo_norma[t] += 1
    stats["distribucion_tipo_norma_parseado"] = dict(por_tipo_norma)

    # Guardar chunks
    print(f"\nGuardando chunks en {CHUNKS_PATH}...")
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)
    print(f"  {CHUNKS_PATH.stat().st_size / 1024:.1f} KB escritos.")

    # Guardar stats
    print(f"Guardando stats en {STATS_PATH}...")
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "docs_info": docs_info}, f, indent=2, ensure_ascii=False)
    print(f"  {STATS_PATH.stat().st_size / 1024:.1f} KB escritos.")

    # Guardar errores
    if errores:
        print(f"\nGuardando log de errores en {ERRORS_PATH}...")
        with open(ERRORS_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(errores))

    # Imprimir resumen
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"Tiempo total:           {elapsed:.1f}s")
    print(f"Docs procesados OK:     {stats['total_docs_ok']}/{total}")
    print(f"Docs con error:         {stats['total_docs_error']}")
    print(f"Chunks generados:       {stats['total_chunks_generados']}")
    print(f"Chunks por doc (avg):   {stats['chunks_por_doc_promedio']}")

    print(f"\nDistribucion por tipo_chunk:")
    for t, n in sorted(por_tipo_chunk.items(), key=lambda x: -x[1]):
        chars = chars_por_tipo_chunk[t]
        print(f"  {t:25} : {n:5} chunks  |  {chars:9,} chars")

    print(f"\nDistribucion por tipo_norma (parseado por header):")
    for t, n in sorted(por_tipo_norma.items(), key=lambda x: -x[1]):
        print(f"  {t:25} : {n}")

    if stats["docs_sin_tipo_norma_parseado"]:
        print(f"\nADVERTENCIA: {len(stats['docs_sin_tipo_norma_parseado'])} doc(s) sin tipo_norma parseado:")
        for d in stats["docs_sin_tipo_norma_parseado"][:10]:
            print(f"  - {d}")

    if stats["docs_sin_chunks"]:
        print(f"\nADVERTENCIA: {len(stats['docs_sin_chunks'])} doc(s) generaron 0 chunks:")
        for d in stats["docs_sin_chunks"][:10]:
            print(f"  - {d}")

    print(f"\nTop 5 docs con mas chunks (posibles anexos gigantes):")
    for nombre, n in chunks_por_doc[:5]:
        print(f"  {n:4} chunks : {nombre}")

    if errores:
        print(f"\nERRORES: {len(errores)//2} (ver {ERRORS_PATH})")

    print()


if __name__ == "__main__":
    main()
