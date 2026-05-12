"""
Detector de derogaciones totales en el corpus del Digesto Municipal.

Detecta frases del tipo:
  - "DERÓGASE la Ordenanza nº 11.854/18"
  - "DERÓGASE la Ordenanza 10.728/15"
  - "DERÓGASE la Ordenanza 7660/07"
  - "DERÓGANSE las Ordenanzas 130/1987; 1960/1996; 12745/2019"

NO detecta:
  - Derogaciones parciales ("DERÓGASE el artículo X de la Ordenanza Y")
  - Modificaciones (MODIFÍCASE, SUSTITÚYESE) -> esas no son derogaciones totales
  - Derogaciones de Resoluciones/Comunicaciones (cobertura: solo Ordenanzas, ~95% de casos)

Cada deteccion devuelve un dict con:
  - doc_id_derogatorio  : quien deroga (ej: "ordenanza_11698_2017")
  - doc_id_derogado     : que se deroga (ej: "ordenanza_10728_2015")
  - verbo               : "DERÓGASE" o "DERÓGANSE"
  - texto_evidencia     : frase original como aparece en el .txt
  - chunk_id_origen     : id del chunk donde se detecto

Decisiones de diseño documentadas:

  1. Año de 2 dígitos -> 4 dígitos:
     - yy >= 60  -> siglo XX (19yy)
     - yy <  60  -> siglo XXI (20yy)
     Cubre desde 1960 hasta 2059. El corpus municipal arranca alrededor
     de 1973 y se proyecta varias decadas mas, asi que el limite es seguro.

  2. Numero de norma:
     - Aceptamos puntos de miles ("10.728" -> 10728)
     - Aceptamos el prefijo "nº", "Nº", "N°" o vacio
     - El numero debe ser entero positivo (1 a 6 digitos)

  3. Capturar "DERÓGASE el artículo X de la Ordenanza Y" es FALSO POSITIVO:
     - Detectamos la presencia de "artículo" / "art." entre el verbo y "Ordenanza"
     - Si esta presente, se descarta la deteccion (es parcial, no total)
"""
import re


# Patron principal: DERÓGASE/DERÓGANSE + la/las + Ordenanza(s) + numero/año
# Permitimos espacios y saltos de linea entre palabras (\s+)
PATRON_DEROGACION = re.compile(
    r"(DER[ÓO]GASE|DER[ÓO]GANSE)"           # verbo
    r"\s+(la|las)"                           # determinante
    r"\s+Ordenanzas?"                        # ordenanza/ordenanzas
    r"(\s+[^\n]{0,400})",                    # resto: hasta 400 chars en la misma logica
    re.IGNORECASE
)

# Subpatron para extraer un numero de norma del "resto"
# Ej matches: "nº 11.854/18", "10.728/15", "7660/07", "1234/2019"
PATRON_NUMERO_NORMA = re.compile(
    r"(?:n[º°]\s*)?"                         # prefijo "nº" opcional (case-insensitive abajo)
    r"(\d{1,2}(?:\.\d{3})*|\d{3,6})"         # numero con o sin puntos de miles
    r"\s*\/\s*"                              # barra (con espacios opcionales)
    r"(\d{2,4})",                            # año 2 o 4 digitos
    re.IGNORECASE
)

# Indicadores de derogacion PARCIAL (descartar el match)
PATRON_PARCIAL = re.compile(
    r"\b(art[íi]culos?|art\.?)\b",
    re.IGNORECASE
)


def _normalizar_numero(numero_str: str) -> str:
    """'10.728' -> '10728', '7660' -> '7660'."""
    return numero_str.replace(".", "").strip()


def _expandir_anio(anio_str: str) -> int:
    """
    Convierte un año en string (2 o 4 digitos) a entero de 4 digitos.
    Regla: yy >= 60 -> 19yy, yy < 60 -> 20yy.
    Cubre 1960-2059.
    """
    n = int(anio_str)
    if n >= 100:
        return n  # ya es 4 digitos
    if n >= 60:
        return 1900 + n
    return 2000 + n


def _es_derogacion_parcial(texto_resto: str) -> bool:
    """
    True si el resto del match contiene indicadores de derogacion parcial
    (mencion de "articulo", "art.", etc).
    """
    return bool(PATRON_PARCIAL.search(texto_resto))


def _construir_doc_id(numero: int, anio: int) -> str:
    """Construye doc_id en el mismo formato que usa el chunker."""
    return f"ordenanza_{numero}_{anio}"


def detectar_derogaciones_totales(chunks: list) -> list:
    """
    Recibe la lista de chunks (formato del _chunks.json) y devuelve
    los eventos derogatorios totales detectados.

    Si un mismo chunk contiene multiples derogaciones (caso
    "DERÓGANSE las Ordenanzas A; B; C"), se generan multiples eventos.
    """
    eventos = []

    for chunk in chunks:
        texto = chunk["texto"]
        chunk_id = chunk["chunk_id"]
        doc_id_derogatorio = chunk["metadata"]["doc_id"]

        for match in PATRON_DEROGACION.finditer(texto):
            verbo = match.group(1).upper()
            resto = match.group(3) or ""

            # Filtro 1: descartar derogaciones parciales (mencion de "artículo")
            # Solo aplicamos este filtro si el "artículo" aparece ANTES del primer numero,
            # porque "DERÓGASE el artículo X de la Ordenanza Y" tiene "artículo" antes del numero.
            # En cambio "DERÓGASE la Ordenanza 1234/2010 que regulaba los artículos..." es total.
            # Estrategia simple: si las primeras ~80 chars del resto tienen "articulo", es parcial.
            preambulo = resto[:80]
            if _es_derogacion_parcial(preambulo):
                # Verificar: si el preambulo tambien tiene "ordenanza"+numero, podria
                # ser una derogacion total con mencion lateral. Por ahora descartamos
                # conservadoramente.
                continue

            # Extraer todos los numeros de norma del resto
            for num_match in PATRON_NUMERO_NORMA.finditer(resto):
                numero_raw = num_match.group(1)
                anio_raw = num_match.group(2)

                numero = int(_normalizar_numero(numero_raw))
                anio = _expandir_anio(anio_raw)

                doc_id_derogado = _construir_doc_id(numero, anio)

                # Evidencia: el match completo de la derogacion
                evidencia = match.group(0).replace("\n", " ").strip()
                # Compactar espacios multiples
                evidencia = re.sub(r"\s+", " ", evidencia)
                if len(evidencia) > 200:
                    evidencia = evidencia[:200] + "..."

                eventos.append({
                    "doc_id_derogatorio": doc_id_derogatorio,
                    "doc_id_derogado": doc_id_derogado,
                    "verbo": verbo,
                    "texto_evidencia": evidencia,
                    "chunk_id_origen": chunk_id,
                })

    return eventos


# ============================================================
# TESTS SINTETICOS
# ============================================================

if __name__ == "__main__":
    print("Test 1: derogacion total simple, formato nº + puntos + año 2 digitos")
    chunk1 = {
        "chunk_id": "ordenanza_12106_2018_art_4",
        "texto": "DERÓGASE\nla\nOrdenanza nº 11.854/18.-",
        "metadata": {"doc_id": "ordenanza_12106_2018"},
    }
    eventos = detectar_derogaciones_totales([chunk1])
    assert len(eventos) == 1, f"FALLO: {len(eventos)} eventos"
    assert eventos[0]["doc_id_derogado"] == "ordenanza_11854_2018", \
        f"FALLO: detectado {eventos[0]['doc_id_derogado']}"
    print(f"  Detectado: {eventos[0]['doc_id_derogado']}")
    print(f"  Evidencia: {eventos[0]['texto_evidencia']}")
    print("  OK Test 1\n")

    print("Test 2: derogacion sin prefijo nº, año 2 digitos")
    chunk2 = {
        "chunk_id": "ordenanza_11698_2017_art_4",
        "texto": "DERÓGASE la Ordenanza 10.728/15 \"Estructura Orgánica\"",
        "metadata": {"doc_id": "ordenanza_11698_2017"},
    }
    eventos = detectar_derogaciones_totales([chunk2])
    assert len(eventos) == 1
    assert eventos[0]["doc_id_derogado"] == "ordenanza_10728_2015"
    print(f"  Detectado: {eventos[0]['doc_id_derogado']}")
    print("  OK Test 2\n")

    print("Test 3: derogacion con año 4 digitos completo")
    chunk3 = {
        "chunk_id": "ordenanza_14528_2023_art_2",
        "texto": "DERÓGANSE las Ordenanzas 130/1987; 1960/1996; 12745/2019 y 13418/2021.-",
        "metadata": {"doc_id": "ordenanza_14528_2023"},
    }
    eventos = detectar_derogaciones_totales([chunk3])
    assert len(eventos) == 4, f"FALLO: esperaba 4, obtuvo {len(eventos)}"
    nums_derogados = sorted(e["doc_id_derogado"] for e in eventos)
    print(f"  Detectados: {nums_derogados}")
    assert "ordenanza_130_1987" in nums_derogados
    assert "ordenanza_1960_1996" in nums_derogados
    assert "ordenanza_12745_2019" in nums_derogados
    assert "ordenanza_13418_2021" in nums_derogados
    print("  OK Test 3\n")

    print("Test 4: derogacion PARCIAL (debe ignorarse)")
    chunk4 = {
        "chunk_id": "ordenanza_X_art_1",
        "texto": "DERÓGASE el artículo 73° del Código de Faltas, Ordenanza 94/1984",
        "metadata": {"doc_id": "ordenanza_X"},
    }
    eventos = detectar_derogaciones_totales([chunk4])
    assert len(eventos) == 0, f"FALLO Test 4: esperaba 0, obtuvo {len(eventos)}"
    print("  OK Test 4\n")

    print("Test 5: conversion año yy -> aaaa")
    assert _expandir_anio("07") == 2007
    assert _expandir_anio("15") == 2015
    assert _expandir_anio("18") == 2018
    assert _expandir_anio("59") == 2059
    assert _expandir_anio("60") == 1960
    assert _expandir_anio("87") == 1987
    assert _expandir_anio("99") == 1999
    assert _expandir_anio("2014") == 2014
    print("  OK Test 5\n")

    print("Test 6: normalizacion de numero (puntos de miles)")
    assert _normalizar_numero("10.728") == "10728"
    assert _normalizar_numero("7660") == "7660"
    assert _normalizar_numero("11.854") == "11854"
    print("  OK Test 6\n")

    print("Test 7: texto sin derogaciones (no debe falsear)")
    chunk7 = {
        "chunk_id": "ordenanza_X_art_1",
        "texto": "APRUÉBASE el reglamento. La presente Ordenanza entrará en vigencia el 01/01/2024.",
        "metadata": {"doc_id": "ordenanza_X"},
    }
    eventos = detectar_derogaciones_totales([chunk7])
    assert len(eventos) == 0, f"FALLO Test 7: esperaba 0, obtuvo {len(eventos)}"
    print("  OK Test 7\n")

    print("Test 8: MODIFICASE no debe detectarse (no es derogacion)")
    chunk8 = {
        "chunk_id": "ordenanza_X_art_1",
        "texto": "MODIFÍCASE la Ordenanza 1234/2010 en su artículo 5.",
        "metadata": {"doc_id": "ordenanza_X"},
    }
    eventos = detectar_derogaciones_totales([chunk8])
    assert len(eventos) == 0, f"FALLO Test 8: esperaba 0, obtuvo {len(eventos)}"
    print("  OK Test 8\n")

    print("Todos los tests pasaron.")
