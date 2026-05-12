"""
Auditoria liviana sobre el corpus completo del Digesto Municipal.

Corre el chunker sobre los ~8.113 documentos en E:\\Bosio\\Normativa Total\\
Por Tipo Normativa\\ y genera SOLO estadisticas agregadas, sin guardar los
chunks completos.

Objetivo: validar que la pipeline funciona sobre el 100% del corpus antes de
gastar plata en embeddings. Tiempo esperado: 5-10 minutos. Costo: cero.

Salidas:
  data/_audit/_audit_stats.json        : estadisticas agregadas
  data/_audit/_audit_errors.log        : errores con traceback (si hay)
  data/_audit/_audit_derogations.json  : indice completo de derogaciones del corpus
"""
import json
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from time import time

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chunker import chunk_document
from derogation_detector import detectar_derogaciones_totales


CORPUS_DIR = Path(r"E:\Bosio\Normativa Total\Por Tipo Normativa")
OUTPUT_DIR = PROJECT_ROOT / "data" / "_audit"
STATS_PATH = OUTPUT_DIR / "_audit_stats.json"
ERRORS_PATH = OUTPUT_DIR / "_audit_errors.log"
DEROGATIONS_PATH = OUTPUT_DIR / "_audit_derogations.json"


def clasificar_epoca(anio):
    if anio is None:
        return "sin_anio"
    if anio < 2000:
        return "viejo"
    if anio < 2015:
        return "medio_temporal"
    return "reciente"


def main():
    print("=" * 70)
    print("AUDITORIA DEL CORPUS COMPLETO")
    print("=" * 70)

    if not CORPUS_DIR.exists():
        print(f"ERROR: no se encuentra {CORPUS_DIR}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Recolectar archivos
    print(f"\nRecolectando archivos de {CORPUS_DIR}...")
    archivos_por_tipo = defaultdict(list)
    for subdir in CORPUS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        tipo_carpeta = subdir.name
        for archivo in subdir.rglob("*.txt"):
            archivos_por_tipo[tipo_carpeta].append(archivo)

    total = sum(len(v) for v in archivos_por_tipo.values())
    print(f"  Total: {total} archivos")
    for tipo, lista in archivos_por_tipo.items():
        print(f"  {tipo:15} : {len(lista)}")

    # Estructuras de stats
    stats = {
        "total_archivos": total,
        "docs_ok": 0,
        "docs_con_error": 0,
        "docs_sin_chunks": 0,
        "docs_sin_tipo_norma_parseado": 0,
        "total_chunks": 0,
        "total_chars_chunkeados": 0,
        "tipos_chunk": defaultdict(int),
        "chars_por_tipo_chunk": defaultdict(int),
        "tipo_norma_parseado": defaultdict(int),
        "por_epoca": defaultdict(int),
        "por_camino": defaultdict(int),
        "chunks_por_doc_distribucion": defaultdict(int),
        "top_docs_mas_chunks": [],
        "docs_anomalia": [],
    }

    errores_detallados = []
    todas_las_derogaciones = []

    # Iterar y procesar
    print(f"\nProcesando documentos...")
    t0 = time()
    procesados = 0

    for tipo_carpeta, archivos in archivos_por_tipo.items():
        for archivo in archivos:
            procesados += 1

            try:
                chunks = chunk_document(archivo, verbose=False)

                if not chunks:
                    stats["docs_sin_chunks"] += 1
                    stats["docs_anomalia"].append({
                        "archivo": str(archivo.name),
                        "tipo_carpeta": tipo_carpeta,
                        "anomalia": "cero_chunks",
                    })
                    continue

                stats["docs_ok"] += 1
                stats["total_chunks"] += len(chunks)

                # Determinar el "camino" predominante del doc
                tipos_en_doc = set(c["metadata"]["tipo_chunk"] for c in chunks)
                if "doc_completo" in tipos_en_doc:
                    camino = "camino_1_corto"
                elif tipos_en_doc & {"articulo", "articulo_largo_parte", "anexo", "anexo_parte"}:
                    camino = "camino_2_articulos_o_anexos"
                elif "fallback_caracteres" in tipos_en_doc:
                    camino = "camino_3_fallback"
                else:
                    camino = "indefinido"
                stats["por_camino"][camino] += 1

                # Distribucion de tipos
                for c in chunks:
                    t = c["metadata"]["tipo_chunk"]
                    stats["tipos_chunk"][t] += 1
                    stats["chars_por_tipo_chunk"][t] += c["metadata"]["char_count"]
                    stats["total_chars_chunkeados"] += c["metadata"]["char_count"]

                # Metadata del primer chunk (representa el header del doc)
                meta = chunks[0]["metadata"]
                tipo_norma = meta.get("tipo_norma")
                anio = meta.get("anio")

                if tipo_norma:
                    stats["tipo_norma_parseado"][tipo_norma] += 1
                else:
                    stats["docs_sin_tipo_norma_parseado"] += 1
                    stats["docs_anomalia"].append({
                        "archivo": str(archivo.name),
                        "tipo_carpeta": tipo_carpeta,
                        "anomalia": "sin_tipo_norma_parseado",
                    })

                stats["por_epoca"][clasificar_epoca(anio)] += 1

                # Track top docs con mas chunks
                stats["top_docs_mas_chunks"].append((archivo.name, len(chunks)))

                # Distribucion de # chunks por doc (en buckets)
                n = len(chunks)
                if n == 1:
                    bucket = "1"
                elif n <= 5:
                    bucket = "2-5"
                elif n <= 20:
                    bucket = "6-20"
                elif n <= 100:
                    bucket = "21-100"
                else:
                    bucket = "100+"
                stats["chunks_por_doc_distribucion"][bucket] += 1

                # Detectar derogaciones sobre los chunks de este doc
                eventos = detectar_derogaciones_totales(chunks)
                todas_las_derogaciones.extend(eventos)

            except Exception as e:
                stats["docs_con_error"] += 1
                tb = traceback.format_exc()
                errores_detallados.append(
                    f"=== {archivo.name} ===\n"
                    f"Tipo: {tipo_carpeta}\n"
                    f"Error: {type(e).__name__}: {e}\n"
                    f"{tb}\n"
                )

            # Progress
            if procesados % 500 == 0 or procesados == total:
                elapsed = time() - t0
                rate = procesados / elapsed if elapsed > 0 else 0
                print(f"  {procesados:5}/{total} ({elapsed:5.1f}s, {rate:.0f} docs/s) "
                      f"- chunks: {stats['total_chunks']:6} - errores: {stats['docs_con_error']}")

    elapsed = time() - t0

    # Top 10 docs con mas chunks
    stats["top_docs_mas_chunks"].sort(key=lambda x: -x[1])
    stats["top_docs_mas_chunks"] = stats["top_docs_mas_chunks"][:10]

    # Limitar lista de anomalias (puede ser larga)
    if len(stats["docs_anomalia"]) > 50:
        stats["docs_anomalia_truncado"] = True
        stats["docs_anomalia_total"] = len(stats["docs_anomalia"])
        stats["docs_anomalia"] = stats["docs_anomalia"][:50]

    # Convertir defaultdicts a dicts (para serializar JSON)
    for k in ["tipos_chunk", "chars_por_tipo_chunk", "tipo_norma_parseado",
              "por_epoca", "por_camino", "chunks_por_doc_distribucion"]:
        stats[k] = dict(stats[k])

    stats["tiempo_segundos"] = round(elapsed, 2)

    # Stats de derogaciones
    docs_derogatorios = set(e["doc_id_derogatorio"] for e in todas_las_derogaciones)
    docs_derogados = set(e["doc_id_derogado"] for e in todas_las_derogaciones)
    stats["derogaciones"] = {
        "total_eventos": len(todas_las_derogaciones),
        "docs_derogatorios_unicos": len(docs_derogatorios),
        "docs_derogados_unicos": len(docs_derogados),
    }

    # Guardar
    print(f"\nGuardando stats en {STATS_PATH}...")
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(f"Guardando derogaciones en {DEROGATIONS_PATH}...")
    salida_derog = {
        "total_eventos": len(todas_las_derogaciones),
        "docs_derogatorios": sorted(docs_derogatorios),
        "docs_derogados": sorted(docs_derogados),
        "eventos": todas_las_derogaciones,
    }
    with open(DEROGATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(salida_derog, f, indent=2, ensure_ascii=False)

    if errores_detallados:
        print(f"Guardando errores en {ERRORS_PATH}...")
        with open(ERRORS_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(errores_detallados))

    # Resumen humano
    print("\n" + "=" * 70)
    print("RESUMEN DE LA AUDITORIA")
    print("=" * 70)
    print(f"Tiempo total:               {elapsed:.1f}s")
    print(f"Docs procesados OK:         {stats['docs_ok']}/{total}")
    print(f"Docs con error:             {stats['docs_con_error']}")
    print(f"Docs sin chunks:            {stats['docs_sin_chunks']}")
    print(f"Docs sin tipo_norma:        {stats['docs_sin_tipo_norma_parseado']}")
    print(f"Total chunks generados:     {stats['total_chunks']:,}")
    print(f"Total chars chunkeados:     {stats['total_chars_chunkeados']:,}")

    print(f"\nDistribucion por camino del chunker:")
    for camino, n in sorted(stats["por_camino"].items(), key=lambda x: -x[1]):
        pct = n / stats["docs_ok"] * 100 if stats["docs_ok"] else 0
        print(f"  {camino:30}: {n:5} ({pct:5.1f}%)")

    print(f"\nDistribucion por tipo_norma:")
    for tipo, n in sorted(stats["tipo_norma_parseado"].items(), key=lambda x: -x[1]):
        print(f"  {tipo:20}: {n:5}")

    print(f"\nDistribucion por epoca:")
    for ep, n in sorted(stats["por_epoca"].items(), key=lambda x: -x[1]):
        print(f"  {ep:20}: {n:5}")

    print(f"\nDistribucion de chunks por documento:")
    for bucket in ["1", "2-5", "6-20", "21-100", "100+"]:
        n = stats["chunks_por_doc_distribucion"].get(bucket, 0)
        print(f"  {bucket:8}: {n:5} docs")

    print(f"\nTipos de chunk generados:")
    for t, n in sorted(stats["tipos_chunk"].items(), key=lambda x: -x[1]):
        chars = stats["chars_por_tipo_chunk"][t]
        print(f"  {t:25}: {n:6} chunks - {chars:>11,} chars")

    print(f"\nDerogaciones en el corpus completo:")
    print(f"  Total eventos detectados:        {stats['derogaciones']['total_eventos']}")
    print(f"  Normas que derogan otras:        {stats['derogaciones']['docs_derogatorios_unicos']}")
    print(f"  Normas derogadas:                {stats['derogaciones']['docs_derogados_unicos']}")

    print(f"\nTop 10 docs con mas chunks:")
    for nombre, n in stats["top_docs_mas_chunks"]:
        print(f"  {n:5} chunks: {nombre}")

    if stats["docs_con_error"]:
        print(f"\nADVERTENCIA: {stats['docs_con_error']} docs con error - ver {ERRORS_PATH}")
    if stats["docs_sin_chunks"]:
        print(f"ADVERTENCIA: {stats['docs_sin_chunks']} docs generaron 0 chunks")
    if stats["docs_sin_tipo_norma_parseado"]:
        print(f"ADVERTENCIA: {stats['docs_sin_tipo_norma_parseado']} docs sin tipo_norma parseado")

    print()


if __name__ == "__main__":
    main()
