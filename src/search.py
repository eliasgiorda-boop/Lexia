r"""
Busqueda hibrida para el Digesto Municipal: BM25 (keyword) + embeddings
(semantico), fusionados con Reciprocal Rank Fusion (RRF).

POR QUE HIBRIDA:
  La busqueda puramente semantica (embeddings) sufre dilucion con corpus
  grandes: en 20.977 chunks, terminos especificos quedan enterrados bajo
  documentos que comparten vocabulario generico.
  BM25 rescata esos casos detectando match exacto de palabras clave.
  RRF combina ambos rankings sin calibrar pesos arbitrarios.

DOS FASES (buscar_dos_fases):
  La Carta Organica (391 chunks, articulos cortos y densos) compite en
  desventaja contra ordenanzas largas que repiten terminos. El BM25
  penaliza los chunks cortos de la CO.

  Solucion: buscar por separado en CO y en corpus, y fusionar SOLO si
  la CO es realmente relevante. La relevancia se mide con similitud
  coseno cruda (señal absoluta), no con RRF (señal relativa que siempre
  da un "mejor de la CO" aunque sea irrelevante).

  Umbral calibrado empiricamente: coseno >= 0.46 separa limpio queries
  donde la CO aporta (medio ambiente: 0.57, juicio politico: 0.50) de
  donde no (taxis: 0.35, cementerio: 0.42).

ARQUITECTURA buscar() (hibrida simple):
  query --> embeddings (cosine) --> top K_DENSE
       \--> BM25 (keyword)       --> top K_SPARSE
              \--> RRF --> top N

ARQUITECTURA buscar_dos_fases():
  query --> Fase corpus (hibrida sin CO)  --> ranking_corpus
       \--> Fase CO (hibrida solo CO)     --> ranking_co + coseno_top_co
              \--> si coseno_top_co >= UMBRAL: fusionar (RRF global)
                   si no: devolver solo corpus

USO:
  from search import HybridSearch
  buscador = HybridSearch()

  # Hibrida simple
  resultados = buscador.buscar("Banca del Vecino", n_results=10)

  # Dos fases (recomendada para producción)
  resultados = buscador.buscar_dos_fases("proteccion del medio ambiente", n_results=10)

  for r in resultados:
      print(r["chunk_id"], r["score_rrf"], r["metadata"]["tipo_documento"])
"""
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    print("ERROR: falta rank-bm25. Corre: pip install rank-bm25 --break-system-packages")
    raise

from embedder import embed_texts
from indexer import get_collection


# Parametros de la busqueda hibrida
K_DENSE = 50       # candidatos del retriever semantico
K_SPARSE = 50      # candidatos del retriever BM25
RRF_K = 60         # constante de RRF (estandar en la literatura: 60)

# Parametros de la busqueda de dos fases
TIPO_DOC_CO = "carta_organica"
UMBRAL_CO_RELEVANTE = 0.46   # coseno minimo para que la CO entre al resultado
                             # (calibrado: relevante>=0.50, irrelevante<=0.43)
K_FASE_CO = 20               # candidatos a traer de la CO en fase 1
K_FASE_CORPUS = 50           # candidatos a traer del corpus en fase 2


def _tokenizar(texto: str) -> list:
    """
    Tokenizador simple para BM25, adaptado a textos legales en español.
    Minusculas, palabras alfanumericas (con acentos), descarta tokens de 1 char.
    """
    texto = texto.lower()
    tokens = re.findall(r"\w+", texto, re.UNICODE)
    return [t for t in tokens if len(t) > 1]


class HybridSearch:
    """
    Buscador hibrido sobre la coleccion ChromaDB.
    Combina BM25 (en memoria) con la busqueda vectorial de ChromaDB via RRF.
    Ofrece busqueda hibrida simple (buscar) y de dos fases (buscar_dos_fases).
    """

    def __init__(self, verbose: bool = True):
        self.col = get_collection()
        self.verbose = verbose
        self._construir_indice_bm25()

    def _construir_indice_bm25(self):
        """Trae todos los documentos y construye el indice BM25 en memoria."""
        t0 = time.time()
        datos = self.col.get(include=["documents", "metadatas"])
        self.ids = datos["ids"]
        self.documentos = datos["documents"]
        self.metadatas = datos["metadatas"]
        self.id_a_idx = {cid: i for i, cid in enumerate(self.ids)}
        corpus_tokenizado = [_tokenizar(doc) for doc in self.documentos]
        self.bm25 = BM25Okapi(corpus_tokenizado)
        if self.verbose:
            t = time.time() - t0
            print(f"[HybridSearch] Indice BM25 construido: "
                  f"{len(self.ids):,} docs en {t:.1f}s")

    # ---- Retrievers individuales ----

    def _buscar_denso(self, query: str, k: int, filtros: dict = None,
                      emb=None):
        """
        Busqueda semantica via embeddings.
        Devuelve (lista de (chunk_id, rank), dict chunk_id->similitud_coseno).
        Acepta emb pre-calculado para no re-embeddear la misma query.
        """
        if emb is None:
            [emb] = embed_texts([query], verbose=False)
        kwargs = {"query_embeddings": [emb], "n_results": k}
        if filtros:
            kwargs["where"] = filtros
        res = self.col.query(**kwargs)
        ids = res["ids"][0]
        distancias = res["distances"][0]
        ranking = [(cid, rank) for rank, cid in enumerate(ids, start=1)]
        similitudes = {cid: 1 - dist for cid, dist in zip(ids, distancias)}
        return ranking, similitudes

    def _buscar_sparse(self, query: str, k: int, filtros: dict = None):
        """Busqueda BM25. Devuelve lista de (chunk_id, rank)."""
        tokens_query = _tokenizar(query)
        scores = self.bm25.get_scores(tokens_query)
        indices_ordenados = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )
        resultados = []
        rank = 1
        for idx in indices_ordenados:
            if scores[idx] <= 0:
                break
            if filtros:
                meta = self.metadatas[idx]
                if not all(meta.get(kf) == vf for kf, vf in filtros.items()):
                    continue
            resultados.append((self.ids[idx], rank))
            rank += 1
            if len(resultados) >= k:
                break
        return resultados

    def _fusion_rrf(self, *rankings):
        """
        Reciprocal Rank Fusion de uno o mas rankings.
        score_rrf(d) = sum sobre cada ranking de 1 / (RRF_K + rank_d)
        """
        scores = {}
        for ranking in rankings:
            for cid, rank in ranking:
                scores[cid] = scores.get(cid, 0) + 1.0 / (RRF_K + rank)
        return scores

    def _armar_resultados(self, ids_ordenados, scores_rrf, ids_denso,
                          ids_sparse, n_results, similitudes=None):
        """Construye la lista de dicts de resultado a partir de ids ordenados."""
        resultados = []
        for cid in ids_ordenados[:n_results]:
            idx = self.id_a_idx.get(cid)
            if idx is None:
                continue
            r = {
                "chunk_id": cid,
                "texto": self.documentos[idx],
                "metadata": self.metadatas[idx],
                "score_rrf": scores_rrf.get(cid, 0.0),
                "en_denso": cid in ids_denso,
                "en_sparse": cid in ids_sparse,
            }
            if similitudes is not None:
                r["similitud_coseno"] = similitudes.get(cid)
            resultados.append(r)
        return resultados

    # ---- Busqueda hibrida simple ----

    def buscar(self, query: str, n_results: int = 10, filtros: dict = None,
               emb=None):
        """
        Busqueda hibrida (BM25 + embeddings + RRF) en una sola pasada.
        Devuelve lista de dicts ordenados por score RRF descendente.
        """
        ranking_denso, similitudes = self._buscar_denso(
            query, K_DENSE, filtros, emb=emb)
        ranking_sparse = self._buscar_sparse(query, K_SPARSE, filtros)
        ids_denso = {cid for cid, _ in ranking_denso}
        ids_sparse = {cid for cid, _ in ranking_sparse}
        scores_rrf = self._fusion_rrf(ranking_denso, ranking_sparse)
        ids_ordenados = sorted(scores_rrf.keys(),
                               key=lambda c: scores_rrf[c], reverse=True)
        return self._armar_resultados(
            ids_ordenados, scores_rrf, ids_denso, ids_sparse,
            n_results, similitudes)

    # ---- Busqueda de dos fases ----

    def buscar_dos_fases(self, query: str, n_results: int = 10,
                         filtros_extra: dict = None):
        """
        Busqueda de dos fases: corpus + Carta Organica con fusion condicional.

        Fase 1: hibrida sobre el corpus (todo menos CO).
        Fase 2: hibrida solo sobre la CO + medicion de relevancia (coseno).
        Fusion: si el mejor chunk CO supera UMBRAL_CO_RELEVANTE, se fusionan
                ambos rankings (RRF global). Si no, se devuelve solo el corpus.

        Esto evita que articulos cortos de la CO queden enterrados cuando son
        relevantes, sin forzar la CO cuando no aporta.

        filtros_extra: filtros adicionales de metadata (ej: por anio) que se
        aplican a la fase corpus.

        Cada resultado incluye "fuente_fase": 'corpus' o 'carta_organica'.
        """
        # Embeddear la query UNA sola vez y reutilizar en ambas fases
        [emb] = embed_texts([query], verbose=False)

        # --- Fase 2 primero: medir relevancia de la CO ---
        filtros_co = {"tipo_documento": TIPO_DOC_CO}
        ranking_co_denso, sims_co = self._buscar_denso(
            query, K_FASE_CO, filtros_co, emb=emb)
        ranking_co_sparse = self._buscar_sparse(query, K_FASE_CO, filtros_co)

        # Relevancia de la CO = mejor similitud coseno entre sus chunks
        mejor_coseno_co = max(sims_co.values()) if sims_co else 0.0
        co_es_relevante = mejor_coseno_co >= UMBRAL_CO_RELEVANTE

        # --- Fase 1: corpus (todo menos CO) ---
        # ChromaDB where con $ne para excluir la CO
        filtros_corpus = {"tipo_documento": {"$ne": TIPO_DOC_CO}}
        if filtros_extra:
            filtros_corpus = {"$and": [
                {"tipo_documento": {"$ne": TIPO_DOC_CO}},
                filtros_extra,
            ]}
        ranking_corpus_denso, sims_corpus = self._buscar_denso(
            query, K_FASE_CORPUS, filtros_corpus, emb=emb)
        ranking_corpus_sparse = self._buscar_sparse(
            query, K_FASE_CORPUS, None)
        # Filtrar sparse del corpus para excluir CO (BM25 no soporta $ne)
        ranking_corpus_sparse = [
            (cid, rank) for cid, rank in ranking_corpus_sparse
            if self.metadatas[self.id_a_idx[cid]].get("tipo_documento") != TIPO_DOC_CO
        ]
        # Re-numerar ranks tras el filtrado
        ranking_corpus_sparse = [
            (cid, i) for i, (cid, _) in enumerate(ranking_corpus_sparse, start=1)
        ]

        # --- Fusion ---
        similitudes_todas = {**sims_corpus, **sims_co}
        if co_es_relevante:
            scores_rrf = self._fusion_rrf(
                ranking_corpus_denso, ranking_corpus_sparse,
                ranking_co_denso, ranking_co_sparse)
            ids_denso = ({c for c, _ in ranking_corpus_denso} |
                         {c for c, _ in ranking_co_denso})
            ids_sparse = ({c for c, _ in ranking_corpus_sparse} |
                          {c for c, _ in ranking_co_sparse})
        else:
            scores_rrf = self._fusion_rrf(
                ranking_corpus_denso, ranking_corpus_sparse)
            ids_denso = {c for c, _ in ranking_corpus_denso}
            ids_sparse = {c for c, _ in ranking_corpus_sparse}

        ids_ordenados = sorted(scores_rrf.keys(),
                               key=lambda c: scores_rrf[c], reverse=True)
        resultados = self._armar_resultados(
            ids_ordenados, scores_rrf, ids_denso, ids_sparse,
            n_results, similitudes_todas)

        # Anotar fuente de fase y metadata de la decision
        for r in resultados:
            tipo = r["metadata"].get("tipo_documento")
            r["fuente_fase"] = "carta_organica" if tipo == TIPO_DOC_CO else "corpus"
        # Adjuntar info de la decision en el primer resultado (debug)
        if resultados:
            resultados[0]["_debug_co"] = {
                "mejor_coseno_co": round(mejor_coseno_co, 4),
                "co_es_relevante": co_es_relevante,
                "umbral": UMBRAL_CO_RELEVANTE,
            }
        return resultados


if __name__ == "__main__":
    print("Test del modulo de busqueda (hibrida simple + dos fases)...\n")
    buscador = HybridSearch()

    queries_test = [
        "proteccion del medio ambiente",   # CO relevante -> debe traer CO
        "tarifa de taxis y remises",       # CO irrelevante -> solo corpus
        "remuneracion del intendente",     # CO muy relevante
        "GIRSU residuos solidos",          # corpus puro
    ]

    for q in queries_test:
        print(f"\n{'='*64}")
        print(f"QUERY: {q}")
        print('='*64)
        resultados = buscador.buscar_dos_fases(q, n_results=6)
        if resultados and "_debug_co" in resultados[0]:
            d = resultados[0]["_debug_co"]
            estado = "RELEVANTE" if d["co_es_relevante"] else "irrelevante"
            print(f"  [CO {estado}: mejor coseno {d['mejor_coseno_co']} "
                  f"(umbral {d['umbral']})]")
        for rank, r in enumerate(resultados, 1):
            fuentes = ("D" if r["en_denso"] else "") + ("B" if r["en_sparse"] else "")
            fase = "CO " if r["fuente_fase"] == "carta_organica" else "COR"
            print(f"  #{rank} rrf={r['score_rrf']:.4f} [{fuentes:2}] {fase} {r['chunk_id']}")
