"""
Comparacion lado a lado: busqueda PURA (embeddings) vs HIBRIDA (BM25+RRF).

Para cada query muestra el top 5 de cada metodo, asi se ve objetivamente
donde la hibrida mejora (o no) sobre la semantica pura.

Las queries estan elegidas para cubrir distintos escenarios:
  - Terminos especificos discriminantes (numeros de articulo, nombres propios raros)
  - Conceptos juridicos
  - Terminos comunes ambiguos

USO:
  python scripts/test_hibrida_vs_pura.py
"""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from embedder import embed_texts
from indexer import get_collection
from search import HybridSearch


QUERIES = [
    # Termino discriminante (deberia beneficiar a BM25)
    "Defensoria del Pueblo y del Ambiente",
    "juicio politico reglamentacion",
    "Banca del Vecino",
    # Conceptos juridicos
    "remuneracion del intendente",
    "exencion de tasas para jubilados",
    # Numero de ordenanza especifico (caso donde BM25 brilla)
    "Ordenanza 9445 tarifaria",
    # Concepto que requiere semantica
    "proteccion del medio ambiente",
    # Termino tecnico del corpus
    "GIRSU residuos solidos",
]


def desc_corto(metadata, chunk_id):
    tipo = metadata.get("tipo_documento") or metadata.get("tipo_norma", "?")
    if tipo == "carta_organica":
        art = metadata.get("articulo_num", "")
        inc = metadata.get("inciso_num")
        if inc:
            return f"CO Art.{art} inc.{inc}"
        tc = metadata.get("tipo_chunk", "")
        if tc == "preambulo":
            return "CO Preambulo"
        if tc == "disposicion_transitoria":
            return f"CO Transit.{metadata.get('transitoria_ordinal','')}"
        return f"CO Art.{art}"
    num = metadata.get("numero", "?")
    anio = metadata.get("anio", "?")
    return f"{tipo[:4].upper()} {num}/{anio}"


def buscar_pura(col, query, n=5):
    [emb] = embed_texts([query], verbose=False)
    res = col.query(query_embeddings=[emb], n_results=n)
    out = []
    for cid, meta, dist in zip(res["ids"][0], res["metadatas"][0], res["distances"][0]):
        out.append((cid, meta, 1 - dist))
    return out


def main():
    print("=" * 70)
    print("COMPARACION: BUSQUEDA PURA (embeddings) vs HIBRIDA (BM25+RRF)")
    print("=" * 70)

    col = get_collection()
    buscador = HybridSearch(verbose=True)

    for i, query in enumerate(QUERIES, 1):
        print(f"\n{'='*70}")
        print(f"[{i}/{len(QUERIES)}] QUERY: {query}")
        print("=" * 70)

        pura = buscar_pura(col, query, n=5)
        hibrida = buscador.buscar(query, n_results=5)

        print(f"\n  {'PURA (embeddings)':<35} | {'HIBRIDA (BM25+RRF)':<35}")
        print(f"  {'-'*35} | {'-'*35}")
        for rank in range(5):
            izq = ""
            if rank < len(pura):
                cid, meta, sim = pura[rank]
                izq = f"{desc_corto(meta, cid):<22} {sim:.3f}"
            der = ""
            if rank < len(hibrida):
                r = hibrida[rank]
                fuentes = ("D" if r["en_denso"] else "") + ("B" if r["en_sparse"] else "")
                der = f"{desc_corto(r['metadata'], r['chunk_id']):<22} [{fuentes}]"
            print(f"  {izq:<35} | {der:<35}")

    print(f"\n{'='*70}")
    print("Comparacion completa")
    print("=" * 70)


if __name__ == "__main__":
    main()
