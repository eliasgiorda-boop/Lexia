"""
Construye el indice de derogaciones a partir de los chunks del piloto.

Lee data/samples/pilot/_chunks.json, aplica el detector de derogaciones
totales, e imprime un reporte humano + guarda data/samples/pilot/_derogations.json

Pensado para validacion manual antes de integrar 'vigente: false' en la
metadata de los chunks. El reporte impreso es el insumo principal: vos
revisas si cada evento detectado tiene sentido, y solo despues decidimos
si confiar en el detector para marcar metadata en produccion.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from derogation_detector import detectar_derogaciones_totales


CHUNKS_PATH = PROJECT_ROOT / "data" / "samples" / "pilot" / "_chunks.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "samples" / "pilot" / "_derogations.json"


def main():
    print("=" * 70)
    print("DETECCION DE DEROGACIONES TOTALES SOBRE EL PILOTO")
    print("=" * 70)

    if not CHUNKS_PATH.exists():
        print(f"ERROR: no se encuentra {CHUNKS_PATH}")
        print("       Corre primero: python scripts\\run_pilot_chunking.py")
        sys.exit(1)

    print(f"\nLeyendo chunks desde {CHUNKS_PATH}...")
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"  {len(chunks)} chunks cargados.")

    print("\nDetectando derogaciones totales...")
    eventos = detectar_derogaciones_totales(chunks)
    print(f"  {len(eventos)} eventos derogatorios detectados.")

    # Agrupar para reporte
    por_derogatorio = defaultdict(list)
    derogados_unicos = set()
    derogatorios_unicos = set()

    for ev in eventos:
        por_derogatorio[ev["doc_id_derogatorio"]].append(ev)
        derogados_unicos.add(ev["doc_id_derogado"])
        derogatorios_unicos.add(ev["doc_id_derogatorio"])

    # === REPORTE PARA VALIDACION HUMANA ===
    print("\n" + "=" * 70)
    print("EVENTOS DETECTADOS (para validacion manual)")
    print("=" * 70)

    for doc_derog in sorted(por_derogatorio.keys()):
        eventos_doc = por_derogatorio[doc_derog]
        print(f"\n[{doc_derog}] deroga {len(eventos_doc)} norma(s):")
        for ev in eventos_doc:
            print(f"  -> {ev['doc_id_derogado']}")
            print(f"     verbo:     {ev['verbo']}")
            print(f"     evidencia: {ev['texto_evidencia']}")
            print(f"     chunk:     {ev['chunk_id_origen']}")

    # === ESTADISTICAS ===
    print("\n" + "=" * 70)
    print("ESTADISTICAS")
    print("=" * 70)
    print(f"  Total eventos:                  {len(eventos)}")
    print(f"  Docs derogatorios unicos:       {len(derogatorios_unicos)}")
    print(f"  Docs derogados unicos:          {len(derogados_unicos)}")

    # ¿Alguno de los derogados esta tambien presente en el piloto?
    docs_en_piloto = set(c["metadata"]["doc_id"] for c in chunks)
    derogados_en_piloto = derogados_unicos & docs_en_piloto
    print(f"  Derogados presentes en piloto:  {len(derogados_en_piloto)}")
    if derogados_en_piloto:
        print(f"    -> {sorted(derogados_en_piloto)}")
        print("    (estos son los que podriamos marcar como vigente=False ahora mismo)")

    # === GUARDAR JSON ===
    print(f"\nGuardando indice en {OUTPUT_PATH}...")
    salida = {
        "total_eventos": len(eventos),
        "derogatorios_unicos": sorted(derogatorios_unicos),
        "derogados_unicos": sorted(derogados_unicos),
        "derogados_presentes_en_piloto": sorted(derogados_en_piloto),
        "eventos": eventos,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, indent=2, ensure_ascii=False)
    print(f"  {OUTPUT_PATH.stat().st_size} bytes escritos.")

    print("\nSiguiente paso:")
    print("  Revisar manualmente que cada evento detectado tenga sentido.")
    print("  Si todo OK, integrar 'vigente: false' en la metadata de los chunks.")


if __name__ == "__main__":
    main()
