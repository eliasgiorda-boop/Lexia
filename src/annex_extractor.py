"""
Extractor de anexos en documentos del Digesto Municipal.

Este modulo NO modifica parser.py. Recibe el cuerpo_limpio que devuelve
clean_body() y extrae los anexos como entidades separadas.

Cada anexo tiene la MISMA forma que un articulo:
  {num, label, texto, start, end, char_count}

Esto permite que el chunker los procese con la misma maquinaria de
sub-chunking que ya existe para articulos largos.

Reglas de deteccion:

  - Patron: ANEXO en MAYUSCULAS, en linea propia, seguido de numero romano.
    Ej: "ANEXO I", "ANEXO II", "ANEXO III"...
  - Las menciones internas tipo "forma parte como Anexo I" (capitalizacion
    mixta o embebidas en una oracion) NO son inicios de anexo.
  - El anexo va desde su encabezado hasta el inicio del siguiente anexo,
    o hasta el fin del cuerpo si es el ultimo.
"""
import re


# Patron estricto: ANEXO en mayusculas + numero romano + fin de linea
# El re.MULTILINE hace que ^ y $ matcheen inicio/fin de cada linea.
PATRON_ANEXO = re.compile(
    r"^\s*(ANEXO\s+([IVXLCDM]+))\s*$",
    re.MULTILINE
)


def _romano_a_int(romano: str) -> int:
    """Convierte un numero romano a entero. Para ordenar anexos."""
    valores = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = 0
    prev = 0
    for char in reversed(romano.upper()):
        valor = valores.get(char, 0)
        if valor < prev:
            total -= valor
        else:
            total += valor
        prev = valor
    return total


def extraer_anexos(cuerpo_limpio: str) -> list:
    """
    Detecta y devuelve los anexos del documento.

    Devuelve una lista de dicts ordenados por aparicion en el texto:
      [
        {
          "num": "I",
          "num_int": 1,         # para ordenar/filtrar
          "label": "ANEXO I",
          "texto": "...",       # contenido del anexo sin el encabezado
          "start": int,
          "end": int,
          "char_count": int,
        },
        ...
      ]
    Si no hay anexos, devuelve [].
    """
    if not cuerpo_limpio:
        return []

    matches = list(PATRON_ANEXO.finditer(cuerpo_limpio))
    if not matches:
        return []

    anexos = []
    for i, match in enumerate(matches):
        label = match.group(1).strip()
        num_romano = match.group(2)
        start = match.start()

        # El contenido empieza despues del encabezado
        contenido_start = match.end()

        # El anexo termina donde empieza el siguiente, o al fin del cuerpo
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(cuerpo_limpio)

        texto = cuerpo_limpio[contenido_start:end].strip()

        anexos.append({
            "num": num_romano,
            "num_int": _romano_a_int(num_romano),
            "label": label,
            "texto": texto,
            "start": start,
            "end": end,
            "char_count": end - start,
        })

    return anexos


if __name__ == "__main__":
    print("Test 1: documento con un solo anexo al final")
    cuerpo1 = """ARTÍCULO 1º.- Algo del articulo 1.
ARTÍCULO 2º.- Algo del articulo 2, que menciona el Anexo I como referencia.
ARTÍCULO 3º.- Comuniquese.

ANEXO I
Contenido del anexo
linea 2 del anexo
linea 3 del anexo"""
    anexos1 = extraer_anexos(cuerpo1)
    print(f"  Anexos detectados: {len(anexos1)}")
    assert len(anexos1) == 1, f"FALLO Test 1: esperaba 1 anexo, obtuvo {len(anexos1)}"
    assert anexos1[0]["num"] == "I"
    assert "Contenido del anexo" in anexos1[0]["texto"]
    assert "Anexo I como referencia" not in anexos1[0]["texto"], "El anexo no debe incluir la mencion interna"
    print(f"  Anexo I: {anexos1[0]['char_count']} chars")
    print("  OK Test 1\n")

    print("Test 2: documento con multiples anexos")
    cuerpo2 = """ARTÍCULO 1º.- Aprueba algo.

ANEXO I
Texto del primer anexo.
Mas texto del primer anexo.

ANEXO II
Texto del segundo anexo.

ANEXO III
Texto del tercer anexo."""
    anexos2 = extraer_anexos(cuerpo2)
    print(f"  Anexos detectados: {len(anexos2)}")
    assert len(anexos2) == 3, f"FALLO Test 2: esperaba 3 anexos, obtuvo {len(anexos2)}"
    nums = [a["num"] for a in anexos2]
    assert nums == ["I", "II", "III"], f"FALLO Test 2: orden {nums}"
    assert "Texto del primer" in anexos2[0]["texto"]
    assert "Texto del segundo" in anexos2[1]["texto"]
    assert "Texto del tercer" in anexos2[2]["texto"]
    # El primer anexo NO debe contener texto del segundo
    assert "segundo" not in anexos2[0]["texto"], "Anexo I se solapa con Anexo II"
    print(f"  Orden: {nums}")
    print(f"  Tamanos: {[a['char_count'] for a in anexos2]}")
    print("  OK Test 2\n")

    print("Test 3: documento sin anexos (no debe falsear)")
    cuerpo3 = """ARTÍCULO 1º.- Aprueba algo que menciona como Anexo I (pero no es).
ARTÍCULO 2º.- Comuniquese."""
    anexos3 = extraer_anexos(cuerpo3)
    print(f"  Anexos detectados: {len(anexos3)}")
    assert len(anexos3) == 0, f"FALLO Test 3: esperaba 0, obtuvo {len(anexos3)}"
    print("  OK Test 3\n")

    print("Test 4: mencion interna con mayusculas en medio de una oracion")
    # Edge case: el ANEXO mencionado en una oracion no debe detectarse
    cuerpo4 = """ARTÍCULO 1º.- Aprueba algo segun ANEXO I que se adjunta a continuacion.
ARTÍCULO 2º.- Comuniquese."""
    anexos4 = extraer_anexos(cuerpo4)
    # En este caso, "ANEXO I" no esta en una linea propia, esta embebido.
    # Con la regex actual (^...$ y MULTILINE), no deberia matchear.
    print(f"  Anexos detectados: {len(anexos4)}")
    assert len(anexos4) == 0, f"FALLO Test 4: esperaba 0 (mencion embebida), obtuvo {len(anexos4)}"
    print("  OK Test 4\n")

    print("Test 5: conversion de numeros romanos")
    assert _romano_a_int("I") == 1
    assert _romano_a_int("IV") == 4
    assert _romano_a_int("IX") == 9
    assert _romano_a_int("XII") == 12
    assert _romano_a_int("XLII") == 42
    print("  OK Test 5\n")

    print("Todos los tests pasaron.")
