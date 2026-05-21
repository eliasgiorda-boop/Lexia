"""
Construye el indice unificado de derogaciones combinando dos fuentes:

  1. derogation_detector  : verbos en el texto (DERÓGASE, DERÓGANSE)
  2. derogation_from_filename : marca DEROGADA en el nombre del archivo

Ambas fuentes son complementarias:
  - Texto: descubre quien deroga + a quien deroga (con evidencia)
  - Filename: descubre normas marcadas como derogadas por el propio
              municipio en el sistema de archivos (incluye casos viejos
              donde la norma derogatoria pudo perderse)

Salidas:
  data/_audit/_derogations_unified.json
    {
      "fuentes_resumen": { texto: N1, filename: N2, ambas: N3 },
      "doc_ids_derogados_unificado": [ ... ],
      "eventos": [
        { doc_id_derogado, fuente: "texto"|"filename"|"ambas", ... }
      ]
    }
"""
import json
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from derogation_from_filename import detectar_derogados_por_filename


AUDIT_DIR = PROJECT_ROOT / "data" / "_audit"
CORPUS_DIR = Path(os.getenv("CORPUS_PATH", "."))
DEROGATIONS_TEXTO_PATH = AUDIT_DIR / "_audit_derogations.json"
OUTPUT_PATH = AUDIT_DIR / "_derogations_unified.json"


def main():
    print("=" * 70)
    print("UNIFICACION DE DETECTORES DE DEROGACIONES")
    print("=" * 70)

    # === Fuente 1: derogaciones por texto (ya las tenemos del audit) ===
    if not DEROGATIONS_TEXTO_PATH.exists():
        print(f"ERROR: no se encuentra {DEROGATIONS_TEXTO_PATH}")
        print("       Corre primero: python scripts\\audit_corpus.py")
        sys.exit(1)

    print(f"\n[1/3] Leyendo derogaciones por texto desde {DEROGATIONS_TEXTO_PATH}...")
    with open(DEROGATIONS_TEXTO_PATH, "r", encoding="utf-8") as f:
        data_texto = json.load(f)
    eventos_texto = data_texto["eventos"]
    derogados_por_texto = set(data_texto["docs_derogados"])
    print(f"  {len(eventos_texto)} eventos, {len(derogados_por_texto)} docs derogados unicos por texto")

    # === Fuente 2: derogaciones por filename ===
    print(f"\n[2/3] Detectando derogaciones por filename en {CORPUS_DIR}...")
    eventos_filename = detectar_derogados_por_filename(CORPUS_DIR, verbose=False)
    derogados_por_filename = set(e["doc_id_derogado"] for e in eventos_filename)
    print(f"  {len(eventos_filename)} eventos, {len(derogados_por_filename)} docs derogados unicos por filename")

    # === Unificacion ===
    print(f"\n[3/3] Unificando...")

    solo_texto = derogados_por_texto - derogados_por_filename
    solo_filename = derogados_por_filename - derogados_por_texto
    ambas = derogados_por_texto & derogados_por_filename
    todos = derogados_por_texto | derogados_por_filename

    print(f"  Solo por texto:       {len(solo_texto)}")
    print(f"  Solo por filename:    {len(solo_filename)}")
    print(f"  En ambas fuentes:     {len(ambas)}")
    print(f"  TOTAL UNIFICADO:      {len(todos)}")

    # Construir eventos unificados
    eventos_unificados = []

    # Eventos de texto: cada uno tiene su tag de fuente
    for ev in eventos_texto:
        ev_copia = dict(ev)
        if ev["doc_id_derogado"] in derogados_por_filename:
            ev_copia["fuentes"] = ["texto", "filename"]
        else:
            ev_copia["fuentes"] = ["texto"]
        eventos_unificados.append(ev_copia)

    # Eventos de filename: solo agregar los que NO estan ya en texto
    # (para no duplicar info de los que estan en ambas)
    for ev in eventos_filename:
        if ev["doc_id_derogado"] in derogados_por_texto:
            continue  # ya esta cubierto por el evento de texto (que ya tiene "filename" en fuentes)
        ev_copia = dict(ev)
        ev_copia["fuentes"] = ["filename"]
        eventos_unificados.append(ev_copia)

    # === Guardar ===
    salida = {
        "fuentes_resumen": {
            "solo_texto": len(solo_texto),
            "solo_filename": len(solo_filename),
            "ambas": len(ambas),
            "total_unificado": len(todos),
        },
        "doc_ids_derogados_unificado": sorted(todos),
        "eventos": eventos_unificados,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, indent=2, ensure_ascii=False)

    print(f"\nIndice unificado guardado en {OUTPUT_PATH}")
    print(f"  Tamano: {OUTPUT_PATH.stat().st_size / 1024:.1f} KB")

    # === Reporte ===
    print("\n" + "=" * 70)
    print("DOCS DETECTADOS SOLO POR FILENAME (no por texto)")
    print("=" * 70)
    print("Estos son los que el detector de texto se hubiera perdido:\n")
    for ev in eventos_filename:
        if ev["doc_id_derogado"] in solo_filename:
            print(f"  {ev['doc_id_derogado']}")
            print(f"    marca: {ev['marca_encontrada']}")
            print(f"    file:  {ev['nombre_archivo']}")
            print()

    print("=" * 70)
    print("RESUMEN FINAL")
    print("=" * 70)
    print(f"Normas derogadas detectadas (union): {len(todos)}")
    print(f"  - cazadas por ambos detectores: {len(ambas)}")
    print(f"  - solo por texto:               {len(solo_texto)}")
    print(f"  - solo por filename:            {len(solo_filename)}")

    cobertura_mejora_pct = (len(solo_filename) / len(derogados_por_texto) * 100
                            if derogados_por_texto else 0)
    print(f"\nMejora de cobertura aportada por filename: +{cobertura_mejora_pct:.1f}%")


if __name__ == "__main__":
    main()
