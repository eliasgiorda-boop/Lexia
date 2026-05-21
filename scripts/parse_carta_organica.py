"""
Parser de la Carta Organica Municipal de San Martin de los Andes (Julio 2010).

Diferencias clave respecto al parser de ordenanzas/resoluciones:

  - Estructura jerarquica fija: PARTE > TITULO > CAPITULO > SECCION? > ARTICULO
  - 218 articulos numerados consecutivos + 12 disposiciones transitorias + preambulo
  - 3 articulos blindados (no modificables por enmienda): 41, 68, y el cap III del Titulo IX (160-162)
  - El indice al final del documento NO se chunkea (es navegacion, no contenido)

ESTRATEGIA DE CHUNKING:
  Granularidad: 1 chunk = 1 articulo (mismo criterio que sistemas legales profesionales).
  Cada chunk tiene header de navegacion en el texto + metadata jerarquica completa.

OUTPUTS:
  data/carta_organica/_chunks.json     -- ~231 chunks listos para embeddings
  data/carta_organica/_stats.json      -- estadisticas de parseo

USO:
  python scripts/parse_carta_organica.py
"""
import json
import re
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
PDF_PATH = Path(os.getenv("CARTA_ORGANICA_PATH", "."))
OUTPUT_DIR = PROJECT_ROOT / "data" / "carta_organica"

try:
    import pdfminer.high_level
except ImportError:
    print("ERROR: pdfminer.six no esta instalado.")
    print("       Corre: pip install pdfminer.six --break-system-packages")
    sys.exit(1)


# ============================================================
# CONSTANTES JURIDICAS
# ============================================================

# Articulos blindados (no modificables por enmienda, segun art. 218)
# - Art. 41: remuneraciones del Concejo
# - Art. 68: remuneracion del Intendente
# - Capitulo III del Titulo IX (arts. 160-162): Consejo de Planificacion Estrategica
ARTICULOS_BLINDADOS = {41, 68, 160, 161, 162}

# Configuracion de sub-chunking por inciso para articulos densos.
# Un articulo es "denso" si supera CUALQUIERA de estos umbrales.
# Decision basada en audit del corpus: el problema afecta a 6 articulos
# (8, 10, 13, 15, 45, 72). Articulos con menos incisos (ej. Art. 38 con 7)
# rinden bien sin sub-chunking en la validacion.
UMBRAL_INCISOS_PARA_SUBCHUNK = 10
UMBRAL_CHARS_PARA_SUBCHUNK = 2500

# Mapeo romanos -> numeros (hasta XX, suficiente para esta CO)
ROMANOS = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
    "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
}

# Mapeo ordinales escritos (Primera, Segunda, ...) -> numeros
ORDINALES = {
    "Primera": 1, "Segunda": 2, "Tercera": 3, "Cuarta": 4,
    "Quinta": 5, "Sexta": 6, "Septima": 7, "Séptima": 7,
    "Octava": 8, "Novena": 9,
    "Decima": 10, "Décima": 10,
    "Decimo primera": 11, "Décimo primera": 11,
    "Decimo segunda": 12, "Décimo segunda": 12,
}


# ============================================================
# EXTRACCION DEL PDF
# ============================================================

def extraer_texto_pdf(pdf_path):
    """Extrae el texto plano del PDF de la Carta Organica."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"No se encuentra el PDF: {pdf_path}")
    return pdfminer.high_level.extract_text(str(pdf_path))


# ============================================================
# LIMPIEZA Y NORMALIZACION
# ============================================================

def limpiar_texto(texto):
    """Limpia el texto extraido del PDF preservando estructura."""
    # Quitar el header "Titulo : CARTA ORGANICA MUNICIPAL - Julio 2010"
    # y "Texto :" que pdfminer agrega al inicio del archivo
    m = re.search(r"P\s*R\s*E\s*Á?M\s*B\s*U\s*L\s*O", texto)
    if m:
        texto = texto[m.start():]

    # Cortar el INDICE final si esta presente (estructura repetida que falsea conteos)
    m_indice = re.search(r"\n\s*\x0c?\s*ÍNDICE\s*\n", texto)
    if m_indice:
        texto = texto[:m_indice.start()]

    # Cortar el INDICE final si esta presente (estructura repetida que falsea conteos)
    m_indice = re.search(r"\n\s*\x0c?\s*ÍNDICE\s*\n", texto)
    if m_indice:
        texto = texto[:m_indice.start()]

    # Normalizar saltos de linea excesivos
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    # Normalizar espacios multiples (pero no los saltos de linea)
    texto = re.sub(r"[ \t]+", " ", texto)

    return texto.strip()


# ============================================================
# DETECCION DE ESTRUCTURA JERARQUICA
# ============================================================

# Patrones para detectar elementos estructurales
# Importante: las regex se anclan a \n para evitar matches dentro de articulos
# (donde puede haber menciones tipo "el Titulo III" en texto narrativo)

PATRON_PARTE = re.compile(
    r"\n(PRIMERA PARTE|SEGUNDA PARTE)\s*\n([^\n]+)",
    re.IGNORECASE
)

PATRON_TITULO = re.compile(
    # Captura el nombre del TITULO incluso si abarca varias lineas
    # (se detiene en el siguiente elemento estructural)
    r"\nTÍTULO\s+([IVX]+)\s*\n((?:(?!\nCAPÍTULO|\nSECCIÓN|\nArtículo|\nTÍTULO|\nPRIMERA PARTE|\nSEGUNDA PARTE).)+)",
    re.IGNORECASE | re.DOTALL
)

PATRON_CAPITULO = re.compile(
    r"\nCAPÍTULO\s+([IVX]+)\s*:\s*([^\n]+)",
    re.IGNORECASE
)

PATRON_SECCION = re.compile(
    r"\nSECCIÓN\s+([IVX]+)\s*:\s*([^\n]+)",
    re.IGNORECASE
)

PATRON_ARTICULO = re.compile(
    r"\n\s*\x0c?\s*Artículo\s+(\d+):",
)

PATRON_TRANSITORIA = re.compile(
    r"\n\s*\x0c?\s*(Primera|Segunda|Tercera|Cuarta|Quinta|Sexta|Séptima|Octava|Novena|Décima|Décimo primera|Décimo segunda):"
)

PATRON_PREAMBULO_INICIO = re.compile(r"P\s*R\s*E\s*Á?M\s*B\s*U\s*L\s*O", re.IGNORECASE)

PATRON_INDICE_INICIO = re.compile(r"\nÍNDICE\s*\n", re.IGNORECASE)

PATRON_DISPOSICIONES = re.compile(r"\nDISPOSICIONES TRANSITORIAS", re.IGNORECASE)


def construir_indice_estructural(texto):
    """
    Recorre el texto y construye una lista de eventos estructurales
    en orden de aparicion: cada evento tiene {tipo, num, nombre, pos}.

    Esto nos da la "tabla de contenidos" implicita del PDF para despues
    asignar cada articulo a su contexto jerarquico.
    """
    eventos = []

    for m in PATRON_PARTE.finditer(texto):
        nombre_parte = m.group(1).strip().upper()
        contenido = m.group(2).strip()
        eventos.append({
            "tipo": "parte",
            "num": 1 if "PRIMERA" in nombre_parte else 2,
            "nombre": nombre_parte,
            "subtitulo": contenido,
            "pos": m.start(),
        })

    for m in PATRON_TITULO.finditer(texto):
        eventos.append({
            "tipo": "titulo",
            "num": ROMANOS.get(m.group(1).upper(), 0),
            "nombre": " ".join(m.group(2).split()),
            "pos": m.start(),
        })

    for m in PATRON_CAPITULO.finditer(texto):
        eventos.append({
            "tipo": "capitulo",
            "num": ROMANOS.get(m.group(1).upper(), 0),
            "nombre": m.group(2).strip(),
            "pos": m.start(),
        })

    for m in PATRON_SECCION.finditer(texto):
        eventos.append({
            "tipo": "seccion",
            "num": ROMANOS.get(m.group(1).upper(), 0),
            "nombre": m.group(2).strip(),
            "pos": m.start(),
        })

    eventos.sort(key=lambda e: e["pos"])
    return eventos


def contexto_jerarquico_en(eventos, pos, parte_actual=None, titulo_actual=None,
                            capitulo_actual=None, seccion_actual=None):
    """
    Dado un puntero de posicion, encuentra el contexto jerarquico activo:
    cual es la parte, titulo, capitulo, seccion vigentes en ese punto.

    Recorre los eventos previos a 'pos' y va actualizando el estado.
    Nota: una seccion solo es valida si esta DENTRO del mismo capitulo;
    al cambiar de capitulo, se resetea la seccion.
    """
    parte = parte_actual
    titulo = titulo_actual
    capitulo = capitulo_actual
    seccion = seccion_actual

    for e in eventos:
        if e["pos"] >= pos:
            break
        if e["tipo"] == "parte":
            parte = e
            titulo = None
            capitulo = None
            seccion = None
        elif e["tipo"] == "titulo":
            titulo = e
            capitulo = None
            seccion = None
        elif e["tipo"] == "capitulo":
            capitulo = e
            seccion = None
        elif e["tipo"] == "seccion":
            seccion = e

    return parte, titulo, capitulo, seccion


# ============================================================
# EXTRACCION DE REFERENCIAS CRUZADAS
# ============================================================

PATRON_REFERENCIA_ARTICULO = re.compile(
    r"art[ií]culo\s+(\d+)|art\.\s*(\d+)",
    re.IGNORECASE
)


def extraer_referencias(texto_articulo, articulo_propio):
    """
    Detecta menciones a otros articulos dentro del texto.
    Devuelve set de numeros de articulos referenciados (excluyendo el propio).
    """
    refs = set()
    for m in PATRON_REFERENCIA_ARTICULO.finditer(texto_articulo):
        num = int(m.group(1) or m.group(2))
        if num != articulo_propio and 1 <= num <= 218:
            refs.add(num)
    return sorted(refs)


# ============================================================
# EXTRACCION DE BLOQUES (PREAMBULO, ARTICULOS, TRANSITORIAS)
# ============================================================

def extraer_preambulo(texto):
    """Extrae el bloque del Preambulo (desde 'PREÁMBULO' hasta 'PRIMERA PARTE')."""
    m_inicio = PATRON_PREAMBULO_INICIO.search(texto)
    m_fin = re.search(r"\nPRIMERA PARTE\s*\n", texto)

    if not m_inicio or not m_fin:
        return None

    # El preambulo va desde despues del titulo "PREAMBULO" hasta "PRIMERA PARTE"
    inicio = m_inicio.end()
    fin = m_fin.start()
    return texto[inicio:fin].strip()


def extraer_articulos(texto, eventos):
    """
    Extrae los 218 articulos con su contexto jerarquico.

    Cada articulo va desde 'Artículo N:' hasta el siguiente 'Artículo' o
    hasta el final del bloque normativo (antes de Disposiciones Transitorias).
    """
    # Cortar antes de disposiciones transitorias para no contaminar
    m_disp = PATRON_DISPOSICIONES.search(texto)
    fin_normativo = m_disp.start() if m_disp else len(texto)

    matches = list(PATRON_ARTICULO.finditer(texto, 0, fin_normativo))
    articulos = []

    for i, m in enumerate(matches):
        num = int(m.group(1))
        start = m.start()

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = fin_normativo

        texto_articulo = texto[start:end].strip()

        # Quitar el "Artículo N:" del inicio para el contenido limpio
        texto_limpio = re.sub(r"^[\n\s\x0c]*Artículo\s+\d+:\s*", "", texto_articulo).strip()
        if not texto_limpio:
            # Fallback: si no matchea el patron al inicio
            texto_limpio = re.sub(r"^Artículo\s+\d+:\s*", "", texto_articulo).strip()

        # Contexto jerarquico
        parte, titulo, capitulo, seccion = contexto_jerarquico_en(eventos, start)

        articulos.append({
            "num": num,
            "texto": texto_limpio,
            "parte": parte,
            "titulo": titulo,
            "capitulo": capitulo,
            "seccion": seccion,
            "start": start,
            "end": end,
            "char_count": len(texto_limpio),
        })

    return articulos


def extraer_disposiciones_transitorias(texto):
    """Extrae las disposiciones transitorias (Primera a Decimo segunda)."""
    m_inicio = PATRON_DISPOSICIONES.search(texto)
    if not m_inicio:
        return []

    # El indice esta despues de las transitorias; cortar antes
    m_indice = PATRON_INDICE_INICIO.search(texto, m_inicio.end())
    fin = m_indice.start() if m_indice else len(texto)

    bloque = texto[m_inicio.end():fin]

    matches = list(PATRON_TRANSITORIA.finditer(bloque))
    disposiciones = []

    for i, m in enumerate(matches):
        nombre_ordinal = m.group(1).strip()
        num = ORDINALES.get(nombre_ordinal)
        if num is None:
            continue

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bloque)

        texto_disp = bloque[start:end].strip()
        texto_limpio = re.sub(r"^\n?[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+:\s*", "", texto_disp).strip()

        disposiciones.append({
            "num": num,
            "ordinal": nombre_ordinal,
            "texto": texto_limpio,
            "char_count": len(texto_limpio),
        })

    return disposiciones


# ============================================================
# DETECCION Y EXTRACCION DE INCISOS
# ============================================================

PATRON_INCISO = re.compile(r"^\s*(\d{1,2})\.\s+", re.MULTILINE)


def detectar_incisos(texto):
    """
    Detecta los incisos numerados de un articulo y devuelve sus posiciones.

    Cuenta como inciso solo si los numeros son consecutivos desde 1.
    Esto evita falsos positivos (ej: "Art. 75 Inciso 17 de la Constitucion").

    Devuelve lista de tuplas (num_inciso, start, end) o [] si no hay incisos.
    """
    matches = list(PATRON_INCISO.finditer(texto))
    if not matches:
        return []

    # Validar consecutividad desde 1
    nums = [int(m.group(1)) for m in matches]
    if 1 not in nums:
        return []

    # Encontrar la secuencia maxima consecutiva desde 1
    consec = [matches[nums.index(1)]]
    siguiente = 2
    while siguiente in nums:
        consec.append(matches[nums.index(siguiente)])
        siguiente += 1

    if len(consec) < 3:
        # Menos de 3 incisos consecutivos: probablemente no es una enumeracion real
        return []

    # Construir tuplas con start y end de cada inciso
    incisos = []
    for i, m in enumerate(consec):
        num_inciso = int(m.group(1))
        start = m.start()
        if i + 1 < len(consec):
            end = consec[i + 1].start()
        else:
            end = len(texto)
        incisos.append((num_inciso, start, end))

    return incisos


def es_articulo_denso(num_incisos, char_count):
    """Decide si un articulo requiere sub-chunking por inciso."""
    return (num_incisos >= UMBRAL_INCISOS_PARA_SUBCHUNK
            or char_count >= UMBRAL_CHARS_PARA_SUBCHUNK)


def extraer_incisos_de_articulo(articulo):
    """
    Si el articulo es denso, extrae sus incisos como sub-chunks.
    Devuelve lista de dicts con num_inciso y texto_inciso, o [] si no aplica.
    """
    texto = articulo["texto"]
    incisos = detectar_incisos(texto)
    if not incisos:
        return []
    if not es_articulo_denso(len(incisos), articulo["char_count"]):
        return []

    sub_chunks = []
    # Texto introductorio: lo que esta antes del primer inciso
    intro_end = incisos[0][1]
    intro = texto[:intro_end].strip()

    for num_inciso, start, end in incisos:
        texto_inciso = texto[start:end].strip()
        sub_chunks.append({
            "num_inciso": num_inciso,
            "texto_inciso": texto_inciso,
            "intro_articulo": intro,
        })
    return sub_chunks


# ============================================================
# CONSTRUCCION DE CHUNKS
# ============================================================

def construir_header_navegacion(parte, titulo, capitulo, seccion):
    """Construye el header de navegacion que se prepende al texto del chunk."""
    partes = ["CARTA ORGÁNICA MUNICIPAL"]
    if parte:
        partes.append(parte["nombre"])
    if titulo:
        partes.append(f"Título {titulo['num']}: {titulo['nombre']}")
    if capitulo:
        partes.append(f"Capítulo {capitulo['num']}: {capitulo['nombre']}")
    if seccion:
        partes.append(f"Sección {seccion['num']}: {seccion['nombre']}")
    return "[" + " > ".join(partes) + "]"


def construir_chunk_preambulo(texto_preambulo):
    """
    Construye el chunk del Preambulo.

    El texto original esta en MAYUSCULAS (estilo ceremonial del PDF).
    Para el embedding lo normalizamos a "sentence case" porque las
    mayusculas afectan negativamente la similitud semantica en
    text-embedding-3-small. El header mantiene el contexto original.
    """
    header = "[CARTA ORGÁNICA MUNICIPAL > PREÁMBULO]"
    # Normalizar a sentence case para mejor embedding
    # (mantiene las mayusculas originales como capitalizacion de oracion)
    texto_normalizado = texto_preambulo.lower()
    # Capitalizar al inicio y despues de . ! ? ;
    import re as _re_local
    def _capitalizar(m):
        return m.group(0).upper()
    texto_normalizado = _re_local.sub(r"^[a-záéíóúñ]", _capitalizar, texto_normalizado)
    texto_normalizado = _re_local.sub(r"([.!?;]\s+)([a-záéíóúñ])",
                                       lambda m: m.group(1) + m.group(2).upper(),
                                       texto_normalizado)
    # Nombres propios y siglas tipicas del Preambulo (preservar)
    nombres_propios = {
        "san martín de los andes": "San Martín de los Andes",
        "dios": "Dios",
    }
    for original, restaurado in nombres_propios.items():
        texto_normalizado = texto_normalizado.replace(original, restaurado)

    texto_completo = f"{header}\n\n{texto_normalizado}"
    return {
        "chunk_id": "carta_organica_2010_preambulo",
        "texto": texto_completo,
        "metadata": {
            "doc_id": "carta_organica_2010",
            "tipo_documento": "carta_organica",
            "tipo_chunk": "preambulo",
            "fuente": "Carta Orgánica Municipal SMA - Julio 2010",
            "anio": 2010,
            "boletin_oficial": "382",
            "fecha_publicacion": "2010-11-26",
            "char_count": len(texto_completo),
            "no_modificable_por_enmienda": True,  # el preambulo NO se puede enmendar (art 218)
        }
    }


def construir_chunk_articulo(articulo):
    """Construye el chunk de un articulo."""
    num = articulo["num"]
    header = construir_header_navegacion(
        articulo["parte"], articulo["titulo"],
        articulo["capitulo"], articulo["seccion"]
    )
    texto_completo = f"{header}\n\nArtículo {num}: {articulo['texto']}"
    refs = extraer_referencias(articulo["texto"], num)

    metadata = {
        "doc_id": "carta_organica_2010",
        "tipo_documento": "carta_organica",
        "tipo_chunk": "articulo_carta_organica",
        "fuente": "Carta Orgánica Municipal SMA - Julio 2010",
        "anio": 2010,
        "boletin_oficial": "382",
        "fecha_publicacion": "2010-11-26",
        "articulo_num": num,
        "char_count": len(texto_completo),
        "no_modificable_por_enmienda": num in ARTICULOS_BLINDADOS,
        "referencias_a_articulos": refs,
    }

    # Metadata jerarquica
    if articulo["parte"]:
        metadata["parte_num"] = articulo["parte"]["num"]
        metadata["parte_nombre"] = articulo["parte"]["nombre"]
    if articulo["titulo"]:
        metadata["titulo_num"] = articulo["titulo"]["num"]
        metadata["titulo_nombre"] = articulo["titulo"]["nombre"]
    if articulo["capitulo"]:
        metadata["capitulo_num"] = articulo["capitulo"]["num"]
        metadata["capitulo_nombre"] = articulo["capitulo"]["nombre"]
    if articulo["seccion"]:
        metadata["seccion_num"] = articulo["seccion"]["num"]
        metadata["seccion_nombre"] = articulo["seccion"]["nombre"]

    return {
        "chunk_id": f"carta_organica_2010_art_{num}",
        "texto": texto_completo,
        "metadata": metadata,
    }


def construir_chunk_inciso(articulo, sub_chunk):
    """
    Construye un chunk para un inciso especifico de un articulo denso.

    El texto incluye:
      - Header de navegacion jerarquica
      - Intro del articulo (contexto previo al primer inciso)
      - El inciso especifico

    El embedding de este chunk se enfoca en el inciso, pero mantiene contexto.
    """
    num = articulo["num"]
    num_inciso = sub_chunk["num_inciso"]

    header = construir_header_navegacion(
        articulo["parte"], articulo["titulo"],
        articulo["capitulo"], articulo["seccion"]
    )

    # Construir texto: header + "Articulo N (inciso M): [intro]\n[texto_inciso]"
    # El intro del articulo da contexto al inciso (de que esta hablando)
    texto_completo = (
        f"{header}\n\n"
        f"Articulo {num}, inciso {num_inciso}\n\n"
        f"Contexto del articulo: {sub_chunk['intro_articulo']}\n\n"
        f"Inciso {num_inciso}: {sub_chunk['texto_inciso']}"
    )

    refs = extraer_referencias(sub_chunk["texto_inciso"], num)

    metadata = {
        "doc_id": "carta_organica_2010",
        "tipo_documento": "carta_organica",
        "tipo_chunk": "inciso_carta_organica",
        "fuente": "Carta Organica Municipal SMA - Julio 2010",
        "anio": 2010,
        "boletin_oficial": "382",
        "fecha_publicacion": "2010-11-26",
        "articulo_num": num,
        "inciso_num": num_inciso,
        "es_inciso": True,
        "char_count": len(texto_completo),
        "no_modificable_por_enmienda": num in ARTICULOS_BLINDADOS,
        "referencias_a_articulos": refs,
    }

    # Heredar metadata jerarquica del articulo padre
    if articulo["parte"]:
        metadata["parte_num"] = articulo["parte"]["num"]
        metadata["parte_nombre"] = articulo["parte"]["nombre"]
    if articulo["titulo"]:
        metadata["titulo_num"] = articulo["titulo"]["num"]
        metadata["titulo_nombre"] = articulo["titulo"]["nombre"]
    if articulo["capitulo"]:
        metadata["capitulo_num"] = articulo["capitulo"]["num"]
        metadata["capitulo_nombre"] = articulo["capitulo"]["nombre"]
    if articulo["seccion"]:
        metadata["seccion_num"] = articulo["seccion"]["num"]
        metadata["seccion_nombre"] = articulo["seccion"]["nombre"]

    return {
        "chunk_id": f"carta_organica_2010_art_{num}_inc_{num_inciso}",
        "texto": texto_completo,
        "metadata": metadata,
    }


def construir_chunk_transitoria(disposicion):
    """Construye un chunk para una disposicion transitoria."""
    num = disposicion["num"]
    ordinal = disposicion["ordinal"]
    header = f"[CARTA ORGÁNICA MUNICIPAL > DISPOSICIONES TRANSITORIAS Y COMPLEMENTARIAS]"
    texto_completo = f"{header}\n\nDisposición {ordinal}: {disposicion['texto']}"
    return {
        "chunk_id": f"carta_organica_2010_transitoria_{num}",
        "texto": texto_completo,
        "metadata": {
            "doc_id": "carta_organica_2010",
            "tipo_documento": "carta_organica",
            "tipo_chunk": "disposicion_transitoria",
            "fuente": "Carta Orgánica Municipal SMA - Julio 2010",
            "anio": 2010,
            "boletin_oficial": "382",
            "fecha_publicacion": "2010-11-26",
            "transitoria_num": num,
            "transitoria_ordinal": ordinal,
            "char_count": len(texto_completo),
            "es_disposicion_transitoria": True,
        }
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("PARSER CARTA ORGANICA MUNICIPAL - San Martin de los Andes (2010)")
    print("=" * 70)

    print(f"\nLeyendo PDF: {PDF_PATH}")
    texto_raw = extraer_texto_pdf(PDF_PATH)
    print(f"  {len(texto_raw)} chars extraidos.")

    print("\nLimpiando texto...")
    texto = limpiar_texto(texto_raw)
    print(f"  {len(texto)} chars despues de limpieza.")

    print("\nConstruyendo indice estructural...")
    eventos = construir_indice_estructural(texto)
    partes = [e for e in eventos if e["tipo"] == "parte"]
    titulos = [e for e in eventos if e["tipo"] == "titulo"]
    capitulos = [e for e in eventos if e["tipo"] == "capitulo"]
    secciones = [e for e in eventos if e["tipo"] == "seccion"]
    print(f"  Partes:    {len(partes)}")
    print(f"  Titulos:   {len(titulos)}")
    print(f"  Capitulos: {len(capitulos)}")
    print(f"  Secciones: {len(secciones)}")

    print("\nExtrayendo Preambulo...")
    preambulo = extraer_preambulo(texto)
    if preambulo:
        print(f"  Preambulo: {len(preambulo)} chars")
    else:
        print(f"  WARN: no se pudo extraer el Preambulo")

    print("\nExtrayendo articulos...")
    articulos = extraer_articulos(texto, eventos)
    print(f"  {len(articulos)} articulos extraidos (esperado: 218).")
    if len(articulos) != 218:
        print(f"  ADVERTENCIA: cantidad de articulos no coincide con el esperado.")
        nums_extraidos = set(a["num"] for a in articulos)
        faltantes = set(range(1, 219)) - nums_extraidos
        extras = nums_extraidos - set(range(1, 219))
        if faltantes:
            print(f"    Faltantes: {sorted(faltantes)[:20]}{'...' if len(faltantes) > 20 else ''}")
        if extras:
            print(f"    Extras (>218): {sorted(extras)}")

    print("\nExtrayendo disposiciones transitorias...")
    transitorias = extraer_disposiciones_transitorias(texto)
    print(f"  {len(transitorias)} transitorias extraidas (esperado: 12).")

    print("\nConstruyendo chunks...")
    chunks = []

    if preambulo:
        chunks.append(construir_chunk_preambulo(preambulo))

    articulos_sub_chunkeados = []
    total_incisos_generados = 0
    for art in articulos:
        # Chunk consolidado del articulo completo (siempre)
        chunks.append(construir_chunk_articulo(art))

        # Si es denso, agregar chunks por inciso
        sub_chunks = extraer_incisos_de_articulo(art)
        if sub_chunks:
            articulos_sub_chunkeados.append((art["num"], len(sub_chunks)))
            total_incisos_generados += len(sub_chunks)
            for sub in sub_chunks:
                chunks.append(construir_chunk_inciso(art, sub))

    for disp in transitorias:
        chunks.append(construir_chunk_transitoria(disp))

    print(f"  Total chunks: {len(chunks)}")
    print(f"\nSub-chunking por inciso:")
    print(f"  Articulos sub-chunkeados: {len(articulos_sub_chunkeados)}")
    print(f"  Chunks de inciso generados: {total_incisos_generados}")
    for art_num, n_incisos in articulos_sub_chunkeados:
        print(f"    Art. {art_num}: {n_incisos} incisos")

    # Estadisticas
    char_counts = [c["metadata"]["char_count"] for c in chunks]
    print(f"\nEstadisticas de tamaño de chunks:")
    print(f"  Min: {min(char_counts)} chars")
    print(f"  Max: {max(char_counts)} chars")
    print(f"  Avg: {sum(char_counts) // len(char_counts)} chars")
    print(f"  Total chars: {sum(char_counts):,}")

    blindados_encontrados = [c for c in chunks
                             if c["metadata"].get("no_modificable_por_enmienda")]
    print(f"\nChunks blindados (no modificables por enmienda): {len(blindados_encontrados)}")
    for c in blindados_encontrados:
        meta = c["metadata"]
        if meta.get("tipo_chunk") == "preambulo":
            print(f"  - Preambulo")
        elif meta.get("tipo_chunk") == "articulo_carta_organica":
            print(f"  - Art. {meta['articulo_num']}")

    # Guardar outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks_path = OUTPUT_DIR / "_chunks.json"
    stats_path = OUTPUT_DIR / "_stats.json"

    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)
    print(f"\nChunks guardados en {chunks_path}")
    print(f"  Tamaño: {chunks_path.stat().st_size / 1024:.1f} KB")

    stats = {
        "doc_id": "carta_organica_2010",
        "fuente": str(PDF_PATH),
        "chars_extraidos_del_pdf": len(texto_raw),
        "chars_despues_de_limpieza": len(texto),
        "partes_detectadas": len(partes),
        "titulos_detectados": len(titulos),
        "capitulos_detectados": len(capitulos),
        "secciones_detectadas": len(secciones),
        "articulos_extraidos": len(articulos),
        "articulos_esperados": 218,
        "transitorias_extraidas": len(transitorias),
        "transitorias_esperadas": 12,
        "preambulo_extraido": preambulo is not None,
        "total_chunks": len(chunks),
        "blindados_encontrados": len(blindados_encontrados),
        "char_count_min": min(char_counts),
        "char_count_max": max(char_counts),
        "char_count_avg": sum(char_counts) // len(char_counts),
        "char_count_total": sum(char_counts),
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"Stats guardadas en {stats_path}")

    print("\n" + "=" * 70)
    print("PARSEO COMPLETO")
    print("=" * 70)


if __name__ == "__main__":
    main()
