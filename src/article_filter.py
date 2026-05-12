"""
Filtro de falsos positivos en la lista de articulos extraidos.

Este modulo NO modifica parser.py. Recibe la lista que devuelve
extract_articles() y descarta articulos espurios.

Estrategia: 2 capas de defensa.

  Capa A (marcador textual):
    El articulo anterior termina con una frase tipo
    "por el siguiente:", "quedara redactado:", etc.

  Capa B (ruptura de secuencia numerica):
    Si articulo[i].num >> articulo[i-1].num
    Y articulo[i+1].num retoma la secuencia.

IMPORTANTE: cada articulo "marcador" solo puede consumir UN falso positivo.
Asi evitamos que despues de una fusion el marcador siga vivo y devore
tambien al articulo legitimo siguiente.
"""
import re


MARCADORES_SUSTITUCION = [
    r"por\s+el\s+siguiente\s*:",
    r"por\s+los\s+siguientes\s*:",
    r"queda(?:ra|ran)?\s+redactad[oa]s?\s+(?:de\s+la\s+siguiente\s+manera|como\s+sigue)\s*:",
    r"se\s+sustituye\s+por\s*:",
    r"que\s+dir[aa]\s*:",
    r"el\s+siguiente\s+texto\s*:",
]
PATRON_SUSTITUCION = re.compile("|".join(MARCADORES_SUSTITUCION), re.IGNORECASE)

UMBRAL_SALTO_NUMERICO = 20


def _detectar_falso_positivo(articulos, i, marcadores_consumidos):
    """
    Devuelve (es_falso, motivo).
    marcadores_consumidos: set de indices cuyo marcador ya consumio un falso.
    """
    if i == 0 or i >= len(articulos):
        return False, ""

    prev_idx = i - 1
    prev = articulos[prev_idx]
    curr = articulos[i]

    # Capa A: marcador textual (solo si no se consumio)
    if prev_idx not in marcadores_consumidos:
        if PATRON_SUSTITUCION.search(prev["texto"]):
            return True, f"marcador_textual_en_art_{prev['num']}"

    # Capa B: ruptura de secuencia
    salto = curr["num"] - prev["num"]
    if salto > UMBRAL_SALTO_NUMERICO and i + 1 < len(articulos):
        siguiente = articulos[i + 1]
        if siguiente["num"] == prev["num"] + 1:
            return True, f"ruptura_secuencia_{prev['num']}_a_{curr['num']}_a_{siguiente['num']}"

    return False, ""


def _fusionar(articulos, i):
    """Fusiona articulos[i] dentro de articulos[i-1]."""
    prev = articulos[i - 1]
    falso = articulos[i]

    texto_fusionado = (
        prev["texto"].rstrip()
        + "\n\n"
        + falso["label"]
        + " "
        + falso["texto"]
    )

    prev["texto"] = texto_fusionado
    prev["end"] = falso["end"]
    prev["char_count"] = prev["end"] - prev["start"]

    return articulos[:i] + articulos[i + 1:]


def filtrar_falsos_positivos(articulos, verbose=False):
    """
    Devuelve (articulos_limpios, reporte).
    No muta la lista de entrada.
    """
    if not articulos:
        return [], []

    articulos = [dict(a) for a in articulos]
    reporte = []
    marcadores_consumidos = set()

    i = 1
    while i < len(articulos):
        es_falso, motivo = _detectar_falso_positivo(articulos, i, marcadores_consumidos)
        if es_falso:
            descartado = articulos[i]
            idx_anterior = i - 1
            reporte.append({
                "num_descartado": descartado["num"],
                "label_descartado": descartado["label"],
                "fusionado_con_num": articulos[idx_anterior]["num"],
                "motivo": motivo,
                "chars_fusionados": descartado["char_count"],
            })
            if verbose:
                print(f"  [filtro] descartando art_{descartado['num']} "
                      f"({motivo}) -> fusionado con art_{articulos[idx_anterior]['num']}")

            if motivo.startswith("marcador_textual"):
                marcadores_consumidos.add(idx_anterior)

            articulos = _fusionar(articulos, i)
        else:
            i += 1

    return articulos, reporte


if __name__ == "__main__":
    print("Test 1: caso del falso positivo art_73 (debe quedar [7, 8, 9])")
    articulos_test = [
        {"num": 7, "label": "ARTÍCULO 7º.-", "texto": "Sanciones...", "start": 0, "end": 100, "char_count": 100},
        {"num": 8, "label": "ARTÍCULO 8º.-", "texto": "SUSTITÚYESE el Artículo 73º del Código de Faltas, por el siguiente:", "start": 100, "end": 200, "char_count": 100},
        {"num": 73, "label": "ARTICULO 73º.", "texto": "La instalación...", "start": 200, "end": 300, "char_count": 100},
        {"num": 9, "label": "ARTÍCULO 9º.-", "texto": "Comuníquese...", "start": 300, "end": 400, "char_count": 100},
    ]
    limpios, reporte = filtrar_falsos_positivos(articulos_test, verbose=True)
    print(f"  Antes: {len(articulos_test)} articulos, despues: {len(limpios)}")
    print(f"  Numeros finales: {[a['num'] for a in limpios]}")
    assert [a['num'] for a in limpios] == [7, 8, 9], f"FALLO Test 1: quedo {[a['num'] for a in limpios]}"
    print("  OK Test 1\n")

    print("Test 2: sin falsos positivos (debe quedar [1, 2, 3])")
    articulos_ok = [
        {"num": 1, "label": "ARTÍCULO 1º.-", "texto": "Aprueba...", "start": 0, "end": 100, "char_count": 100},
        {"num": 2, "label": "ARTÍCULO 2º.-", "texto": "Establece...", "start": 100, "end": 200, "char_count": 100},
        {"num": 3, "label": "ARTÍCULO 3º.-", "texto": "Comuníquese...", "start": 200, "end": 300, "char_count": 100},
    ]
    limpios2, _ = filtrar_falsos_positivos(articulos_ok, verbose=True)
    print(f"  Numeros finales: {[a['num'] for a in limpios2]}")
    assert [a['num'] for a in limpios2] == [1, 2, 3], "FALLO Test 2"
    print("  OK Test 2\n")

    print("Test 3: ruptura de secuencia sin marcador (debe filtrar por capa B)")
    articulos_b = [
        {"num": 3, "label": "ART 3", "texto": "Algo.", "start": 0, "end": 100, "char_count": 100},
        {"num": 4, "label": "ART 4", "texto": "Algo sin marcador.", "start": 100, "end": 200, "char_count": 100},
        {"num": 99, "label": "ART 99", "texto": "Texto huerfano.", "start": 200, "end": 300, "char_count": 100},
        {"num": 5, "label": "ART 5", "texto": "Comuniquese.", "start": 300, "end": 400, "char_count": 100},
    ]
    limpios3, _ = filtrar_falsos_positivos(articulos_b, verbose=True)
    print(f"  Numeros finales: {[a['num'] for a in limpios3]}")
    assert [a['num'] for a in limpios3] == [3, 4, 5], "FALLO Test 3"
    print("  OK Test 3\n")

    print("Todos los tests pasaron.")
