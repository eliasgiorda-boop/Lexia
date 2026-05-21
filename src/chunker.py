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


def _build_link_original(metadata: dict) -> dict:
    """
    Construye el link al original (HTML del Digesto viejo o PDF del nuevo)
    segun el url_origen del documento. Para la UI.

    Devuelve {url, tipo} donde tipo es 'html_viejo', 'pdf_nuevo' o 'sin_link'.
    """
    url = metadata.get("url_origen") or ""
    if "digesto.cdsma.gob.ar/normas/" in url:
        return {"url": url, "tipo": "pdf_nuevo"}
    if "digeh.cdsma.gob.ar" in url:
        return {"url": url, "tipo": "html_viejo"}
    return {"url": "", "tipo": "sin_link"}


def _chunkear_anexo_con_articulos(anexo, doc_id, meta_base, link_original):
    """
    Procesa un anexo que tiene contenido real, detectando si tiene articulos
    propios internos.

    - Si el anexo tiene articulos -> 1 chunk por articulo con chunk_id
      jerarquico: doc_id + _anexo_N + _art_M
    - Si NO tiene articulos -> chunk(s) por caracteres del anexo entero
      (mismo comportamiento que el chunker original)
    """
    texto_anexo = anexo["texto"]
    anexo_num = anexo["num"]
    anexo_id = f"{doc_id}_anexo_{anexo_num}"

    # Intentar detectar articulos DENTRO del anexo
    articulos_internos = extract_articles(texto_anexo)
    articulos_internos, _ = filtrar_falsos_positivos(articulos_internos)

    chunks = []
    meta_anexo_base = {
        **meta_base,
        "anexo_num": anexo_num,
        "anexo_label": anexo["label"],
        "chunk_id_padre": doc_id,
        "link_original_url": link_original["url"],
        "link_original_tipo": link_original["tipo"],
    }

    if articulos_internos:
        # El anexo tiene articulos propios: 1 chunk por articulo
        for art in articulos_internos:
            texto_art = art["texto"]
            num = art["num"]

            if len(texto_art) <= UMBRAL_ARTICULO_LARGO:
                chunks.append({
                    "chunk_id": f"{anexo_id}_art_{num}",
                    "texto": texto_art,
                    "metadata": {
                        **meta_anexo_base,
                        "tipo_chunk": "articulo_anexo",
                        "articulo_num": num,
                        "char_count": len(texto_art),
                        "chunk_id_anexo_padre": anexo_id,
                    },
                })
            else:
                sub_chunks = _chunk_por_caracteres(
                    texto_art, TAM_CHUNK_FALLBACK, OVERLAP_FALLBACK
                )
                for idx, sub in enumerate(sub_chunks, start=1):
                    chunks.append({
                        "chunk_id": f"{anexo_id}_art_{num}_part_{idx}",
                        "texto": sub,
                        "metadata": {
                            **meta_anexo_base,
                            "tipo_chunk": "articulo_anexo_largo_parte",
                            "articulo_num": num,
                            "parte": idx,
                            "char_count": len(sub),
                            "chunk_id_anexo_padre": anexo_id,
                        },
                    })
    else:
        # El anexo NO tiene articulos: chunkear por caracteres como antes
        if len(texto_anexo) <= UMBRAL_ARTICULO_LARGO:
            chunks.append({
                "chunk_id": anexo_id,
                "texto": texto_anexo,
                "metadata": {
                    **meta_anexo_base,
                    "tipo_chunk": "anexo",
                    "articulo_num": None,
                    "char_count": len(texto_anexo),
                },
            })
        else:
            sub_chunks = _chunk_por_caracteres(
                texto_anexo, TAM_CHUNK_FALLBACK, OVERLAP_FALLBACK
            )
            for idx, sub in enumerate(sub_chunks, start=1):
                chunks.append({
                    "chunk_id": f"{anexo_id}_part_{idx}",
                    "texto": sub,
                    "metadata": {
                        **meta_anexo_base,
                        "tipo_chunk": "anexo_parte",
                        "articulo_num": None,
                        "parte": idx,
                        "char_count": len(sub),
                    },
                })

    return chunks


def _chunk_marker_anexo_vacio(anexo, doc_id, meta_base, link_original):
    """
    Construye un chunk marker para un anexo que existe formalmente pero
    no tiene texto digitalizado (PDF adjunto en el original).

    El texto del chunk es informativo y embebible. La UI puede mostrar
    un link al original cuando este chunk aparece en resultados.
    """
    anexo_num = anexo["num"]
    tipo_norma = meta_base.get("tipo_norma", "norma")
    numero = meta_base.get("numero", "?")
    anio = meta_base.get("anio", "?")

    texto = (
        f"Anexo {anexo_num} de {tipo_norma} N° {numero}/{anio}. "
        f"El contenido de este anexo no se encuentra digitalizado en el texto "
        f"de la norma. Ver documento original para acceder al anexo completo."
    )

    return {
        "chunk_id": f"{doc_id}_anexo_{anexo_num}",
        "texto": texto,
        "metadata": {
            **meta_base,
            "tipo_chunk": "anexo_vacio",
            "anexo_num": anexo_num,
            "anexo_label": anexo["label"],
            "articulo_num": None,
            "char_count": len(texto),
            "es_anexo_vacio": True,
            "chunk_id_padre": doc_id,
            "link_original_url": link_original["url"],
            "link_original_tipo": link_original["tipo"],
        },
    }


def _build_doc_id(metadata: dict) -> str:
    """
    Construye el doc_id usado como prefijo de todos los chunk_ids.

    Estrategia:
      1. Si tiene tipo+numero+anio Y unid: usar tipo_numero_anio_uXXXXX
         (5 chars del UNID discriminan colisiones del corpus, hay 7 casos
         reales de ordenanzas distintas con mismo numero+anio).
      2. Si tiene tipo+numero+anio sin unid: usar tipo_numero_anio
         (las 14 normas del scraper nuevo son unicas por construccion).
      3. Si solo tiene unid: usar unid_xxx.
      4. Fallback: doc_desconocido.

    Determinismo garantizado: mismo archivo -> mismo doc_id siempre.
    """
    tipo = metadata.get("tipo_norma")
    numero = metadata.get("numero")
    anio = metadata.get("anio")
    unid = metadata.get("unid")

    if tipo and numero and anio:
        if unid:
            # Sufijo de 5 chars hex del UNID para discriminar colisiones
            sufijo = unid[:5].lower()
            return f"{tipo}_{numero}_{anio}_u{sufijo}"
        return f"{tipo}_{numero}_{anio}"

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


def _post_procesar_chunks(chunks: list) -> list:
    """
    Post-procesa la lista de chunks antes de retornar:

      1. Filtra chunks con texto vacio (no embebibles).
      2. Deduplica chunk_ids agregando sufijo __N a variantes residuales.
         Preserva chunk_id_original en metadata para trazabilidad UI.

    Estas dos correcciones son seguras y no alteran chunks bien formados.
    """
    if not chunks:
        return chunks

    # 1. Descartar chunks con texto vacio
    chunks_validos = [c for c in chunks if c.get("texto", "").strip()]

    # 2. Deduplicar chunk_ids residuales
    vistos = {}
    for c in chunks_validos:
        cid = c.get("chunk_id")
        if not cid:
            continue
        if cid not in vistos:
            vistos[cid] = 1
        else:
            vistos[cid] += 1
            nuevo_id = f"{cid}__{vistos[cid]}"
            if "metadata" not in c:
                c["metadata"] = {}
            c["metadata"]["chunk_id_original"] = cid
            c["metadata"]["es_variante_chunk_id"] = True
            c["chunk_id"] = nuevo_id

    return chunks_validos


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
        return _post_procesar_chunks(chunks)

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
        # FIX ARQUITECTONICO: separar cuerpo principal de anexos ANTES de procesar.
        # extract_articles() previamente corria sobre el cuerpo COMPLETO incluyendo
        # los textos de anexos, lo que causaba chunk_id duplicados (719 colisiones
        # en el corpus). Ahora cortamos por la posicion del primer anexo y
        # procesamos cuerpo principal y anexos por separado.

        link_original = _build_link_original(metadata)

        if anexos:
            # Cortar cuerpo principal en la posicion del primer anexo
            pos_primer_anexo = min(a["start"] for a in anexos)
            cuerpo_principal = cuerpo[:pos_primer_anexo]
            # Re-extraer articulos solo del cuerpo principal
            articulos = extract_articles(cuerpo_principal)
            articulos, _ = filtrar_falsos_positivos(articulos)

        # Procesar articulos del cuerpo principal (chunk_ids iguales a antes)
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
                        "chunk_id_padre": doc_id,
                        "link_original_url": link_original["url"],
                        "link_original_tipo": link_original["tipo"],
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
                            "chunk_id_padre": doc_id,
                            "link_original_url": link_original["url"],
                            "link_original_tipo": link_original["tipo"],
                        },
                    })

        # Procesar anexos (orden natural: I, II, III...)
        for anexo in anexos:
            if anexo.get("estado_contenido") == "vacio":
                # Anexo sin contenido digitalizado: marker con link al original
                chunks.append(
                    _chunk_marker_anexo_vacio(anexo, doc_id, meta_base, link_original)
                )
            else:
                # Anexo real con contenido: detectar articulos internos
                chunks.extend(
                    _chunkear_anexo_con_articulos(anexo, doc_id, meta_base, link_original)
                )

        return _post_procesar_chunks(chunks)

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

    return _post_procesar_chunks(chunks)


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
