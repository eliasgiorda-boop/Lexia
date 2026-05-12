"""
Chunker condicional para documentos del Digesto Municipal.

Estrategia (regla 82/12/6):
- Doc < 2.000 chars  -> 1 chunk unico
- Doc con articulos  -> 1 chunk por articulo (sub-chunkear si > 6.000 chars)
- Doc >= 2.000 chars sin articulos -> chunking por caracteres (1.500 con overlap 200)

Procesamiento de anexos (cuando existen):
- Cada ANEXO se trata como un "articulo gigante": 1 chunk si cabe, o
  sub-chunking por caracteres si supera UMBRAL_ARTICULO_LARGO.
- Los anexos se agregan despues de los articulos, preservando el orden
  natural del documento original.

Modulos auxiliares:
- parser.py            : extraccion de header, body limpio y articulos
- article_filter.py    : filtrado de falsos positivos en articulos
- annex_extractor.py   : extraccion de anexos como entidades separadas
"""
import re
from pathlib import Path

from parser import parse_header, clean_body, extract_articles
from article_filter import filtrar_falsos_positivos
from annex_extractor import extraer_anexos


UMBRAL_DOC_CORTO = 2_000
UMBRAL_ARTICULO_LARGO = 6_000
TAM_CHUNK_FALLBACK = 1_500
OVERLAP_FALLBACK = 200


def _build_doc_id(metadata: dict) -> str:
    tipo = metadata.get("tipo_norma")
    numero = metadata.get("numero")
    anio = metadata.get("anio")

    if tipo and numero and anio:
        return f"{tipo}_{numero}_{anio}"

    unid = metadata.get("unid")
    if unid:
        return f"unid_{unid[:12].lower()}"

    return "doc_desconocido"


def _chunk_por_caracteres(texto: str, tam: int, overlap: int) -> list[str]:
    if not texto:
        return []

    if len(texto) <= tam:
        return [texto]

    chunks = []
    inicio = 0
    paso = tam - overlap

    while inicio < len(texto):
        fin = inicio + tam
        pedazo = texto[inicio:fin].strip()
        if pedazo:
            chunks.append(pedazo)
        if fin >= len(texto):
            break
        inicio += paso

    return chunks


def _chunkear_anexo(anexo: dict, doc_id: str, meta_base: dict) -> list[dict]:
    """
    Convierte un anexo en una lista de chunks.
    - Si cabe en un chunk (<= UMBRAL_ARTICULO_LARGO): 1 chunk.
    - Si es grande: sub-chunking por caracteres con overlap.
    """
    texto = anexo["texto"]
    num_romano = anexo["num"]

    if len(texto) <= UMBRAL_ARTICULO_LARGO:
        return [{
            "chunk_id": f"{doc_id}_anexo_{num_romano}",
            "texto": texto,
            "metadata": {
                **meta_base,
                "tipo_chunk": "anexo",
                "anexo_num": num_romano,
                "articulo_num": None,
                "char_count": len(texto),
            },
        }]

    # Anexo largo -> sub-chunking
    sub_chunks = _chunk_por_caracteres(texto, TAM_CHUNK_FALLBACK, OVERLAP_FALLBACK)
    chunks = []
    for idx, sub in enumerate(sub_chunks, start=1):
        chunks.append({
            "chunk_id": f"{doc_id}_anexo_{num_romano}_part_{idx}",
            "texto": sub,
            "metadata": {
                **meta_base,
                "tipo_chunk": "anexo_parte",
                "anexo_num": num_romano,
                "parte": idx,
                "articulo_num": None,
                "char_count": len(sub),
            },
        })
    return chunks


def chunk_document(filepath, verbose=False):
    """
    Recibe la ruta a un .txt del Digesto y devuelve una lista de chunks
    listos para embebir, cada uno con texto + metadata.
    """
    filepath = Path(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        contenido = f.read()

    metadata = parse_header(contenido)
    cuerpo = clean_body(contenido)
    doc_id = _build_doc_id(metadata)

    meta_base = {
        "doc_id": doc_id,
        "unid_origen": metadata.get("unid"),
        "url_origen": metadata.get("url_origen"),
        "tipo_norma": metadata.get("tipo_norma"),
        "numero": metadata.get("numero"),
        "anio": metadata.get("anio"),
        "titulo_corto": metadata.get("titulo_corto"),
        "fecha_publicacion": metadata.get("fecha_publicacion"),
        "boletin_oficial": metadata.get("boletin_oficial"),
        "categoria": metadata.get("categoria"),
    }

    chunks = []

    # --- CAMINO 1: doc corto -> 1 chunk unico ---
    if len(cuerpo) < UMBRAL_DOC_CORTO:
        chunks.append({
            "chunk_id": f"{doc_id}_full",
            "texto": cuerpo,
            "metadata": {
                **meta_base,
                "tipo_chunk": "doc_completo",
                "articulo_num": None,
                "char_count": len(cuerpo),
            },
        })
        return chunks

    # --- Extraccion de articulos y anexos (independientemente) ---
    articulos = extract_articles(cuerpo)
    articulos, reporte_falsos = filtrar_falsos_positivos(articulos, verbose=verbose)
    if reporte_falsos and verbose:
        print(f"  Falsos positivos descartados en {filepath.name}: {len(reporte_falsos)}")

    anexos = extraer_anexos(cuerpo)
    if anexos and verbose:
        print(f"  Anexos detectados en {filepath.name}: {len(anexos)} "
              f"(chars totales: {sum(a['char_count'] for a in anexos)})")

    # --- CAMINO 2: doc con articulos y/o anexos ---
    if articulos or anexos:
        # Primero los articulos (orden natural del documento)
        for art in articulos:
            texto_art = art["texto"]
            num = art["num"]

            if len(texto_art) <= UMBRAL_ARTICULO_LARGO:
                chunks.append({
                    "chunk_id": f"{doc_id}_art_{num}",
                    "texto": texto_art,
                    "metadata": {
                        **meta_base,
                        "tipo_chunk": "articulo",
                        "articulo_num": num,
                        "char_count": len(texto_art),
                    },
                })
            else:
                sub_chunks = _chunk_por_caracteres(
                    texto_art, TAM_CHUNK_FALLBACK, OVERLAP_FALLBACK
                )
                for idx, sub in enumerate(sub_chunks, start=1):
                    chunks.append({
                        "chunk_id": f"{doc_id}_art_{num}_part_{idx}",
                        "texto": sub,
                        "metadata": {
                            **meta_base,
                            "tipo_chunk": "articulo_largo_parte",
                            "articulo_num": num,
                            "parte": idx,
                            "char_count": len(sub),
                        },
                    })

        # Despues los anexos (orden natural: I, II, III...)
        for anexo in anexos:
            chunks.extend(_chunkear_anexo(anexo, doc_id, meta_base))

        return chunks

    # --- CAMINO 3: doc largo sin articulos NI anexos -> chunking por caracteres ---
    sub_chunks = _chunk_por_caracteres(cuerpo, TAM_CHUNK_FALLBACK, OVERLAP_FALLBACK)
    for idx, sub in enumerate(sub_chunks, start=1):
        chunks.append({
            "chunk_id": f"{doc_id}_chunk_{idx}",
            "texto": sub,
            "metadata": {
                **meta_base,
                "tipo_chunk": "fallback_caracteres",
                "articulo_num": None,
                "parte": idx,
                "char_count": len(sub),
            },
        })

    return chunks


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python chunker.py <ruta_al_archivo.txt> [--verbose]")
        sys.exit(1)

    archivo = sys.argv[1]
    verbose = "--verbose" in sys.argv
    resultado = chunk_document(archivo, verbose=verbose)

    print(f"Documento: {archivo}")
    print(f"Total de chunks generados: {len(resultado)}\n")
    print("=" * 70)

    # Si hay muchos chunks, mostrar solo primeros y ultimos
    MAX_PREVIEW = 15
    if len(resultado) <= MAX_PREVIEW:
        items = list(enumerate(resultado, start=1))
    else:
        items = list(enumerate(resultado[:8], start=1))
        items.append(None)  # marcador
        items.extend(list(enumerate(resultado[-5:], start=len(resultado) - 4)))

    for entry in items:
        if entry is None:
            print(f"\n  ... ({len(resultado) - 13} chunks omitidos) ...")
            continue
        i, c = entry
        print(f"\n[CHUNK {i}/{len(resultado)}]  chunk_id = {c['chunk_id']}")
        print(f"  tipo_chunk = {c['metadata']['tipo_chunk']}  |  "
              f"chars = {c['metadata']['char_count']}")
        if c['metadata'].get('articulo_num') is not None:
            print(f"  articulo  = {c['metadata']['articulo_num']}")
        if c['metadata'].get('anexo_num') is not None:
            print(f"  anexo     = {c['metadata']['anexo_num']}")
        preview = c["texto"][:200].replace("\n", " ")
        if len(c["texto"]) > 200:
            preview += "..."
        print(f"  preview   : {preview}")

    print()
    print("=" * 70)
    print("RESUMEN POR TIPO DE CHUNK")
    print("=" * 70)
    tipos = {}
    chars_por_tipo = {}
    for c in resultado:
        t = c['metadata']['tipo_chunk']
        tipos[t] = tipos.get(t, 0) + 1
        chars_por_tipo[t] = chars_por_tipo.get(t, 0) + c['metadata']['char_count']
    for t, n in tipos.items():
        print(f"  {t:30} : {n:4} chunks  |  {chars_por_tipo[t]:8} chars totales")
    print(f"\n  TOTAL CHARS CHUNKEADOS: {sum(chars_por_tipo.values())}")
