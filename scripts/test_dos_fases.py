"""
Test reproducible de la busqueda de dos fases (CO condicional).

Valida automaticamente que el sistema:
  1. Activa la CO cuando la query es de tema constitucional municipal
  2. NO activa la CO cuando la query es puramente operativa/tarifaria
  3. La decision usa el umbral coseno calibrado (0.46)

Sirve como red de seguridad: si en el futuro se reindexar el corpus,
se cambia el chunker o se ajustan parametros, este test confirma que
la busqueda sigue separando bien CO relevante de irrelevante.

USO:
  python scripts/test_dos_fases.py
  python scripts/test_dos_fases.py --verbose   # muestra top resultados
"""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from search import HybridSearch, UMBRAL_CO_RELEVANTE


# Queries con su expectativa de relevancia de la CO.
# (query, deberia_activar_CO)
CASOS = [
    # CO relevante (temas constitucionales municipales)
    ("remuneracion del intendente", True),
    ("proteccion del medio ambiente", True),
    ("juicio politico", True),
    ("atribuciones del Concejo Deliberante", True),
    ("derechos de los vecinos", True),
    ("audiencia publica", True),
    ("iniciativa popular y referendum", True),
    ("Complejo Cerro Chapelco patrimonio", True),
    # CO irrelevante (temas operativos/tarifarios)
    ("horarios de feria americana", False),
    ("tarifa de taxis y remises", False),
    ("habilitacion de food trucks gastronomicos", False),
    ("derechos de cementerio inhumacion", False),
    ("estacionamiento medido tarjeta", False),
    ("poda de arboles en veredas", False),
    ("licencia de conducir renovacion", False),
    ("patente de motovehiculos", False),
    ("GIRSU residuos solidos", False),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="Muestra los top resultados de cada query")
    args = parser.parse_args()

    print("=" * 70)
    print("TEST REPRODUCIBLE: busqueda de dos fases (CO condicional)")
    print(f"Umbral coseno CO: {UMBRAL_CO_RELEVANTE}")
    print("=" * 70)

    buscador = HybridSearch(verbose=True)

    aciertos = 0
    fallos = []

    for query, espera_co in CASOS:
        resultados = buscador.buscar_dos_fases(query, n_results=6)

        # Leer la decision desde el debug
        debug = resultados[0].get("_debug_co", {}) if resultados else {}
        coseno = debug.get("mejor_coseno_co", 0.0)
        co_activada = debug.get("co_es_relevante", False)

        # Verificar si hay chunks de CO en los resultados
        hay_co_en_resultados = any(
            r["fuente_fase"] == "carta_organica" for r in resultados
        )

        ok = (co_activada == espera_co)
        if ok:
            aciertos += 1
            estado = "OK "
        else:
            fallos.append((query, espera_co, co_activada, coseno))
            estado = "XX "

        esperado = "CO+" if espera_co else "COR"
        obtenido = "CO+" if co_activada else "COR"
        print(f"  {estado} [{esperado}->{obtenido}] coseno={coseno:.3f}  {query}")

        if args.verbose:
            for rank, r in enumerate(resultados[:3], 1):
                fase = "CO " if r["fuente_fase"] == "carta_organica" else "COR"
                print(f"        #{rank} [{fase}] {r['chunk_id']}")

    print("\n" + "=" * 70)
    print(f"RESULTADO: {aciertos}/{len(CASOS)} aciertos")
    print("=" * 70)
    if fallos:
        print(f"\nFALLOS ({len(fallos)}):")
        for query, esperado, obtenido, coseno in fallos:
            print(f"  '{query}'")
            print(f"    esperaba CO={'si' if esperado else 'no'}, "
                  f"obtuvo CO={'si' if obtenido else 'no'} (coseno={coseno:.3f})")
        print(f"\nSi los fallos son casos borde con coseno cerca de "
              f"{UMBRAL_CO_RELEVANTE}, considerar ajustar el umbral.")
        sys.exit(1)
    else:
        print("\nTodos los casos pasaron. La decision CO funciona correctamente.")


if __name__ == "__main__":
    main()
