"""
Validacion del indice de Carta Organica con 10 queries juridicas reales.

Para cada query:
  1. Embebe el query con OpenAI
  2. Busca top 3 chunks mas similares en ChromaDB
  3. Muestra resultados con score, articulo, jerarquia y un preview del texto

Sirve para validar cualitativamente que el RAG devuelve resultados correctos
antes de escalar al corpus completo.

USO:
  python scripts/test_queries_co.py
  python scripts/test_queries_co.py --top 5      # mostrar top 5 en lugar de top 3
"""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from embedder import embed_texts
from indexer import get_collection


# 10 queries diseñadas con criterio juridico:
# cada una toca un aspecto distinto de la Carta Organica
QUERIES = [
    "Cual es el quorum para sancionar el presupuesto?",
    "Que causales hay para juicio politico al Intendente?",
    "Puede modificarse la remuneracion del Intendente por enmienda?",
    "Como se eligen los concejales municipales?",
    "Que requisitos hay para ser Intendente?",
    "Que dice la Carta Organica sobre el ambiente?",
    "Derechos de los vecinos",
    "Audiencia Publica: cuando es obligatoria",
    "Reforma de la Carta Organica",
    "Atribuciones del Concejo Deliberante",
]


def describir_chunk(chunk_id: str, metadata: dict) -> str:
    """Describe un chunk de forma compacta para mostrar en consola."""
    tipo = metadata.get("tipo_chunk", "?")
    if tipo == "preambulo":
        return "PREAMBULO (blindado)"
    if tipo == "disposicion_transitoria":
        return f"Transitoria {metadata.get('transitoria_ordinal', '?')}"
    if tipo == "articulo_carta_organica":
        num = metadata.get("articulo_num", "?")
        titulo = metadata.get("titulo_num", "?")
        cap = metadata.get("capitulo_num", "?")
        blind = " [BLINDADO]" if metadata.get("no_modificable_por_enmienda") else ""
        return f"Art. {num} (Titulo {titulo} - Cap. {cap}){blind}"
    return chunk_id


def preview_texto(texto: str, max_len: int = 250) -> str:
    """Quita el header de navegacion y devuelve preview del cuerpo."""
    # El texto suele tener formato: "[HEADER]\n\nArticulo N: contenido..."
    if "]\n\n" in texto:
        cuerpo = texto.split("]\n\n", 1)[1]
    else:
        cuerpo = texto
    # Reemplazar saltos por espacios para preview compacto
    cuerpo = " ".join(cuerpo.split())
    if len(cuerpo) <= max_len:
        return cuerpo
    return cuerpo[:max_len] + "..."


def correr_query(col, query: str, n_results: int = 3):
    """Embebe el query y busca top N en ChromaDB."""
    # Embebemos el query con OpenAI (mismo modelo que los chunks)
    [query_emb] = embed_texts([query], verbose=False)

    resultados = col.query(
        query_embeddings=[query_emb],
        n_results=n_results,
    )

    return resultados


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=3,
                        help="Cantidad de resultados por query (default: 3)")
    args = parser.parse_args()

    print("=" * 70)
    print("VALIDACION DEL INDICE - QUERIES SOBRE CARTA ORGANICA")
    print("=" * 70)

    col = get_collection()
    total = col.count()
    print(f"Coleccion: {col.name}")
    print(f"Chunks en DB: {total}")

    if total == 0:
        print("\nERROR: la coleccion esta vacia. Corre primero:")
        print("  python scripts/build_index_co.py")
        sys.exit(1)

    for i, query in enumerate(QUERIES, 1):
        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(QUERIES)}] QUERY: {query}")
        print("=" * 70)

        try:
            res = correr_query(col, query, n_results=args.top)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        distances = res["distances"][0]

        for rank, (chunk_id, doc, meta, dist) in enumerate(
            zip(ids, docs, metas, distances), 1
        ):
            # ChromaDB devuelve distancia coseno (0 = identico, 2 = opuesto)
            # Convertimos a similitud para mostrar mas intuitivo
            similitud = 1 - dist
            print(f"\n  #{rank} | similitud: {similitud:.3f} | {describir_chunk(chunk_id, meta)}")
            print(f"      {preview_texto(doc)}")

    print(f"\n{'=' * 70}")
    print(f"Validacion completa: {len(QUERIES)} queries")
    print("=" * 70)


if __name__ == "__main__":
    main()
