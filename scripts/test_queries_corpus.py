"""
Validacion del indice COMPLETO (Carta Organica + corpus de 8.127 docs).

Estas queries estan diseñadas para verificar que la busqueda integra
correctamente la Carta Organica con ordenanzas, resoluciones y comunicaciones.

Cada query muestra de que TIPO de documento viene cada resultado, para
ver como se mezclan las fuentes.

USO:
  python scripts/test_queries_corpus.py
  python scripts/test_queries_corpus.py --top 5
"""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from embedder import embed_texts
from indexer import get_collection


# Queries que tocan distintas combinaciones de fuentes
QUERIES = [
    # Temas que deberian mezclar Carta Organica + ordenanzas
    "Banca del Vecino",
    "Audiencia Publica obligatoria",
    "remuneracion del intendente",
    "juicio politico",
    "presupuesto municipal",

    # Temas tipicos de ordenanzas
    "ordenanza tarifaria alumbrado publico",
    "habilitacion comercial requisitos",
    "exencion de tasas para jubilados",
    "codigo de edificacion construccion",
    "estacionamiento medido en el centro",

    # Temas de medio ambiente (CO art 24 + ordenanzas)
    "proteccion del medio ambiente y reservas naturales",
    "residuos solidos urbanos",

    # Temas especificos
    "Cerro Chapelco",
    "transporte publico de pasajeros",
    "defensoria del pueblo",
]


def describir_resultado(metadata):
    """Describe de forma compacta de donde viene un resultado."""
    tipo_doc = metadata.get("tipo_documento") or metadata.get("tipo_norma", "?")

    if tipo_doc == "carta_organica":
        tipo_chunk = metadata.get("tipo_chunk", "")
        if tipo_chunk == "preambulo":
            return "CARTA ORGANICA - Preambulo"
        if tipo_chunk == "disposicion_transitoria":
            return f"CARTA ORGANICA - Transitoria {metadata.get('transitoria_ordinal','')}"
        if tipo_chunk == "inciso_carta_organica":
            return f"CARTA ORGANICA - Art. {metadata.get('articulo_num')} inc. {metadata.get('inciso_num')}"
        return f"CARTA ORGANICA - Art. {metadata.get('articulo_num','?')}"

    # Documentos del corpus
    numero = metadata.get("numero", "?")
    anio = metadata.get("anio", "?")
    titulo = metadata.get("titulo_corto", "")
    tipo_label = tipo_doc.upper() if tipo_doc else "?"

    desc = f"{tipo_label} {numero}/{anio}"
    if titulo:
        desc += f" - {titulo[:50]}"

    # Indicar si es un anexo o articulo especifico
    tipo_chunk = metadata.get("tipo_chunk", "")
    if tipo_chunk == "articulo" and metadata.get("articulo_num"):
        desc += f" (Art. {metadata.get('articulo_num')})"
    elif tipo_chunk == "articulo_anexo":
        desc += f" (Anexo {metadata.get('anexo_num')} Art. {metadata.get('articulo_num')})"
    elif tipo_chunk == "anexo_vacio":
        desc += f" (Anexo {metadata.get('anexo_num')} - sin digitalizar)"

    # Marcar derogadas
    if metadata.get("es_derogada_por_filename"):
        desc += " [DEROGADA]"

    return desc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=4)
    args = parser.parse_args()

    print("=" * 70)
    print("VALIDACION DEL INDICE COMPLETO (Carta Organica + Corpus)")
    print("=" * 70)

    col = get_collection()
    total = col.count()
    print(f"Coleccion: {col.name} | Total chunks: {total:,}\n")

    for i, query in enumerate(QUERIES, 1):
        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(QUERIES)}] QUERY: {query}")
        print("=" * 70)

        [q_emb] = embed_texts([query], verbose=False)
        res = col.query(query_embeddings=[q_emb], n_results=args.top)

        ids = res["ids"][0]
        metas = res["metadatas"][0]
        distances = res["distances"][0]

        for rank, (cid, meta, dist) in enumerate(zip(ids, metas, distances), 1):
            sim = 1 - dist
            print(f"  #{rank} | sim {sim:.3f} | {describir_resultado(meta)}")

    print(f"\n{'=' * 70}")
    print(f"Validacion completa: {len(QUERIES)} queries sobre {total:,} chunks")
    print("=" * 70)


if __name__ == "__main__":
    main()
