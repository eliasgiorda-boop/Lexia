"""
Capa 2 del pipeline RAG: sintesis con LLM (GPT-4.1) sobre los chunks
recuperados por la busqueda hibrida de dos fases.

PRINCIPIO DE DISEÑO (producto legal):
  Las CITAS las construye Python de forma deterministica a partir de la
  metadata real de cada chunk. El LLM solo referencia por numero [1][2].
  Esto elimina la alucinacion de numeros de norma/articulo, que en un
  producto juridico es inaceptable.

FLUJO:
  1. buscar_dos_fases(query) -> top N chunks (CO + corpus)
  2. construir_contexto_numerado(chunks) -> bloque [1]..[N] para el LLM
  3. LLM (GPT-4.1) sintetiza citando por numero
  4. construir_fuentes(chunks) -> lista deterministica con citas + links
  5. Se devuelve {respuesta, fuentes, query, modelo, uso}

FORMATO DE SALIDA: estructurado
  - Respuesta directa
  - Normas aplicables (citadas por [n])
  - Relaciones inferidas (marcadas como inferencia)
  - Disclaimer legal
  + lista de fuentes con links (construida por Python)

USO:
  from llm_answer import responder
  from search import HybridSearch

  buscador = HybridSearch()
  res = responder("como se regula la Banca del Vecino?", buscador)
  print(res["respuesta"])
  for f in res["fuentes"]:
      print(f["numero"], f["cita"], f["link"])
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

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

MODEL_NAME = "gpt-4.1"
PRICE_INPUT_PER_1M = 2.00    # USD por 1M tokens input (verificado may-2026)
PRICE_OUTPUT_PER_1M = 8.00   # USD por 1M tokens output
MAX_RETRIES = 4
INITIAL_BACKOFF = 2
N_CHUNKS_DEFAULT = 12        # cuantos chunks pasar al LLM como contexto


def _get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta OPENAI_API_KEY en .env")
    return OpenAI(api_key=api_key)


# ----------------------------------------------------------------------
# Construccion deterministica de citas y links (NO la hace el LLM)
# ----------------------------------------------------------------------

def _normalizar_tipo(metadata: dict) -> str:
    """Devuelve el tipo de norma, manejando los dos esquemas de metadata."""
    return (metadata.get("tipo_documento")
            or metadata.get("tipo_norma")
            or "norma")


def _derivar_link(metadata: dict) -> dict:
    """
    Construye el link al original. Maneja los dos esquemas:
      - Corpus: link_original_url / url_origen
      - CO: no tiene link externo (PDF local)
    Devuelve {url, tipo}.
    """
    tipo_doc = _normalizar_tipo(metadata)
    if tipo_doc == "carta_organica":
        return {"url": "", "tipo": "carta_organica_local"}

    # Preferir link_original_url, caer a url_origen
    url = metadata.get("link_original_url") or metadata.get("url_origen") or ""
    tipo_link = metadata.get("link_original_tipo")
    if not tipo_link:
        if "digesto.cdsma.gob.ar/normas/" in url:
            tipo_link = "pdf_nuevo"
        elif "digeh.cdsma.gob.ar" in url:
            tipo_link = "html_viejo"
        else:
            tipo_link = "sin_link"
    return {"url": url, "tipo": tipo_link}


def construir_cita(metadata: dict) -> str:
    """
    Construye la cita textual de un chunk a partir de su metadata.
    Maneja CO, ordenanzas/resoluciones/comunicaciones y anexos.
    Determinista: nunca inventa numeros.
    """
    tipo_doc = _normalizar_tipo(metadata)
    tipo_chunk = metadata.get("tipo_chunk", "")

    # --- Carta Organica ---
    if tipo_doc == "carta_organica":
        art = metadata.get("articulo_num")
        if tipo_chunk == "preambulo":
            return "Carta Orgánica Municipal, Preámbulo"
        if tipo_chunk == "disposicion_transitoria":
            ordinal = metadata.get("transitoria_ordinal", "")
            return f"Carta Orgánica Municipal, Disposición Transitoria {ordinal}".strip()
        inc = metadata.get("inciso_num")
        if inc is not None and metadata.get("es_inciso"):
            return f"Carta Orgánica Municipal, Art. {art}, inc. {inc}"
        return f"Carta Orgánica Municipal, Art. {art}"

    # --- Corpus: ordenanza / resolucion / comunicacion ---
    label_tipo = {
        "ordenanza": "Ordenanza",
        "resolucion": "Resolución",
        "comunicacion": "Comunicación",
    }.get(tipo_doc, tipo_doc.capitalize())

    numero = metadata.get("numero", "?")
    anio = metadata.get("anio", "?")
    cita = f"{label_tipo} N° {numero}/{anio}"

    # Anexo (vacio o con contenido)
    anexo_num = metadata.get("anexo_num")
    if anexo_num:
        cita += f", Anexo {anexo_num}"
        if metadata.get("es_anexo_vacio"):
            cita += " (no digitalizado)"

    # Articulo
    art = metadata.get("articulo_num")
    if art is not None:
        cita += f", Art. {art}"

    # Titulo corto descriptivo
    titulo = metadata.get("titulo_corto")
    if titulo:
        cita += f' — "{titulo}"'

    # Marca de derogada
    if metadata.get("es_derogada_por_filename"):
        cita += " [DEROGADA]"

    return cita


def construir_fuentes(chunks: list) -> list:
    """
    Construye la lista deterministica de fuentes numeradas [1]..[N]
    a partir de los chunks recuperados. Cada fuente trae cita + link.
    """
    fuentes = []
    for i, ch in enumerate(chunks, start=1):
        meta = ch["metadata"]
        link = _derivar_link(meta)
        fuentes.append({
            "numero": i,
            "chunk_id": ch["chunk_id"],
            "cita": construir_cita(meta),
            "link": link["url"],
            "link_tipo": link["tipo"],
            "tipo_documento": _normalizar_tipo(meta),
            "es_derogada": meta.get("es_derogada_por_filename", False),
            "fuente_fase": ch.get("fuente_fase", ""),
        })
    return fuentes


def construir_contexto_numerado(chunks: list) -> str:
    """
    Arma el bloque de contexto que recibe el LLM, numerado [1]..[N].
    El LLM citara por estos numeros; las citas reales las arma Python.
    """
    bloques = []
    for i, ch in enumerate(chunks, start=1):
        cita = construir_cita(ch["metadata"])
        texto = ch["texto"].strip()
        bloques.append(f"[{i}] {cita}\n{texto}")
    return "\n\n".join(bloques)


# ----------------------------------------------------------------------
# Prompt del sistema (asistente legal)
# ----------------------------------------------------------------------

PROMPT_SISTEMA = """\
Eres un asistente de investigación jurídica especializado en la normativa \
municipal de San Martín de los Andes (Provincia del Neuquén, Argentina). \
NO eres un abogado: eres una herramienta profesional de búsqueda y análisis \
normativo (similar a Westlaw o La Ley) que ayuda a profesionales del derecho \
a encontrar, conectar y comprender la normativa aplicable. El profesional \
mantiene siempre el juicio interpretativo.

REGLAS DE FIDELIDAD (obligatorias):
1. Responde ÚNICAMENTE con base en los fragmentos normativos provistos en el \
contexto. Nunca inventes artículos, números de norma ni contenido normativo.
2. Si la información necesaria no está en los fragmentos, dilo explícitamente: \
"No encuentro normativa específica sobre esto en los documentos disponibles."
3. Cada afirmación sobre el contenido de una norma debe referenciar su fuente \
mediante el número entre corchetes del fragmento, por ejemplo [1], [2].
4. NO escribas tú los números de ordenanza ni de artículo en las citas: solo \
usa las referencias numéricas [n]. El sistema construye las citas formales.

TRAZABILIDAD (distinguir hecho de inferencia):
5. Distingue siempre entre lo que la norma dice textualmente y lo que es una \
inferencia o conexión que tú estableces. Marca las inferencias con el prefijo \
"Relación inferida:".
6. Puedes señalar relaciones evidentes (p. ej., que una ordenanza reglamenta un \
artículo de la Carta Orgánica), pero siempre marcándolas como inferencia.

JERARQUÍA NORMATIVA:
7. La Carta Orgánica Municipal es la norma de máxima jerarquía local: prevalece \
sobre ordenanzas, resoluciones y comunicaciones. Tenlo presente al analizar.
8. Si un fragmento está marcado como [DEROGADA], adviértelo explícitamente.

FORMATO DE RESPUESTA (estructurado, en español rioplatense formal):
Usa estas secciones, omitiendo las que no apliquen:

**Respuesta**
(Síntesis directa de la consulta, con referencias [n].)

**Normas aplicables**
(Lista de las normas relevantes con su referencia [n] y una frase de qué aporta cada una.)

**Relaciones inferidas**
(Solo si estableces conexiones entre normas. Cada una marcada como inferencia.)

No incluyas un disclaimer al final: el sistema lo agrega automáticamente. \
No inventes una sección de fuentes: el sistema la genera.
"""


def responder(query: str, buscador, n_chunks: int = N_CHUNKS_DEFAULT,
              filtros_extra: dict = None, verbose: bool = False):
    """
    Pipeline completo de la Capa 2: busca + sintetiza con LLM + arma fuentes.

    Devuelve dict:
      {
        "query": str,
        "respuesta": str,           # texto del LLM (estructurado)
        "fuentes": list,            # citas deterministicas con links
        "modelo": str,
        "uso": {tokens_in, tokens_out, costo_usd},
        "chunks_usados": int,
      }
    """
    # 1. Recuperar chunks con la busqueda de dos fases
    chunks = buscador.buscar_dos_fases(query, n_results=n_chunks,
                                       filtros_extra=filtros_extra)

    if not chunks:
        return {
            "query": query,
            "respuesta": "No encuentro normativa relacionada con esta consulta "
                         "en los documentos disponibles.",
            "fuentes": [],
            "modelo": MODEL_NAME,
            "uso": {"tokens_in": 0, "tokens_out": 0, "costo_usd": 0.0},
            "chunks_usados": 0,
        }

    # 2. Construir contexto numerado para el LLM
    contexto = construir_contexto_numerado(chunks)

    # 3. Construir fuentes deterministicas (Python, no el LLM)
    fuentes = construir_fuentes(chunks)

    # 4. Llamar al LLM
    mensaje_usuario = (
        f"CONSULTA DEL PROFESIONAL:\n{query}\n\n"
        f"FRAGMENTOS NORMATIVOS DISPONIBLES:\n{contexto}"
    )

    client = _get_client()
    respuesta_texto = None
    uso = {"tokens_in": 0, "tokens_out": 0, "costo_usd": 0.0}

    for intento in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": PROMPT_SISTEMA},
                    {"role": "user", "content": mensaje_usuario},
                ],
                temperature=0.2,  # baja: priorizamos fidelidad sobre creatividad
            )
            respuesta_texto = resp.choices[0].message.content
            tin = resp.usage.prompt_tokens
            tout = resp.usage.completion_tokens
            costo = (tin / 1e6 * PRICE_INPUT_PER_1M +
                     tout / 1e6 * PRICE_OUTPUT_PER_1M)
            uso = {"tokens_in": tin, "tokens_out": tout,
                   "costo_usd": round(costo, 5)}
            break
        except (RateLimitError, APIError) as e:
            espera = INITIAL_BACKOFF * (2 ** intento)
            if verbose:
                print(f"  [reintento {intento+1}/{MAX_RETRIES}] {e} "
                      f"-> espero {espera}s")
            time.sleep(espera)
    else:
        respuesta_texto = ("Error: no se pudo obtener respuesta del modelo "
                           "tras varios reintentos.")

    # 5. Disclaimer legal (agregado por el sistema, no por el LLM)
    disclaimer = (
        "\n\n---\n"
        "*Esta respuesta fue generada automáticamente con fines orientativos "
        "y no constituye asesoramiento legal. La normativa puede haber sido "
        "modificada o derogada con posterioridad. Verifique siempre contra el "
        "texto oficial vigente antes de tomar decisiones jurídicas.*"
    )
    respuesta_final = (respuesta_texto or "") + disclaimer

    return {
        "query": query,
        "respuesta": respuesta_final,
        "fuentes": fuentes,
        "modelo": MODEL_NAME,
        "uso": uso,
        "chunks_usados": len(chunks),
    }


if __name__ == "__main__":
    from search import HybridSearch

    print("Test de la Capa 2 (LLM con RAG)...\n")
    buscador = HybridSearch()

    query = "¿Cómo se regula la Banca del Vecino?"
    print(f"CONSULTA: {query}\n")
    print("=" * 70)

    res = responder(query, buscador, verbose=True)

    print(res["respuesta"])
    print("\n" + "=" * 70)
    print("FUENTES (construidas por Python, deterministicas):")
    print("=" * 70)
    for f in res["fuentes"]:
        link = f["link"] if f["link"] else "(Carta Orgánica - documento local)"
        print(f"  [{f['numero']}] {f['cita']}")
        print(f"       {link}")

    print("\n" + "=" * 70)
    u = res["uso"]
    print(f"Modelo: {res['modelo']} | chunks: {res['chunks_usados']} | "
          f"tokens in/out: {u['tokens_in']}/{u['tokens_out']} | "
          f"costo: U$S {u['costo_usd']}")
