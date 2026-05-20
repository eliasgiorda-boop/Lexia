"""
Wrapper sobre ChromaDB para el pipeline RAG.

Caracteristicas:
  - Cliente persistente local en data/chromadb/
  - Coleccion unica "digesto_sma"
  - Sanitizacion de metadata: ChromaDB NO acepta listas/dicts, los convertimos a string
  - Idempotente: upsert si el chunk_id ya existe

USO:
  from indexer import get_collection, upsert_chunks, query

  col = get_collection()
  upsert_chunks(col, chunks_con_embeddings)
  resultados = query(col, "que dice sobre juicio politico", n_results=5)
"""
import json
import sys
from pathlib import Path

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    print("ERROR: chromadb no esta instalado. Corre:")
    print("  pip install chromadb --break-system-packages")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent.parent
CHROMADB_DIR = PROJECT_ROOT / "data" / "chromadb"
COLLECTION_NAME = "digesto_sma"


def get_client():
    """Cliente persistente local."""
    CHROMADB_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(CHROMADB_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def get_collection(name: str = COLLECTION_NAME):
    """
    Obtiene o crea la coleccion.
    metadata={"hnsw:space": "cosine"} para similitud coseno (estandar en embeddings).
    """
    client = get_client()
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def sanitizar_metadata(metadata: dict) -> dict:
    """
    ChromaDB acepta solo str, int, float, bool en metadata.
    Convertimos listas a string JSON y filtramos None.
    """
    sanitizada = {}
    for k, v in metadata.items():
        if v is None:
            continue  # ChromaDB no acepta None
        if isinstance(v, (str, int, float, bool)):
            sanitizada[k] = v
        elif isinstance(v, list):
            # Listas se serializan como JSON string
            if not v:  # lista vacia
                sanitizada[k] = ""
            else:
                sanitizada[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, dict):
            sanitizada[k] = json.dumps(v, ensure_ascii=False)
        else:
            # Fallback: convertir a string
            sanitizada[k] = str(v)
    return sanitizada


def upsert_chunks(collection, chunks_con_embeddings: list, verbose: bool = True):
    """
    Inserta o actualiza chunks en la coleccion.

    Cada elemento de chunks_con_embeddings debe tener:
      - chunk_id (str)
      - texto (str)
      - metadata (dict)
      - embedding (list[float])
    """
    if not chunks_con_embeddings:
        return 0

    ids = []
    documents = []
    metadatas = []
    embeddings = []

    for c in chunks_con_embeddings:
        ids.append(c["chunk_id"])
        documents.append(c["texto"])
        metadatas.append(sanitizar_metadata(c["metadata"]))
        embeddings.append(c["embedding"])

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    if verbose:
        total = collection.count()
        print(f"  Upserted {len(ids)} chunks. Total en coleccion: {total}")

    return len(ids)


def query(collection, texto_query: str, embedding_query: list = None,
          n_results: int = 5, filtros: dict = None):
    """
    Busca chunks similares al query.

    - Si pasas embedding_query, usa busqueda vectorial directa (mas eficiente).
    - Si no, ChromaDB embebe el texto_query con su modelo default (NO usar, queremos OpenAI consistente).
    - filtros es un dict opcional para filtrar por metadata (ej {"tipo_documento": "carta_organica"}).
    """
    kwargs = {"n_results": n_results}

    if embedding_query is not None:
        kwargs["query_embeddings"] = [embedding_query]
    else:
        kwargs["query_texts"] = [texto_query]

    if filtros:
        kwargs["where"] = filtros

    return collection.query(**kwargs)


def listar_colecciones():
    """Util para debug."""
    client = get_client()
    return client.list_collections()


if __name__ == "__main__":
    print("Test del indexer...")
    col = get_collection()
    print(f"Coleccion: {col.name}")
    print(f"Chunks actuales: {col.count()}")
    print(f"DB persistente en: {CHROMADB_DIR}")
    print("\nOK indexer funcional.")
