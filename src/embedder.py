"""
Cliente OpenAI Embeddings para el pipeline RAG.

Modelo: text-embedding-3-small (1536 dimensiones, U$S 0.02 por 1M tokens).

Caracteristicas:
  - Lee OPENAI_API_KEY desde .env
  - Procesa en batches de hasta 100 chunks por request (limite practico)
  - Reintentos con backoff exponencial ante rate limits
  - Estima costos antes y reporta despues
"""
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    from openai import OpenAI, RateLimitError, APIError
except ImportError:
    print("ERROR: faltan paquetes. Corre:")
    print("  pip install openai python-dotenv --break-system-packages")
    sys.exit(1)

# Cargar .env desde la raiz del proyecto
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL_NAME = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # dimension fija del modelo
PRICE_PER_1M_TOKENS = 0.02  # USD
DEFAULT_BATCH_SIZE = 100
MAX_RETRIES = 5
INITIAL_BACKOFF = 2  # segundos

# Aproximacion: 1 token ~= 3.5 chars en español
CHARS_PER_TOKEN_APPROX = 3.5


def _get_client():
    """Construye el cliente OpenAI verificando que haya API key."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY no esta configurada. "
            "Verifica el archivo .env en la raiz del proyecto."
        )
    return OpenAI(api_key=api_key)


def estimar_tokens(textos: list) -> int:
    """Estimacion aproximada de tokens totales."""
    total_chars = sum(len(t) for t in textos)
    return int(total_chars / CHARS_PER_TOKEN_APPROX)


def estimar_costo_usd(textos: list) -> float:
    """Estimacion de costo en USD."""
    tokens = estimar_tokens(textos)
    return (tokens / 1_000_000) * PRICE_PER_1M_TOKENS


def embed_texts(
    textos: list,
    batch_size: int = DEFAULT_BATCH_SIZE,
    verbose: bool = True,
) -> list:
    """
    Embebe una lista de textos. Devuelve lista de vectores (1 por texto, en el mismo orden).

    Procesa en batches con reintentos automaticos ante rate limits.
    """
    if not textos:
        return []

    client = _get_client()
    total = len(textos)
    embeddings = [None] * total

    tokens_usados = 0
    inicio = time.time()

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = textos[batch_start:batch_end]

        # Reintentos con backoff exponencial
        for intento in range(1, MAX_RETRIES + 1):
            try:
                response = client.embeddings.create(
                    model=MODEL_NAME,
                    input=batch,
                )
                for i, item in enumerate(response.data):
                    embeddings[batch_start + i] = item.embedding
                tokens_usados += response.usage.total_tokens
                break
            except RateLimitError as e:
                if intento == MAX_RETRIES:
                    raise
                wait = INITIAL_BACKOFF * (2 ** (intento - 1))
                if verbose:
                    print(f"    Rate limit (intento {intento}/{MAX_RETRIES}), "
                          f"esperando {wait}s...")
                time.sleep(wait)
            except APIError as e:
                if intento == MAX_RETRIES:
                    raise
                wait = INITIAL_BACKOFF * (2 ** (intento - 1))
                if verbose:
                    print(f"    API error (intento {intento}/{MAX_RETRIES}): {e}, "
                          f"esperando {wait}s...")
                time.sleep(wait)

        if verbose:
            transcurrido = time.time() - inicio
            print(f"  Embedded {batch_end}/{total} chunks "
                  f"({transcurrido:.1f}s, {tokens_usados:,} tokens)")

    costo = (tokens_usados / 1_000_000) * PRICE_PER_1M_TOKENS

    if verbose:
        print(f"\n  TOTAL: {total} chunks, {tokens_usados:,} tokens, "
              f"U$S {costo:.4f}, {time.time() - inicio:.1f}s")

    return embeddings


if __name__ == "__main__":
    # Test minimo: 3 textos simples
    print("Test del embedder...")
    textos_test = [
        "El Concejo Deliberante sanciona ordenanzas.",
        "El Intendente Municipal es el jefe del Departamento Ejecutivo.",
        "Los vecinos tienen derecho a la salud y la educacion.",
    ]
    print(f"\nTextos de prueba: {len(textos_test)}")
    print(f"Costo estimado: U$S {estimar_costo_usd(textos_test):.6f}")
    print(f"\nEmbebiendo...")
    vectores = embed_texts(textos_test)
    print(f"\nVectores obtenidos: {len(vectores)}")
    print(f"Dimensiones del primer vector: {len(vectores[0])}")
    print(f"Primeros 5 valores del primer vector: {vectores[0][:5]}")
    print("\nOK Test pasado.")
