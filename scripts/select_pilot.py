"""
Selector de muestra piloto del corpus del Digesto Municipal.

Selecciona 100 documentos estratificados (tipo x tamano) desde el corpus
completo y los copia a data/samples/pilot/ para validacion del chunker.

Estratificacion:
  - Por tipo de norma: Ordenanza / Resolucion / Comunicacion
  - Por tamano: corto (<2KB) / medio (2-20KB) / largo (>20KB)
  - Adicional: garantizar diversidad temporal (al menos N docs <2000 y N >=2015)

Salidas:
  - data/samples/pilot/<archivos copiados>
  - data/samples/pilot/_manifest.json con info de la seleccion

El script es REPRODUCIBLE: con la misma seed, devuelve los mismos 100 docs.
"""
import json
import random
import re
import shutil
from collections import defaultdict
from pathlib import Path


# === CONFIGURACION ===

CORPUS_DIR = Path(r"E:\Bosio\Normativa Total\Por Tipo Normativa")
OUTPUT_DIR = Path("data/samples/pilot")
MANIFEST_PATH = OUTPUT_DIR / "_manifest.json"
SEED = 42

# Umbrales de tamano en bytes (alineados con la regla 82/12/6 del chunker)
UMBRAL_CORTO_BYTES = 2_000
UMBRAL_MEDIO_BYTES = 20_000

# Cuotas por estrato (tipo x tamano). Suman 100.
CUOTAS = {
    "Ordenanza":    {"corto": 60, "medio": 20, "largo": 8},
    "Resolución":   {"corto": 5,  "medio": 2,  "largo": 1},
    "Comunicación": {"corto": 3,  "medio": 1,  "largo": 0},
}

# Diversidad temporal: por cada tipo, al menos N docs viejos y N recientes
# (siempre que el corpus tenga suficientes)
MIN_VIEJOS_POR_TIPO = 2   # anio < 2000
MIN_RECIENTES_POR_TIPO = 2  # anio >= 2015


# === FUNCIONES ===

def parsear_anio_desde_nombre(nombre_archivo: str):
    """
    Extrae el anio del nombre del archivo.
    Patron esperado: "NUMERO-ANIO - titulo.txt"
    Ejemplo: "10328-2014 - Registro de Casas...txt" -> 2014
    Si no matchea, devuelve None.
    """
    # Patron principal: numero-anio al inicio
    m = re.match(r"^\d+-(\d{4})\s*-", nombre_archivo)
    if m:
        anio = int(m.group(1))
        # Validacion sanity: el corpus es municipal, anios entre 1960 y hoy
        if 1960 <= anio <= 2030:
            return anio
    return None


def clasificar_tamano(bytes_size: int) -> str:
    if bytes_size < UMBRAL_CORTO_BYTES:
        return "corto"
    if bytes_size < UMBRAL_MEDIO_BYTES:
        return "medio"
    return "largo"


def clasificar_epoca(anio):
    if anio is None:
        return "sin_anio"
    if anio < 2000:
        return "viejo"
    if anio < 2015:
        return "medio_temporal"
    return "reciente"


def recolectar_archivos_por_tipo(corpus_dir: Path) -> dict:
    """
    Devuelve dict: {tipo: [(path, size_bytes, anio), ...]}
    Solo incluye archivos .txt.
    """
    resultado = defaultdict(list)
    for subdir in corpus_dir.iterdir():
        if not subdir.is_dir():
            continue
        tipo = subdir.name
        for archivo in subdir.rglob("*.txt"):
            size = archivo.stat().st_size
            anio = parsear_anio_desde_nombre(archivo.name)
            resultado[tipo].append((archivo, size, anio))
    return dict(resultado)


def seleccionar_estrato(candidatos: list, n: int, rng: random.Random) -> list:
    """
    Selecciona n elementos de candidatos garantizando diversidad temporal
    cuando es posible. candidatos es lista de tuplas (path, size, anio).
    """
    if not candidatos or n == 0:
        return []
    if len(candidatos) <= n:
        return list(candidatos)

    # Separar por epoca
    por_epoca = defaultdict(list)
    for c in candidatos:
        por_epoca[clasificar_epoca(c[2])].append(c)

    seleccionados = []

    # Garantizar viejos y recientes (best effort)
    if por_epoca.get("viejo"):
        cupo_viejos = min(MIN_VIEJOS_POR_TIPO, len(por_epoca["viejo"]), n // 3)
        muestra_viejos = rng.sample(por_epoca["viejo"], cupo_viejos)
        seleccionados.extend(muestra_viejos)

    if por_epoca.get("reciente"):
        cupo_recientes = min(MIN_RECIENTES_POR_TIPO, len(por_epoca["reciente"]), n // 3)
        muestra_recientes = rng.sample(por_epoca["reciente"], cupo_recientes)
        seleccionados.extend(muestra_recientes)

    # Rellenar con el resto al azar (excluyendo ya seleccionados)
    seleccionados_set = set(s[0] for s in seleccionados)
    restantes = [c for c in candidatos if c[0] not in seleccionados_set]
    faltan = n - len(seleccionados)
    if faltan > 0 and restantes:
        cupo_rest = min(faltan, len(restantes))
        seleccionados.extend(rng.sample(restantes, cupo_rest))

    return seleccionados


def main():
    print("=" * 70)
    print("SELECTOR DE MUESTRA PILOTO")
    print("=" * 70)

    if not CORPUS_DIR.exists():
        print(f"ERROR: No se encuentra el corpus en {CORPUS_DIR}")
        return

    print(f"\nCorpus: {CORPUS_DIR}")
    print(f"Salida: {OUTPUT_DIR}")
    print(f"Seed:   {SEED}\n")

    rng = random.Random(SEED)

    # Paso 1: recolectar archivos por tipo
    print("[1/4] Recolectando archivos del corpus...")
    por_tipo = recolectar_archivos_por_tipo(CORPUS_DIR)
    for tipo, archivos in por_tipo.items():
        print(f"  {tipo:15} : {len(archivos)} archivos")

    # Paso 2: clasificar por estrato y seleccionar
    print("\n[2/4] Estratificando y seleccionando...")
    seleccionados_globales = []
    reporte_estratos = {}

    for tipo, cuotas_tipo in CUOTAS.items():
        if tipo not in por_tipo:
            print(f"  ADVERTENCIA: tipo {tipo} no encontrado en el corpus")
            continue

        archivos_tipo = por_tipo[tipo]
        # Subdividir por tamano
        por_tam = defaultdict(list)
        for a in archivos_tipo:
            por_tam[clasificar_tamano(a[1])].append(a)

        reporte_estratos[tipo] = {}
        for tam, cuota in cuotas_tipo.items():
            candidatos = por_tam.get(tam, [])
            seleccion = seleccionar_estrato(candidatos, cuota, rng)
            reporte_estratos[tipo][tam] = {
                "disponibles": len(candidatos),
                "cuota_solicitada": cuota,
                "seleccionados": len(seleccion),
            }
            for path, size, anio in seleccion:
                seleccionados_globales.append({
                    "path_origen": str(path),
                    "nombre": path.name,
                    "tipo": tipo,
                    "tamano_clase": tam,
                    "bytes": size,
                    "anio": anio,
                    "epoca": clasificar_epoca(anio),
                })
            print(f"  {tipo:15} {tam:6}: {len(seleccion)}/{cuota} "
                  f"(de {len(candidatos)} disponibles)")

    total = len(seleccionados_globales)
    print(f"\n  TOTAL SELECCIONADO: {total} documentos")

    # Paso 3: copiar archivos
    print(f"\n[3/4] Copiando archivos a {OUTPUT_DIR}...")
    if OUTPUT_DIR.exists():
        respuesta = input(f"  La carpeta {OUTPUT_DIR} ya existe. "
                          f"Borrar contenido previo? [s/N]: ").strip().lower()
        if respuesta == "s":
            shutil.rmtree(OUTPUT_DIR)
            print("  Carpeta previa borrada.")
        else:
            print("  Conservando contenido previo (puede haber duplicados).")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for entry in seleccionados_globales:
        origen = Path(entry["path_origen"])
        destino = OUTPUT_DIR / entry["nombre"]
        shutil.copy2(origen, destino)
        entry["path_destino"] = str(destino)

    print(f"  {total} archivos copiados.")

    # Paso 4: escribir manifest
    print(f"\n[4/4] Escribiendo manifest en {MANIFEST_PATH}...")
    manifest = {
        "seed": SEED,
        "corpus_origen": str(CORPUS_DIR),
        "total_seleccionados": total,
        "cuotas": CUOTAS,
        "reporte_estratos": reporte_estratos,
        "documentos": seleccionados_globales,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"  Manifest escrito ({MANIFEST_PATH.stat().st_size} bytes).")

    # Resumen final
    print("\n" + "=" * 70)
    print("RESUMEN DEL PILOTO")
    print("=" * 70)

    # Por epoca
    por_epoca_cnt = defaultdict(int)
    for d in seleccionados_globales:
        por_epoca_cnt[d["epoca"]] += 1
    print("\nDistribucion por epoca:")
    for ep in ["viejo", "medio_temporal", "reciente", "sin_anio"]:
        if por_epoca_cnt[ep]:
            print(f"  {ep:15}: {por_epoca_cnt[ep]}")

    # Tamanos
    total_bytes = sum(d["bytes"] for d in seleccionados_globales)
    print(f"\nTamano total del piloto: {total_bytes / 1024:.1f} KB "
          f"({total_bytes / 1024 / 1024:.2f} MB)")

    print(f"\nSiguiente paso:")
    print(f"  Correr el chunker sobre los {total} docs y validar resultados.")


if __name__ == "__main__":
    main()
