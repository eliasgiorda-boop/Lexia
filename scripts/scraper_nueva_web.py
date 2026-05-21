"""
Scraper de mantenimiento para digesto.cdsma.gob.ar (web nueva).

Consume el endpoint JSON Laravel /get-statutes en lugar de scrapear HTML.
Mucho mas robusto y rapido que el approach HTML scraping.

ARQUITECTURA:

  1. GET /consulta-digesto?...    -> obtiene cookies de sesion + csrf_token
                                     desde el meta tag <meta name="csrf-token">
  2. POST /get-statutes           -> con cookies + header X-XSRF-TOKEN,
                                     payload JSON con filtros y paginacion
  3. Por cada norma del JSON, descargar /normas/{id}.pdf
  4. Extraer texto con pdfminer.six
  5. Validar pasando por chunk_document antes de guardar

DECISIONES DE DISENO:

  1. Heuristica por fecha: solo procesa normas con fecha posterior a
     (hoy - ventana_meses). Default 2 meses. Para primera corrida pasar
     valor grande (ej: 24).

  2. Header sintetico: el .txt tiene un header arriba del texto del PDF
     compatible con parser.py (mojibake incluido para que parse_header lo
     reconozca como los archivos del scraper viejo).

  3. Validacion: cada .txt generado pasa por chunk_document() antes de
     guardarse. Si tira excepcion o no parsea tipo_norma, va a _fallidos/.

  4. Idempotente: si la norma ya existe en el corpus, no la re-descarga.

USO:

  python scripts/scraper_nueva_web.py                       # ventana 2 meses
  python scripts/scraper_nueva_web.py --ventana-meses 24    # primera corrida
  python scripts/scraper_nueva_web.py --dry-run             # no guarda, solo reporta
  python scripts/scraper_nueva_web.py --tipos Ordenanza     # solo un tipo

REQUISITOS:
  - requests (pip install requests --break-system-packages)
  - pdfminer.six (pip install pdfminer.six --break-system-packages)
"""
import argparse
import re
import sys
import time
from datetime import datetime, timedelta
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# Agregar src al path para poder importar chunker
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    import requests
except ImportError:
    print("ERROR: requests no esta instalado.")
    print("       Corre: pip install requests --break-system-packages")
    sys.exit(1)

try:
    import pdfminer.high_level
except ImportError:
    print("ERROR: pdfminer.six no esta instalado.")
    print("       Corre: pip install pdfminer.six --break-system-packages")
    sys.exit(1)

from chunker import chunk_document

# === Configuracion ===
BASE_URL = "https://digesto.cdsma.gob.ar"
ENDPOINT_LISTA = BASE_URL + "/consulta-digesto"
ENDPOINT_STATUTES = BASE_URL + "/get-statutes"
PDF_URL_TEMPLATE = BASE_URL + "/normas/{id}.pdf"

CORPUS_DIR = Path(os.getenv("CORPUS_PATH", "."))
FALLIDOS_DIR = PROJECT_ROOT / "data" / "_scraper_fallidos"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 digesto-search-scraper/1.0"

# Tipos a procesar (deben coincidir con los nombres de carpeta en CORPUS_DIR)
TIPOS_NORMA = ["Ordenanza", "Resolución", "Comunicación"]

# Meses en espanol (para parsear fechas del JSON tipo "19 mar. 2026")
MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


# ============================================================
# CLIENTE HTTP CON SESION
# ============================================================

class DigestoClient:
    """Cliente HTTP que mantiene sesion Laravel + token CSRF."""

    def __init__(self, verbose=False):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-AR,es;q=0.9",
        })
        self.csrf_token = None
        self.verbose = verbose

    def iniciar_sesion(self, tipo="Ordenanza", alcance="General"):
        """
        Hace GET a /consulta-digesto para obtener cookies + csrf_token.
        Devuelve True si todo OK, False si fallo.
        """
        url = f"{ENDPOINT_LISTA}?ano=desc&mostrar=10&tipo={tipo}&alcance={alcance}"
        if self.verbose:
            print(f"  [sesion] GET {url}")
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [sesion] ERROR al hacer GET: {e}")
            return False

        # Extraer csrf_token del meta tag
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', resp.text)
        if not m:
            print(f"  [sesion] ERROR: no se encontro meta csrf-token en el HTML")
            return False
        self.csrf_token = m.group(1)

        if self.verbose:
            print(f"  [sesion] CSRF token: {self.csrf_token[:20]}...")
            print(f"  [sesion] Cookies: {list(self.session.cookies.keys())}")
        return True

    def get_statutes(self, tipo, alcance="General", page=1, show=50):
        """
        POST a /get-statutes con el payload + headers correctos.
        Devuelve el JSON decodificado o None si fallo.
        """
        if not self.csrf_token:
            print("  [statutes] ERROR: no hay csrf_token, llamar a iniciar_sesion primero")
            return None

        # El header X-XSRF-TOKEN se construye URL-decodificando la cookie XSRF-TOKEN
        # requests lo hace automaticamente si pasamos el header manualmente desde la cookie.
        xsrf_cookie = self.session.cookies.get("XSRF-TOKEN")
        if not xsrf_cookie:
            print("  [statutes] ERROR: no hay cookie XSRF-TOKEN")
            return None

        # Laravel espera el token URL-decoded
        from urllib.parse import unquote
        xsrf_token_decoded = unquote(xsrf_cookie)

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "X-XSRF-TOKEN": xsrf_token_decoded,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": f"{ENDPOINT_LISTA}?ano=desc&mostrar=10&tipo={tipo}&alcance={alcance}",
        }

        payload = {
            "category": None,
            "keyword": "",
            "order": "desc",
            "page": page,
            "scope": alcance,
            "show": str(show),
            "tags": [],
            "type": tipo,
        }

        if self.verbose:
            print(f"  [statutes] POST page={page} type={tipo} scope={alcance}")

        try:
            resp = self.session.post(ENDPOINT_STATUTES, json=payload,
                                     headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [statutes] ERROR HTTP: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"  [statutes] Status: {e.response.status_code}")
                print(f"  [statutes] Body: {e.response.text[:500]}")
            return None

        try:
            return resp.json()
        except ValueError as e:
            print(f"  [statutes] ERROR parseando JSON: {e}")
            return None

    def descargar_pdf(self, pdf_url, max_intentos=3):
        """Descarga un PDF con reintentos. Devuelve bytes o None."""
        for intento in range(1, max_intentos + 1):
            try:
                resp = self.session.get(pdf_url, timeout=60)
                resp.raise_for_status()
                return resp.content
            except requests.RequestException as e:
                if intento == max_intentos:
                    print(f"    ERROR descarga ({intento}/{max_intentos}): {e}")
                    return None
                time.sleep(2 * intento)
        return None


# ============================================================
# FUNCIONES DE PROCESAMIENTO
# ============================================================

def parsear_fecha_es(fecha_str):
    """Parsea '19 mar. 2026' -> datetime. Devuelve None si falla."""
    m = re.match(r"(\d+)\s+([a-z]{3})\.?\s+(\d{4})", fecha_str.lower())
    if not m:
        return None
    dia = int(m.group(1))
    mes_str = m.group(2)
    anio = int(m.group(3))
    mes = MESES_ES.get(mes_str)
    if not mes:
        return None
    try:
        return datetime(anio, mes, dia)
    except ValueError:
        return None


def descubrir_normas(client, tipo, alcance="General", verbose=False):
    """
    Itera todas las paginas del endpoint /get-statutes para un tipo+alcance.
    Devuelve lista de dicts con datos normalizados.
    """
    todas = []
    page = 1
    while True:
        data = client.get_statutes(tipo, alcance=alcance, page=page, show=50)
        if data is None:
            print(f"  [descubrir] ERROR en pagina {page}, deteniendo.")
            break

        statutes = data.get("statutes", [])
        if not statutes:
            if verbose:
                print(f"  [descubrir] Pagina {page} vacia, fin.")
            break

        for s in statutes:
            # Estructura del JSON: id, year, number, date, description, etc.
            fecha = parsear_fecha_es(s.get("date", ""))
            if not fecha:
                if verbose:
                    print(f"  [descubrir] WARN: no se pudo parsear fecha de {s.get('id')}: {s.get('date')}")
                continue

            todas.append({
                "id_pdf": s.get("id"),  # ej "2026-3-15549"
                "numero": int(s.get("number")),
                "anio": int(s.get("year")),
                "fecha": fecha,
                "resumen": s.get("description") or s.get("summary") or "",
                "url_pdf": PDF_URL_TEMPLATE.format(id=s.get("id")),
                "raw": s,  # por si queremos extraer mas campos despues
            })

        if verbose:
            print(f"  [descubrir] Pagina {page}: {len(statutes)} normas. "
                  f"current_page={data.get('current_page')} last_page={data.get('last_page')}")

        # Avanzar segun el JSON
        last_page = data.get("last_page", page)
        if page >= last_page:
            break
        page += 1
        time.sleep(0.3)  # courtesy delay entre paginas

    return todas


def filtrar_por_ventana(normas, ventana_meses):
    """Filtra normas con fecha posterior a hoy - ventana_meses."""
    if ventana_meses <= 0:
        return normas
    limite = datetime.now() - timedelta(days=ventana_meses * 30)
    return [n for n in normas if n["fecha"] >= limite]


def filtrar_existentes(normas, tipo_carpeta, corpus_dir):
    """Filtra normas que ya existen en el corpus local."""
    subdir = corpus_dir / tipo_carpeta
    if not subdir.exists():
        return normas
    existentes = set()
    for f in subdir.glob("*.txt"):
        m = re.match(r"^(\d+)-(\d{4})\s*-", f.name)
        if m:
            existentes.add((int(m.group(1)), int(m.group(2))))
    return [n for n in normas if (n["numero"], n["anio"]) not in existentes]


def extraer_texto_pdf(pdf_bytes):
    """Extrae texto del PDF. Devuelve string o None."""
    pdf_path = Path("_temp_pdf_extract.pdf")
    try:
        pdf_path.write_bytes(pdf_bytes)
        texto = pdfminer.high_level.extract_text(str(pdf_path))
        return texto
    except Exception as e:
        print(f"    ERROR extraccion PDF: {e}")
        return None
    finally:
        if pdf_path.exists():
            pdf_path.unlink()


def construir_txt(norma, texto_pdf, tipo_normalizado):
    """
    Construye el .txt con header sintetico arriba del texto del PDF.

    El header usa mojibake para coincidir con el formato del scraper viejo
    y que parse_header() lo reconozca como hasta ahora.
    """
    unid = f"SCRAPER-{tipo_normalizado.upper()[:3]}-{norma['numero']}-{norma['anio']}"
    header_lines = [
        f"# UNID: {unid}",
        f"# Descargado-de: {norma['url_pdf']}",
        f"# Fecha-descarga: {datetime.now().isoformat(timespec='seconds')}",
        "",
        # Linea con mojibake para que parse_header() la reconozca
        f"{tipo_normalizado} N° {norma['numero']}, Año {norma['anio']}",
        "",
        f"Resumen: {norma['resumen']}",
        "",
        "---",
        "",
    ]
    return "\n".join(header_lines) + texto_pdf


def validar_txt(contenido_txt):
    """
    Valida que el .txt generado pase por chunk_document() sin excepcion
    y que parse_header() extraiga tipo_norma.

    Devuelve (ok: bool, info: dict | str).
    """
    temp_path = Path("_temp_txt_valid.txt")
    try:
        temp_path.write_text(contenido_txt, encoding="utf-8")
        chunks = chunk_document(str(temp_path), verbose=False)
        if not chunks:
            return False, "chunk_document devolvio lista vacia"
        primer = chunks[0]
        tipo_parseado = primer["metadata"].get("tipo_norma")
        if not tipo_parseado:
            return False, "tipo_norma no parseado por parse_header"
        return True, {
            "chunks": len(chunks),
            "tipo_parseado": tipo_parseado,
            "numero_parseado": primer["metadata"].get("numero"),
            "anio_parseado": primer["metadata"].get("anio"),
        }
    except Exception as e:
        return False, f"excepcion: {type(e).__name__}: {e}"
    finally:
        if temp_path.exists():
            temp_path.unlink()


def construir_nombre_archivo(norma):
    """Construye 'NNNNN-AAAA - <resumen>.txt'."""
    resumen = norma.get("resumen") or "sin_titulo"
    resumen = re.sub(r'[<>:"/\\|?*]', "_", resumen)
    resumen = resumen.strip()[:100]
    return f"{norma['numero']}-{norma['anio']} - {resumen}.txt"


def procesar_norma(client, norma, tipo_normalizado, corpus_dir, dry_run=False, verbose=False):
    """Procesa una norma completa. Devuelve dict de resultado."""
    resultado = {
        "doc_id": f"{tipo_normalizado.lower()}_{norma['numero']}_{norma['anio']}",
        "url_pdf": norma["url_pdf"],
        "status": None,
        "destino": None,
        "error": None,
    }

    # 1. Descargar PDF
    pdf_bytes = client.descargar_pdf(norma["url_pdf"])
    if pdf_bytes is None:
        resultado["status"] = "error_descarga"
        resultado["error"] = "no se pudo descargar el PDF"
        return resultado

    # 2. Extraer texto
    texto = extraer_texto_pdf(pdf_bytes)
    if not texto or len(texto.strip()) < 50:
        resultado["status"] = "error_extraccion"
        resultado["error"] = f"texto extraido vacio o muy corto ({len(texto or '')} chars)"
        return resultado

    # 3. Construir .txt
    contenido = construir_txt(norma, texto, tipo_normalizado)

    # 4. Validar
    ok, info = validar_txt(contenido)
    if not ok:
        resultado["status"] = "fallido_validacion"
        resultado["error"] = info
        if not dry_run:
            FALLIDOS_DIR.mkdir(parents=True, exist_ok=True)
            nombre = construir_nombre_archivo(norma)
            destino = FALLIDOS_DIR / nombre
            destino.write_text(contenido, encoding="utf-8")
            resultado["destino"] = str(destino)
        return resultado

    # 5. Guardar
    if dry_run:
        resultado["status"] = "ok_dry_run"
        resultado["destino"] = "(dry-run, no guardado)"
        resultado["info"] = info
        return resultado

    subdir = corpus_dir / tipo_normalizado
    subdir.mkdir(parents=True, exist_ok=True)
    nombre = construir_nombre_archivo(norma)
    destino = subdir / nombre
    destino.write_text(contenido, encoding="utf-8")
    resultado["status"] = "ok"
    resultado["destino"] = str(destino)
    resultado["info"] = info
    return resultado


def main():
    parser_args = argparse.ArgumentParser(description=__doc__)
    parser_args.add_argument("--ventana-meses", type=int, default=2,
                             help="Ventana de tiempo en meses (default: 2). "
                                  "0 = sin limite.")
    parser_args.add_argument("--dry-run", action="store_true",
                             help="No descarga ni guarda nada, solo reporta.")
    parser_args.add_argument("--verbose", action="store_true",
                             help="Salida detallada.")
    parser_args.add_argument("--tipos", nargs="+",
                             choices=TIPOS_NORMA,
                             default=TIPOS_NORMA,
                             help="Tipos a procesar (default: todos).")
    args = parser_args.parse_args()

    print("=" * 70)
    print("SCRAPER DE MANTENIMIENTO - WEB NUEVA DIGESTO SMA (v2 JSON)")
    print("=" * 70)
    limite_fecha = (datetime.now() - timedelta(days=args.ventana_meses * 30)).date() \
        if args.ventana_meses > 0 else None
    print(f"Ventana de tiempo: {args.ventana_meses} meses "
          f"({'desde ' + str(limite_fecha) if limite_fecha else 'sin limite'})")
    print(f"Tipos:             {', '.join(args.tipos)}")
    print(f"Dry-run:           {args.dry_run}")
    print(f"Corpus destino:    {CORPUS_DIR}")
    print()

    resumen = {
        "descubiertas": 0,
        "en_ventana": 0,
        "ya_existen": 0,
        "ok": 0,
        "fallidas_validacion": 0,
        "errores_descarga": 0,
        "errores_extraccion": 0,
    }

    # Cliente HTTP con sesion compartida
    client = DigestoClient(verbose=args.verbose)

    for tipo in args.tipos:
        print(f"\n[{tipo}]")
        print(f"  Iniciando sesion...")
        if not client.iniciar_sesion(tipo=tipo, alcance="General"):
            print(f"  ERROR: no se pudo iniciar sesion. Saltando {tipo}.")
            continue

        print(f"  Descubriendo normas...")
        normas = descubrir_normas(client, tipo, alcance="General", verbose=args.verbose)
        resumen["descubiertas"] += len(normas)
        print(f"  {len(normas)} normas descubiertas en total.")

        normas_ventana = filtrar_por_ventana(normas, args.ventana_meses)
        resumen["en_ventana"] += len(normas_ventana)
        print(f"  {len(normas_ventana)} dentro de la ventana.")

        antes = len(normas_ventana)
        normas_proc = filtrar_existentes(normas_ventana, tipo, CORPUS_DIR)
        ya_existen = antes - len(normas_proc)
        resumen["ya_existen"] += ya_existen
        print(f"  {ya_existen} ya existen en el corpus.")
        print(f"  {len(normas_proc)} a procesar.")

        for i, norma in enumerate(normas_proc, 1):
            print(f"\n  [{i}/{len(normas_proc)}] {tipo} {norma['numero']}/{norma['anio']} ({norma['fecha'].date()})")
            print(f"    URL: {norma['url_pdf']}")
            if args.verbose:
                print(f"    Resumen: {norma['resumen'][:80]}")

            res = procesar_norma(client, norma, tipo, CORPUS_DIR,
                                 dry_run=args.dry_run, verbose=args.verbose)

            if res["status"] in ("ok", "ok_dry_run"):
                resumen["ok"] += 1
                print(f"    OK -> {res['destino']}")
                if "info" in res:
                    print(f"    Validacion: {res['info']}")
            elif res["status"] == "fallido_validacion":
                resumen["fallidas_validacion"] += 1
                print(f"    FALLO VALIDACION: {res['error']}")
                if res["destino"]:
                    print(f"    Guardado en: {res['destino']}")
            elif res["status"] == "error_descarga":
                resumen["errores_descarga"] += 1
                print(f"    ERROR DESCARGA: {res['error']}")
            elif res["status"] == "error_extraccion":
                resumen["errores_extraccion"] += 1
                print(f"    ERROR EXTRACCION: {res['error']}")

            time.sleep(0.5)  # courtesy delay entre descargas

    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"Descubiertas en la web:       {resumen['descubiertas']}")
    print(f"Dentro de la ventana:         {resumen['en_ventana']}")
    print(f"Ya existian en el corpus:     {resumen['ya_existen']}")
    print(f"Procesadas OK:                {resumen['ok']}")
    print(f"Fallidas validacion:          {resumen['fallidas_validacion']}")
    print(f"Errores descarga:             {resumen['errores_descarga']}")
    print(f"Errores extraccion:           {resumen['errores_extraccion']}")

    if resumen['fallidas_validacion'] > 0:
        print(f"\nRevisar archivos en: {FALLIDOS_DIR}")


if __name__ == "__main__":
    main()
