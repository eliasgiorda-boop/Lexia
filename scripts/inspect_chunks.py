"""
Inspector cualitativo de chunks del piloto.

Genera un reporte navegable en Markdown para validacion HUMANA.
La idea: leer ~80 chunks estratificados con criterio juridico y detectar
problemas antes de gastar plata en embeddings.

Tres tipos de muestra:
  1. Estratificada por tipo_chunk (representa cada camino del chunker)
  2. Caso conocido con bug: art_8 fusionado de ordenanza_10328_2014
  3. Sospechosos detectados automaticamente (cortes mid-word, tamaño raro, etc)

Salidas:
  data/samples/pilot/_inspection_report.md   <- abrirlo en VSCode/cualquier editor
  data/samples/pilot/_inspection_report.json <- versión estructurada por si la necesitas
"""
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
PILOT_DIR = PROJECT_ROOT / "data" / "samples" / "pilot"
CHUNKS_PATH = PILOT_DIR / "_chunks.json"
REPORT_MD = PILOT_DIR / "_inspection_report.md"
REPORT_JSON = PILOT_DIR / "_inspection_report.json"

# Reproducibilidad
SEED = 42

# Cuotas por tipo_chunk (estratificacion)
CUOTAS = {
    "articulo": 15,
    "articulo_largo_parte": 10,
    "anexo": 8,
    "anexo_parte": 15,
    "doc_completo": 8,
    "fallback_caracteres": 8,
}


def empieza_mid_word(texto: str) -> bool:
    """True si el chunk arranca con un fragmento de palabra (no MAYUSCULA, no signo)."""
    if not texto:
        return False
    primer_char = texto.lstrip()[:1]
    if not primer_char:
        return False
    # Si arranca con minuscula, probablemente es mid-word
    # (excepto palabras que legítimamente empiezan en minuscula, raras al inicio)
    return primer_char.islower()


def termina_mid_word(texto: str) -> bool:
    """True si el chunk termina cortado a mitad de palabra."""
    if not texto:
        return False
    texto = texto.rstrip()
    if not texto:
        return False
    # Si el ultimo char es letra (no signo, no espacio), probablemente mid-word
    ultimo = texto[-1]
    if ultimo in '.,;:!?)"\']}>-—…':
        return False
    return ultimo.isalpha()


def tiene_basura_scraper(texto: str) -> list:
    """Devuelve lista de marcadores de basura encontrada en el chunk."""
    basura = []
    marcadores = [
        ("Volver", r"\bVolver\b"),
        ("Imprimir", r"\bImprimir\b"),
        ("Versión para Imprimir", r"Versi[óo]n para Imprimir"),
        ("Información Adicional", r"Informaci[óo]n Adicional"),
        ("Zona no Nuclear (slogan)", r"Zona no Nuclear"),
        ("HTML tags", r"<[a-z]+[^>]*>"),
        ("Mojibake clásico", r"Ã[©³­¡]"),
    ]
    for nombre, patron in marcadores:
        if re.search(patron, texto, re.IGNORECASE):
            basura.append(nombre)
    return basura


def es_chunk_sospechoso(chunk: dict) -> list:
    """Devuelve lista de razones por las que el chunk podria tener problemas."""
    razones = []
    texto = chunk["texto"]
    chars = chunk["metadata"]["char_count"]

    if not texto.strip():
        razones.append("texto_vacio")
        return razones

    if empieza_mid_word(texto):
        razones.append("empieza_mid_word")

    if termina_mid_word(texto):
        razones.append("termina_mid_word")

    basura = tiene_basura_scraper(texto)
    if basura:
        razones.append(f"basura: {','.join(basura)}")

    # Tamano anomalo
    if chars < 30:
        razones.append(f"muy_corto ({chars} chars)")
    if chars > 2500:
        razones.append(f"muy_largo ({chars} chars)")

    # Articulos sin contenido sustantivo (solo el verbo o frase corta)
    if chunk["metadata"]["tipo_chunk"] == "articulo" and chars < 60:
        razones.append("articulo_minusculo")

    # Si dice "como" o termina en preposicion, probable truncado por ANEXO
    if texto.rstrip().endswith(" como") or texto.rstrip().endswith(" como:"):
        razones.append("termina_en_'como'_probable_truncado_por_anexo")

    return razones


def formatear_chunk_md(chunk: dict, motivo: str = "") -> str:
    """Formatea un chunk como bloque markdown navegable."""
    meta = chunk["metadata"]
    sospechas = es_chunk_sospechoso(chunk)

    lineas = []
    lineas.append(f"### `{chunk['chunk_id']}`")
    if motivo:
        lineas.append(f"\n**Motivo de inclusión:** {motivo}")
    lineas.append(f"\n**Metadata:**")
    lineas.append(f"- `tipo_chunk`: {meta.get('tipo_chunk')}")
    lineas.append(f"- `doc_id`: {meta.get('doc_id')}")
    lineas.append(f"- `tipo_norma`: {meta.get('tipo_norma')}")
    lineas.append(f"- `numero`: {meta.get('numero')} | `anio`: {meta.get('anio')}")
    if meta.get("articulo_num") is not None:
        lineas.append(f"- `articulo_num`: {meta.get('articulo_num')}")
    if meta.get("anexo_num") is not None:
        lineas.append(f"- `anexo_num`: {meta.get('anexo_num')}")
    if meta.get("parte") is not None:
        lineas.append(f"- `parte`: {meta.get('parte')}")
    lineas.append(f"- `char_count`: {meta.get('char_count')}")
    lineas.append(f"- `url_origen`: {meta.get('url_origen')}")

    if sospechas:
        lineas.append(f"\n**⚠️ Sospechas automaticas:** {', '.join(sospechas)}")

    lineas.append(f"\n**Texto del chunk:**\n")
    lineas.append("```")
    # Truncar a 3000 chars para que el reporte no sea inmanejable
    texto = chunk["texto"]
    if len(texto) > 3000:
        texto = texto[:3000] + f"\n\n[... TEXTO TRUNCADO, hay {len(chunk['texto']) - 3000} chars mas ...]"
    lineas.append(texto)
    lineas.append("```\n")
    lineas.append("**Veredicto del revisor:** [ OK / PROBLEMA: ... ]\n")
    lineas.append("---\n")
    return "\n".join(lineas)


def main():
    print("=" * 70)
    print("INSPECCION CUALITATIVA DE CHUNKS DEL PILOTO")
    print("=" * 70)

    if not CHUNKS_PATH.exists():
        print(f"ERROR: no se encuentra {CHUNKS_PATH}")
        sys.exit(1)

    print(f"\nLeyendo chunks desde {CHUNKS_PATH}...")
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        all_chunks = json.load(f)
    print(f"  {len(all_chunks)} chunks cargados.")

    rng = random.Random(SEED)

    # ===== 1. Muestreo estratificado por tipo_chunk =====
    print("\n[1/3] Muestreo estratificado por tipo_chunk...")
    por_tipo = defaultdict(list)
    for c in all_chunks:
        por_tipo[c["metadata"]["tipo_chunk"]].append(c)

    muestra_estratificada = []
    for tipo, cuota in CUOTAS.items():
        disponibles = por_tipo.get(tipo, [])
        n = min(cuota, len(disponibles))
        if n > 0:
            seleccion = rng.sample(disponibles, n)
            muestra_estratificada.extend(seleccion)
        print(f"  {tipo:25}: {n}/{cuota} (de {len(disponibles)} disponibles)")

    # ===== 2. Caso conocido: chunks del falso positivo fusionado =====
    print("\n[2/3] Caso conocido: ordenanza_10328_2014 (filtro de falso positivo)...")
    caso_conocido = [c for c in all_chunks
                     if c["metadata"]["doc_id"] == "ordenanza_10328_2014"]
    print(f"  {len(caso_conocido)} chunks de este doc en el piloto.")

    # ===== 3. Sospechosos por deteccion automatica =====
    print("\n[3/3] Buscando chunks sospechosos automaticamente...")
    sospechosos = []
    for c in all_chunks:
        razones = es_chunk_sospechoso(c)
        if razones:
            sospechosos.append((c, razones))

    # Top 30 sospechosos, priorizando los mas graves
    def gravedad(item):
        c, razones = item
        score = 0
        if any("vacio" in r for r in razones): score += 100
        if any("basura" in r for r in razones): score += 50
        if any("truncado_por_anexo" in r for r in razones): score += 40
        if any("mid_word" in r for r in razones): score += 20
        if any("anomalo" in r or "muy_corto" in r or "muy_largo" in r for r in razones): score += 10
        return -score

    sospechosos.sort(key=gravedad)
    sospechosos_top = sospechosos[:30]
    print(f"  Total sospechosos encontrados: {len(sospechosos)}")
    print(f"  Tomando top {len(sospechosos_top)} por gravedad.")

    # ===== Generar reporte markdown =====
    print(f"\nGenerando reporte en {REPORT_MD}...")

    md = []
    md.append("# Inspección cualitativa de chunks del piloto\n")
    md.append(f"**Total chunks revisados:** {len(muestra_estratificada) + len(caso_conocido) + len(sospechosos_top)}")
    md.append(f"\n**Tabla de contenidos:**\n")
    md.append("1. [Muestra estratificada](#1-muestra-estratificada-por-tipo_chunk)")
    md.append("2. [Caso conocido: ordenanza_10328_2014](#2-caso-conocido-ordenanza_10328_2014)")
    md.append("3. [Sospechosos por detección automática](#3-sospechosos-por-detección-automática)\n")
    md.append("\n**Cómo usar este reporte:**\n")
    md.append("Leé cada chunk y en `Veredicto del revisor` marcá:")
    md.append("- `OK` si el chunk se ve bien")
    md.append("- `PROBLEMA: descripción` si hay algo que arreglar")
    md.append("\n---\n")

    md.append("\n## 1. Muestra estratificada por tipo_chunk\n")
    for c in muestra_estratificada:
        md.append(formatear_chunk_md(c, "muestra estratificada aleatoria"))

    md.append("\n## 2. Caso conocido: ordenanza_10328_2014\n")
    md.append("Este documento dispara el filtro de falsos positivos por el caso del Art 73 del Código de Faltas.\n")
    for c in sorted(caso_conocido, key=lambda x: x["metadata"].get("articulo_num", 0)):
        md.append(formatear_chunk_md(c, "caso conocido con filtro"))

    md.append("\n## 3. Sospechosos por detección automática\n")
    md.append(f"Top {len(sospechosos_top)} chunks con sospechas automáticas, ordenados por gravedad.\n")
    for c, razones in sospechosos_top:
        md.append(formatear_chunk_md(c, f"sospechoso: {', '.join(razones)}"))

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # JSON paralelo para uso programatico
    salida_json = {
        "total_chunks_inspeccionados": len(muestra_estratificada) + len(caso_conocido) + len(sospechosos_top),
        "muestra_estratificada_ids": [c["chunk_id"] for c in muestra_estratificada],
        "caso_conocido_ids": [c["chunk_id"] for c in caso_conocido],
        "sospechosos": [
            {"chunk_id": c["chunk_id"], "razones": razones}
            for c, razones in sospechosos_top
        ],
        "estadisticas_sospechas": {
            "total_chunks_corpus": len(all_chunks),
            "total_sospechosos": len(sospechosos),
            "pct_sospechosos": round(len(sospechosos) / len(all_chunks) * 100, 1),
        },
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(salida_json, f, indent=2, ensure_ascii=False)

    # Resumen
    print(f"\n{'=' * 70}")
    print("RESUMEN")
    print("=" * 70)
    print(f"Chunks en muestra estratificada: {len(muestra_estratificada)}")
    print(f"Chunks del caso conocido:        {len(caso_conocido)}")
    print(f"Sospechosos top:                 {len(sospechosos_top)}")
    print(f"TOTAL A REVISAR:                 {len(muestra_estratificada) + len(caso_conocido) + len(sospechosos_top)}")

    print(f"\nTotal sospechosos detectados en el corpus: {len(sospechosos)} de {len(all_chunks)}")
    print(f"  ({round(len(sospechosos) / len(all_chunks) * 100, 1)}% del piloto tiene alguna sospecha)")

    # Breakdown de tipos de sospecha
    razones_count = defaultdict(int)
    for _, razones in sospechosos:
        for r in razones:
            # Normalizar (quitar numeros entre parentesis)
            r_norm = re.sub(r"\([^)]*\)", "", r).strip()
            razones_count[r_norm] += 1

    print(f"\nBreakdown de sospechas (puede haber multiples por chunk):")
    for razon, n in sorted(razones_count.items(), key=lambda x: -x[1]):
        print(f"  {razon:50}: {n}")

    print(f"\nReporte navegable: {REPORT_MD}")
    print(f"Estructurado:      {REPORT_JSON}")
    print(f"\nAbrí el .md en VSCode o cualquier editor markdown y leé chunk por chunk.")


if __name__ == "__main__":
    main()
