"""
Capa API del pipeline RAG (digesto-search).

Expone el motor de busqueda hibrida + RAG via HTTP con FastAPI.
Desacopla la logica de negocio (search.py / llm_answer.py) de cualquier UI.

Endpoints:
  GET  /health     -> chequeo de vida
  POST /buscar     -> solo busqueda hibrida
  POST /responder  -> busqueda + RAG completo

El indice BM25 se construye UNA sola vez al arrancar (lifespan).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from search import HybridSearch
from llm_answer import responder

_estado = {"buscador": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[API] Inicializando HybridSearch...")
    _estado["buscador"] = HybridSearch(verbose=True)
    print("[API] Listo. Motor cargado en memoria.")
    yield
    _estado["buscador"] = None
    print("[API] Apagada.")


app = FastAPI(
    title="Digesto Search API",
    description="Busqueda hibrida + RAG sobre normativa municipal de San Martin de los Andes.",
    version="0.1.0",
    lifespan=lifespan,
)


class BuscarRequest(BaseModel):
    query: str = Field(..., min_length=2)
    n_results: int = Field(10, ge=1, le=50)
    dos_fases: bool = Field(False)


class ResponderRequest(BaseModel):
    query: str = Field(..., min_length=2)
    n_chunks: int = Field(12, ge=1, le=30)
    dos_fases: bool = Field(False)


class ChunkResultado(BaseModel):
    chunk_id: str
    texto: str
    metadata: dict
    score_rrf: float
    en_denso: bool
    en_sparse: bool
    similitud_coseno: float | None = None


class BuscarResponse(BaseModel):
    query: str
    n_resultados: int
    resultados: list[ChunkResultado]


class Uso(BaseModel):
    tokens_in: int
    tokens_out: int
    costo_usd: float


class ResponderResponse(BaseModel):
    query: str
    respuesta: str
    fuentes: list[dict]
    modelo: str
    uso: Uso
    chunks_usados: int


def _get_buscador():
    b = _estado["buscador"]
    if b is None:
        raise HTTPException(status_code=503, detail="Motor no inicializado.")
    return b


@app.get("/health")
def health():
    listo = _estado["buscador"] is not None
    return {"status": "ok" if listo else "cargando", "motor_listo": listo}


@app.post("/buscar", response_model=BuscarResponse)
def buscar_endpoint(req: BuscarRequest):
    buscador = _get_buscador()
    if req.dos_fases:
        resultados = buscador.buscar_dos_fases(req.query, n_results=req.n_results)
    else:
        resultados = buscador.buscar(req.query, n_results=req.n_results)
    return {"query": req.query, "n_resultados": len(resultados), "resultados": resultados}


@app.post("/responder", response_model=ResponderResponse)
def responder_endpoint(req: ResponderRequest):
    buscador = _get_buscador()
    try:
        resultado = responder(req.query, buscador, n_chunks=req.n_chunks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")
    return resultado