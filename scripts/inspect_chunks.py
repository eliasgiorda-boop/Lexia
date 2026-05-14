"""
Inspector cualitativo de chunks del piloto - v2 con heuristicas refinadas.

Mejoras respecto a la v1:

  1. Los sub-chunks con overlap (articulo_largo_parte, anexo_parte con parte>1)
     NO se marcan como sospechosos por arrancar/terminar mid-word: ESE es su
     diseño esperado (el overlap protege contra el corte).

  2. Solo chequeamos mid-word en chunks que deberian estar semanticamente
     completos: articulo, anexo (no _parte), doc_completo.

  3. Las heuristicas mid-word son mas estrictas: solo flaguean firma clara
     de corte (palabras de 1-3 chars no comunes al inicio, palabras de 1-2
     chars al final sin signo de puntuacion).

  4. Se mantiene la deteccion de basura del scraper.

Salidas:
  data/samples/pilot/_inspection_report.md
  data/samples/pilot/_inspection_report.json
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

SEED = 42

CUOTAS = {
    "articulo": 15,
    "articulo_largo_parte": 10,
    "anexo": 8,
    "anexo_parte": 15,
    "doc_completo": 8,
    "fallback_caracteres": 8,
}

# Tipos de chunk que DEBERIAN ser semanticamente completos
TIPOS_COMPLETOS = {"articulo", "anexo", "doc_completo"}


def es_subchunk_intermedio(chunk):
    """True si es parte >1 de una serie con overlap (corte intencional)."""
    tipo = chunk["metadata"]["tipo_chunk"]
    if tipo not in ("articulo_largo_parte", "anexo_parte", "fallback_caracteres"):
        return False
    parte = chunk["metadata"].get("parte")
    return parte is not None and parte > 1


def empieza_mid_word_estricto(texto):
    """
    True solo si el chunk arranca con firma clara de corte:
    palabra corta (1-3 chars) en minuscula NO comun, seguida de espacio.
    """
    if not texto:
        return False
    texto = texto.lstrip()
    if not texto or not texto[0].islower():
        return False

    m = re.match(r"^([a-záéíóúñü]+)", texto, re.IGNORECASE)
    if not m:
        return False

    primera = m.group(1).lower()
    palabras_comunes = {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "de", "del", "en", "por", "para", "que", "se", "su", "sus",
        "al", "lo", "le", "les", "me", "te", "no", "si",
        "con", "sin", "y", "o", "u", "e", "es", "son", "ser",
    }
    if primera in palabras_comunes:
        return False

    return len(primera) <= 3


def termina_mid_word_estricto(texto):
    """True solo si hay firma clara de corte al final."""
    if not texto:
        return False
    texto = texto.rstrip()
    if not texto:
        return False

    if texto[-1] in '.,;:!?)"\']}>-—…':
        return False
    if texto[-1].isdigit():
        return False

    m = re.search(r"([a-záéíóúñü]+)$", texto, re.IGNORECASE)
    if not m:
        return False

    ultima = m.group(1)
    return len(ultima) <= 2


def tiene_basura_scraper(texto):
    """Devuelve lista de marcadores de basura."""
    basura = []
    marcadores = [
        ("Volver", r"\bVolver\b"),
        ("Imprimir", r"\bImprimir\b"),
        ("Versión para Imprimir", r"Versi[óo]n para Imprimir"),
        ("Información Adicional", r"Informaci[óo]n Adicional"),
        ("Zona no Nuclear (slogan)", r"Zona no Nuclear"),
        ("HTML tags", r"<[a-z]+[^>]*>"),
        ("Mojibake", r"Ã[©³­¡]"),
        ("Dada en la Sala (colado)", r"Dada en la Sala de Sesiones"),
    ]
    for nombre, patron in marcadores:
        if re.search(patron, texto, re.IGNORECASE):
            basura.append(nombre)
    return basura


def es_chunk_sospechoso(chunk):
    """Devuelve lista de razones de sospecha."""
    razones = []
    texto = chunk["texto"]
    chars = chunk["metadata"]["char_count"]
    tipo = chunk["metadata"]["tipo_chunk"]

    if not texto.strip():
        razones.append("texto_vacio")
        return razones

    # Mid-word solo en chunks que deberian ser completos
    if tipo in TIPOS_COMPLETOS:
        if empieza_mid_word_estricto(texto):
            razones.append("empieza_mid_word_estricto")
        if termina_mid_word_estricto(texto):
            razones.append("termina_mid_word_estricto")

    basura = tiene_basura_scraper(texto)
    if basura:
        razones.append(f"basura: {','.join(basura)}")

    if chars < 20:
        razones.append(f"muy_corto ({chars} chars)")
    if chars > 2500:
        razones.append(f"muy_largo ({chars} chars)")

    if tipo == "articulo" and chars < 30:
        razones.append("articulo_minusculo")

    if texto.rstrip().lower().endswith(" como"):
        razones.append("termina_en_'como'_truncado_por_anexo")

    return razones


def formatear_chunk_md(chunk, motivo=""):
    """Formatea un chunk como bloque markdown."""
    meta = chunk["metadata"]
    sospechas = es_chunk_sospechoso(chunk)

    lineas = []
    lineas.append(f"### `{chunk['chunk_id']}`")
    if motivo:
        lineas.append(f"\n**Motivo:** {motivo}")
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
        lineas.append(f"\n**⚠️ Sospechas:** {', '.join(sospechas)}")

    lineas.append(f"\n**Texto:**\n")
    lineas.append("```")
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
    print("INSPECCION CUALITATIVA DE CHUNKS - v2 (heuristicas refinadas)")
    print("=" * 70)

    if not CHUNKS_PATH.exists():
        print(f"ERROR: no se encuentra {CHUNKS_PATH}")
        sys.exit(1)

    print(f"\nLeyendo chunks desde {CHUNKS_PATH}...")
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        all_chunks = json.load(f)
    print(f"  {len(all_chunks)} chunks cargados.")

    rng = random.Random(SEED)

    # Muestreo estratificado
    print("\n[1/3] Muestreo estratificado por tipo_chunk...")
    por_tipo = defaultdict(list)
    for c in all_chunks:
        por_tipo[c["metadata"]["tipo_chunk"]].append(c)

    muestra_estratificada = []
    for tipo, cuota in CUOTAS.items():
        disponibles = por_tipo.get(tipo, [])
        n = min(cuota, len(disponibles))
        if n > 0:
            muestra_estratificada.extend(rng.sample(disponibles, n))
        print(f"  {tipo:25}: {n}/{cuota} (de {len(disponibles)})")

    # Caso conocido
    print("\n[2/3] Caso conocido: ordenanza_10328_2014...")
    caso_conocido = [c for c in all_chunks
                     if c["metadata"]["doc_id"] == "ordenanza_10328_2014"]
    print(f"  {len(caso_conocido)} chunks de este doc en el piloto.")

    # Sospechosos refinados
    print("\n[3/3] Buscando sospechosos (heuristicas refinadas)...")
    sospechosos = []
    for c in all_chunks:
        razones = es_chunk_sospechoso(c)
        if razones:
            sospechosos.append((c, razones))

    def gravedad(item):
        c, razones = item
        score = 0
        if any("vacio" in r for r in razones): score += 100
        if any("basura" in r for r in razones): score += 50
        if any("truncado" in r for r in razones): score += 40
        if any("mid_word" in r for r in razones): score += 20
        if any("muy_corto" in r or "muy_largo" in r for r in razones): score += 10
        return -score

    sospechosos.sort(key=gravedad)
    sospechosos_top = sospechosos[:30]
    print(f"  Total sospechosos: {len(sospechosos)} ({round(len(sospechosos)/len(all_chunks)*100,1)}%)")
    print(f"  Top a revisar: {len(sospechosos_top)}")

    # Generar reporte
    print(f"\nGenerando reporte en {REPORT_MD}...")

    md = []
    md.append("# Inspección cualitativa de chunks - v2\n")
    md.append(f"**Total a revisar:** "
              f"{len(muestra_estratificada) + len(caso_conocido) + len(sospechosos_top)}\n")
    md.append("\n**Tabla de contenidos:**\n")
    md.append("1. [Muestra estratificada](#1-muestra-estratificada-por-tipo_chunk)")
    md.append("2. [Caso conocido](#2-caso-conocido-ordenanza_10328_2014)")
    md.append("3. [Sospechosos](#3-sospechosos-por-detección-refinada)\n")
    md.append("\n---\n")

    md.append("\n## 1. Muestra estratificada por tipo_chunk\n")
    for c in muestra_estratificada:
        md.append(formatear_chunk_md(c, "muestra estratificada aleatoria"))

    md.append("\n## 2. Caso conocido: ordenanza_10328_2014\n")
    if caso_conocido:
        for c in sorted(caso_conocido, key=lambda x: x["metadata"].get("articulo_num", 0)):
            md.append(formatear_chunk_md(c, "caso conocido (falso positivo art 73)"))
    else:
        md.append("_(no hay chunks de este doc en el piloto)_\n")

    md.append("\n## 3. Sospechosos por detección refinada\n")
    for c, razones in sospechosos_top:
        md.append(formatear_chunk_md(c, f"sospechoso: {', '.join(razones)}"))

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    salida_json = {
        "version": 2,
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
    print(f"TOTAL A REVISAR:                 "
          f"{len(muestra_estratificada) + len(caso_conocido) + len(sospechosos_top)}")
    print(f"\nTotal sospechosos: {len(sospechosos)} de {len(all_chunks)}")
    print(f"  ({round(len(sospechosos)/len(all_chunks)*100,1)}% del piloto)")

    razones_count = defaultdict(int)
    for _, razones in sospechosos:
        for r in razones:
            r_norm = re.sub(r"\([^)]*\)", "", r).strip()
            razones_count[r_norm] += 1

    print(f"\nBreakdown:")
    for razon, n in sorted(razones_count.items(), key=lambda x: -x[1]):
        print(f"  {razon:50}: {n}")

    print(f"\nReporte: {REPORT_MD}")


if __name__ == "__main__":
    main()
