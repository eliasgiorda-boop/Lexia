"""
Validacion EXTENDIDA del indice con queries adversariales.

A diferencia de test_queries_co.py (que son las 10 queries "buenas"),
este script ataca casos limites:
  - Queries ambiguas
  - Queries con sinonimos no obvios
  - Queries muy genericas
  - Queries que mezclan varios temas
  - Queries que requieren saber sobre transitorias / preambulo
  - Queries en estilo coloquial vs juridico formal

OBJETIVO: encontrar debilidades antes de escalar al corpus completo.

USO:
  python scripts/test_queries_adversarial.py
"""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from embedder import embed_texts
from indexer import get_collection


QUERIES_ADVERSARIALES = [
    # 1. Pregunta coloquial vs articulo formal
    ("¿Pueden los extranjeros votar en San Martin?", "esperado: Art. 196 (Cuerpo Electoral incluye extranjeros)"),

    # 2. Sinonimo no obvio (tributos = impuestos)
    ("¿La municipalidad puede cobrar impuestos?", "esperado: Art. 87 (recursos propios, facultad tributaria)"),

    # 3. Tema MUY transversal (mujer aparece en varios articulos)
    ("Igualdad de genero", "esperado: alguno de los arts 8 inc 19 o 19 (familia y mujer)"),

    # 4. Articulo blindado pero NO sobre remuneraciones
    ("Consejo de Planificacion Estrategica", "esperado: Art. 160 [BLINDADO]"),

    # 5. Pregunta sobre el Preambulo (chunk especial)
    ("Que valores inspiran la Carta Organica?", "esperado: PREAMBULO"),

    # 6. Pregunta sobre transitoria especifica
    ("Cuando entra en vigencia la Carta Organica", "esperado: Transitoria Primera"),

    # 7. Pregunta con vocabulario popular (mapuches)
    ("Pueblos originarios mapuche", "esperado: Art. 8 inc 12 (preexistencia etnica Pueblo Mapuche)"),

    # 8. Pregunta sobre algo CONTRARIO al sentido literal
    ("¿Quien NO puede ser concejal?", "esperado: Art. 38 (inhabilidades)"),

    # 9. Pregunta multipalabra con varios conceptos
    ("Procedimiento de veto de ordenanzas por el Intendente", "esperado: Art. 55 (veto)"),

    # 10. Pregunta sobre un instituto especifico
    ("¿Que es la Banca del Vecino?", "esperado: Art. 193"),

    # 11. Bonus: pregunta sobre Cerro Chapelco (super especifica)
    ("Cerro Chapelco patrimonio municipal", "esperado: Art. 8 inc 23 (Cerro Chapelco)"),

    # 12. Bonus: pregunta de procedimiento administrativo
    ("Que pasa si el Intendente no convoca a elecciones?", "esperado: Art. 45 inc 13 (Concejo puede convocar)"),
]


def describir_chunk(chunk_id: str, metadata: dict) -> str:
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


def preview_texto(texto: str, max_len: int = 200) -> str:
    if "]\n\n" in texto:
        cuerpo = texto.split("]\n\n", 1)[1]
    else:
        cuerpo = texto
    cuerpo = " ".join(cuerpo.split())
    if len(cuerpo) <= max_len:
        return cuerpo
    return cuerpo[:max_len] + "..."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    print("=" * 70)
    print("VALIDACION ADVERSARIAL - QUERIES DE ESTRES SOBRE CARTA ORGANICA")
    print("=" * 70)

    col = get_collection()
    print(f"Coleccion: {col.name} | Chunks: {col.count()}")

    for i, (query, esperado) in enumerate(QUERIES_ADVERSARIALES, 1):
        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(QUERIES_ADVERSARIALES)}] QUERY: {query}")
        print(f"            ({esperado})")
        print("=" * 70)

        [query_emb] = embed_texts([query], verbose=False)
        res = col.query(query_embeddings=[query_emb], n_results=args.top)

        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        distances = res["distances"][0]

        for rank, (chunk_id, doc, meta, dist) in enumerate(
            zip(ids, docs, metas, distances), 1
        ):
            similitud = 1 - dist
            print(f"\n  #{rank} | similitud: {similitud:.3f} | {describir_chunk(chunk_id, meta)}")
            print(f"      {preview_texto(doc)}")

    print(f"\n{'=' * 70}")
    print(f"Validacion adversarial completa: {len(QUERIES_ADVERSARIALES)} queries")
    print("=" * 70)


if __name__ == "__main__":
    main()
