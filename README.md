# Digesto Search

Buscador semantico sobre la normativa municipal de San Martin de los Andes.

## Stack

- Python 3.12
- OpenAI API (embeddings: `text-embedding-3-small`)
- ChromaDB (base vectorial local)

## Setup

1. Clonar el repo
2. Crear venv: `py -3.12 -m venv venv`
3. Activar venv: `.\venv\Scripts\Activate.ps1`
4. Instalar deps: `pip install -r requirements.txt`
5. Copiar `.env.example` a `.env` y completar valores
6. Correr piloto: `python scripts/select_pilot.py`

## Estado

- [x] Setup inicial
- [ ] Parser del Digesto
- [ ] Parser de la Carta Organica
- [ ] Chunker condicional
- [ ] Embedder + carga a ChromaDB
- [ ] Validacion de calidad del piloto
- [ ] Indexado del corpus completo
- [ ] API de busqueda
- [ ] Frontend
