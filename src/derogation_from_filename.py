"""
Detector complementario de derogaciones a partir del NOMBRE del archivo.

Algunos archivos del corpus tienen marcas explicitas de derogacion en su
propio nombre, puestas por el municipio. Ejemplos reales:

  "10902-2016 - [NORMA DEROGADA] Adhesión Ley Provincial..."
  "12740-2019 - Ordenanza Tarifaria Anual (DEROGADA).txt"
  "12760-2019 - ... (Derogada).txt"
  "12905-2020 - [Derogada] Modif. Orza. ..."
  "13073-2020 - ... (Norma derogada).txt"
  "2222-1996 - ... _DEROGADA.txt"               <- caso del guion bajo
  "3530-2000 - ... DEROGADA.txt"

Este modulo es COMPLEMENTARIO al detector por texto (derogation_detector.py).

Decisiones de diseño:

  1. Buscamos la subcadena "DEROGAD" + sufijo (A|O|AS|OS) sin usar \\b.
     Razon: en Python re, el guion bajo "_" forma parte de \\w, asi que
     \\b no matchea entre "_" y "D". El archivo "_DEROGADA.txt" se nos
     escapaba silenciosamente. Sin \\b, el riesgo de falso positivo es
     practicamente nulo (no hay palabras en castellano que contengan
     "derogad" y no sean derivados de derogar).

  2. Construimos el doc_id usando el formato del chunker
     (tipo_norma_NUM_ANIO) parseando NUM-ANIO desde el inicio del nombre
     y el tipo desde la carpeta padre.
"""
import re
from pathlib import Path


# Patron simple sin \b: matchea DEROGADA, DEROGADO, DEROGADAS, DEROGADOS
# en cualquier contexto (entre _, parentesis, corchetes, espacios, etc.)
PATRON_DEROGAD = re.compile(r"DEROGAD[AO]S?", re.IGNORECASE)

# Patron para extraer NUM-ANIO al inicio del nombre del archivo
PATRON_NUM_ANIO = re.compile(r"^(\d+)-(\d{4})\s*-")


def es_derogada_por_filename(nombre_archivo: str) -> bool:
    """True si el nombre del archivo contiene marca de derogacion."""
    return bool(PATRON_DEROGAD.search(nombre_archivo))


def parsear_doc_id_desde_filename(nombre_archivo: str, tipo_norma: str = "ordenanza") -> str:
    """
    Construye el doc_id en el formato del chunker.
    Devuelve None si el nombre no sigue el patron NUM-ANIO.
    """
    m = PATRON_NUM_ANIO.match(nombre_archivo)
    if not m:
        return None
    numero = int(m.group(1))
    anio = int(m.group(2))
    return f"{tipo_norma.lower()}_{numero}_{anio}"


def detectar_derogados_por_filename(corpus_dir, verbose: bool = False) -> list:
    """
    Recorre el corpus y devuelve eventos derogatorios detectados por
    marca en el nombre del archivo.

    Cada evento:
      {
        "doc_id_derogado": "ordenanza_2222_1996",
        "nombre_archivo": "...",
        "tipo_norma": "ordenanza",
        "marca_encontrada": "DEROGADA",
        "fuente": "filename",
      }
    """
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.exists():
        raise FileNotFoundError(f"No existe {corpus_dir}")

    eventos = []
    for subdir in corpus_dir.iterdir():
        if not subdir.is_dir():
            continue
        tipo_carpeta = subdir.name.lower()
        tipo_normalizado = (tipo_carpeta
                            .replace("ó", "o")
                            .replace("á", "a")
                            .replace("í", "i")
                            .replace("é", "e")
                            .replace("ú", "u"))

        for archivo in subdir.rglob("*.txt"):
            nombre = archivo.name
            match = PATRON_DEROGAD.search(nombre)
            if not match:
                continue

            doc_id = parsear_doc_id_desde_filename(nombre, tipo_normalizado)
            if not doc_id:
                if verbose:
                    print(f"  [filename detector] WARN: marca en '{nombre}' "
                          f"pero no se pudo parsear NUM-ANIO")
                continue

            eventos.append({
                "doc_id_derogado": doc_id,
                "nombre_archivo": nombre,
                "tipo_norma": tipo_normalizado,
                "marca_encontrada": match.group(0),
                "fuente": "filename",
            })
            if verbose:
                print(f"  [filename detector] {doc_id} <- '{match.group(0)}' en {nombre[:60]}")

    return eventos


# ============================================================
# TESTS SINTETICOS
# ============================================================

if __name__ == "__main__":
    import sys

    print("Test 1: deteccion de DEROGADA en distintas variantes (incluye _DEROGADA)")
    casos_positivos = [
        "10902-2016 - [NORMA DEROGADA] Adhesión Ley.txt",
        "12740-2019 - Ordenanza Tarifaria Anual (DEROGADA).txt",
        "12760-2019 - Modif. Orza Nº4166_01 (Derogada).txt",
        "12905-2020 - [Derogada] Modif. Orza.txt",
        "13073-2020 - Modificación tarifas (Norma derogada).txt",
        "2222-1996 - Exencion automotores antiguos _DEROGADA.txt",  # caso critico
        "3530-2000 - Venta ambulante DEROGADA.txt",
        "107-2006 - Gastos de representación (Derogada).txt",
    ]
    for nombre in casos_positivos:
        assert es_derogada_por_filename(nombre), f"FALLO: no detectado '{nombre}'"
    print(f"  {len(casos_positivos)}/{len(casos_positivos)} casos detectados correctamente.")
    print("  OK Test 1\n")

    print("Test 2: no falsea con nombres sin marca")
    casos_negativos = [
        "10728-2015 - Estructura Orgánica Municipal.txt",
        "11698-2017 - Estructura Orgánica 2018-2019.txt",
        "14528-2023 - sin_titulo.txt",
        "94-1984 - CODIGO DE FALTAS.txt",
    ]
    for nombre in casos_negativos:
        assert not es_derogada_por_filename(nombre), f"FALLO: falso positivo en '{nombre}'"
    print(f"  {len(casos_negativos)}/{len(casos_negativos)} casos correctamente no detectados.")
    print("  OK Test 2\n")

    print("Test 3: parseo de doc_id desde filename")
    casos = [
        ("10902-2016 - [NORMA DEROGADA] Adhesión.txt", "ordenanza", "ordenanza_10902_2016"),
        ("2222-1996 - Exencion automotores _DEROGADA.txt", "ordenanza", "ordenanza_2222_1996"),
        ("107-2006 - Gastos de representación.txt", "resolucion", "resolucion_107_2006"),
        ("30-2022 - Algun titulo.txt", "comunicacion", "comunicacion_30_2022"),
    ]
    for nombre, tipo, esperado in casos:
        obtenido = parsear_doc_id_desde_filename(nombre, tipo)
        assert obtenido == esperado, f"FALLO: '{nombre}' -> {obtenido}, esperaba {esperado}"
    print(f"  {len(casos)}/{len(casos)} casos parseados correctamente.")
    print("  OK Test 3\n")

    print("Test 4: nombre sin patron NUM-ANIO devuelve None")
    casos_invalidos = [
        "archivo_sin_estructura.txt",
        "Algun documento.txt",
        "12973 - sin guion antes del anio.txt",
    ]
    for nombre in casos_invalidos:
        assert parsear_doc_id_desde_filename(nombre) is None, f"FALLO: '{nombre}' no debio matchear"
    print(f"  {len(casos_invalidos)}/{len(casos_invalidos)} casos invalidos correctamente rechazados.")
    print("  OK Test 4\n")

    print("Test 5: variantes de capitalizacion y pluralidad")
    assert es_derogada_por_filename("test DEROGADA real.txt") == True
    assert es_derogada_por_filename("test derogada real.txt") == True
    assert es_derogada_por_filename("test Derogada real.txt") == True
    assert es_derogada_por_filename("test DEROGADO real.txt") == True
    assert es_derogada_por_filename("test DEROGADAS real.txt") == True
    assert es_derogada_por_filename("test DEROGADOS real.txt") == True
    # Edge case del bug original:
    assert es_derogada_por_filename("test_DEROGADA.txt") == True, \
        "FALLO: el guion bajo seguia bloqueando la deteccion"
    print("  OK Test 5 (incluye fix del _DEROGADA)\n")

    # Si se paso un path al corpus, correr deteccion real
    if len(sys.argv) > 1:
        corpus = sys.argv[1]
        print(f"Test 6: deteccion sobre corpus real ({corpus})")
        eventos = detectar_derogados_por_filename(corpus, verbose=True)
        print(f"\n  Total eventos: {len(eventos)}")
        print(f"  Doc IDs unicos: {len(set(e['doc_id_derogado'] for e in eventos))}")
        print("  OK Test 6\n")

    print("Todos los tests pasaron.")
