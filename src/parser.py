"""
Parser de archivos .txt del Digesto Municipal de San Martin de los Andes.
"""
import re
from typing import Optional


def parse_header(texto: str) -> dict:
    """Extrae metadata del encabezado de un .txt del Digesto."""
    metadata = {
        "unid": None, "url_origen": None, "tipo_norma": None,
        "numero": None, "anio": None, "titulo_corto": None,
        "fecha_publicacion": None, "boletin_oficial": None, "categoria": None,
    }
    
    m = re.search(r"^#\s*UNID:\s*([A-F0-9]+)", texto, re.MULTILINE)
    if m: metadata["unid"] = m.group(1)
    
    m = re.search(r"^#\s*Descargado-de:\s*(\S+)", texto, re.MULTILINE)
    if m: metadata["url_origen"] = m.group(1)
    
    m = re.search(
        r"^(Ordenanza|Resolucion|Resolución|Comunicacion|Comunicación)\s+N°?\s*(\d+),?\s*A[nñ]o\s+(\d{4})",
        texto, re.MULTILINE
    )
    if m:
        tipo = m.group(1).lower().replace("ó", "o")
        metadata["tipo_norma"] = tipo
        metadata["numero"] = m.group(2)
        metadata["anio"] = int(m.group(3))
        resto = texto[m.end():].lstrip("\n\r ")
        if resto:
            primera_linea = resto.split("\n")[0].strip()
            if primera_linea:
                metadata["titulo_corto"] = primera_linea
    
    m = re.search(r"Publicaci[oó]n\s*:\s*(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    if m:
        mes = m.group(1)
        dia = m.group(2)
        anio = m.group(3)
        metadata["fecha_publicacion"] = f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}"
    
    m = re.search(r"Bolet[ií]n Oficial N°\s*(\d+)", texto)
    if m: metadata["boletin_oficial"] = m.group(1)
    
    m = re.search(r"NORMA DE CAR[AÁ]CTER\s+(PARTICULAR|GENERAL)", texto)
    if m: metadata["categoria"] = m.group(1).lower()
    
    return metadata


def clean_body(texto: str) -> str:
    """Limpia el cuerpo del documento quitando navegacion de Lotus y artefactos."""
    match_articulo = re.search(r"ART[ÍI]CULO\s+\d", texto, re.IGNORECASE)
    
    if match_articulo:
        cuerpo = texto[match_articulo.start():]
    else:
        match_norma = re.search(r"NORMA DE CAR[AÁ]CTER\s+(PARTICULAR|GENERAL)\s*\n", texto)
        if match_norma:
            cuerpo = texto[match_norma.end():]
        else:
            cuerpo = texto
    
    slogan_pattern = r'"San Mart[ií]n de los Andes,?\s*Zona no Nuclear'
    match_slogan = re.search(slogan_pattern, cuerpo)
    if match_slogan:
        cuerpo = cuerpo[:match_slogan.start()]
    
    lineas_ruido = {
        "Volver", "Digesto Municipal", "Versión para Imprimir",
        "Información Adicional", "Imprimir"
    }
    lineas = cuerpo.split("\n")
    lineas_limpias = [l for l in lineas if l.strip() not in lineas_ruido]
    cuerpo = "\n".join(lineas_limpias)
    
    cuerpo = re.sub(r"-{3,}", "-", cuerpo)
    cuerpo = re.sub(r"\n\s*\n\s*\n+", "\n\n", cuerpo)
    cuerpo = cuerpo.strip()
    
    return cuerpo


def extract_articles(cuerpo_limpio: str) -> list:
    """Separa el cuerpo limpio en una lista de articulos individuales."""
    patron_articulo = re.compile(
        r"^\s*((?:ART[ÍI]CULO|Art[íi]culo)\s+(\d+)\s*[º°]?\s*[\.\-]\s*(?=-|\s*[A-ZÁÉÍÓÚÑ]))",
        re.MULTILINE
    )
    
    matches = list(patron_articulo.finditer(cuerpo_limpio))
    
    if not matches:
        return []
    
    articulos = []
    
    for i, match in enumerate(matches):
        num = int(match.group(2))
        label = match.group(1).strip()
        start = match.start()
        
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(cuerpo_limpio)
            texto_ultimo = cuerpo_limpio[start:end]
            match_dada = re.search(
                r"\n\s*Dada\s+en\s+la\s+[Ss]ala\s+de\s+[Ss]esiones",
                texto_ultimo
            )
            if match_dada:
                end = start + match_dada.start()
        
        texto_articulo = cuerpo_limpio[start:end]
        match_anexo = re.search(r"\n\s*ANEXO\s+[IVX]+\s*\n", texto_articulo, re.IGNORECASE)
        if match_anexo:
            end = start + match_anexo.start()
        
        texto_completo = cuerpo_limpio[start:end].strip()
        texto_sin_label = patron_articulo.sub("", texto_completo, count=1).strip()
        texto_sin_label = re.sub(r"^[\.\-\s]+", "", texto_sin_label)
        
        articulos.append({
            "num": num,
            "label": label,
            "texto": texto_sin_label,
            "start": start,
            "end": end,
            "char_count": end - start,
        })
    
    return articulos


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Uso: python parser.py <ruta_al_archivo.txt>")
        sys.exit(1)
    
    archivo = sys.argv[1]
    with open(archivo, "r", encoding="utf-8") as f:
        contenido = f.read()
    
    print(f"Parseando: {archivo}\n")
    
    metadata = parse_header(contenido)
    print("=" * 60)
    print("METADATA EXTRAIDA")
    print("=" * 60)
    for clave, valor in metadata.items():
        print(f"  {clave:25} = {valor}")
    
    cuerpo = clean_body(contenido)
    print()
    print("=" * 60)
    print(f"CUERPO LIMPIO ({len(cuerpo)} chars, original: {len(contenido)} chars)")
    print("=" * 60)
    print(cuerpo[:500] + ("..." if len(cuerpo) > 500 else ""))
    
    articulos = extract_articles(cuerpo)
    print()
    print("=" * 60)
    print(f"ARTICULOS DETECTADOS: {len(articulos)}")
    print("=" * 60)
    for art in articulos:
        preview = art["texto"][:120].replace("\n", " ")
        if len(art["texto"]) > 120:
            preview += "..."
        print(f"\n  [Art. {art['num']}] {art['label']} ({art['char_count']} chars)")
        print(f"    {preview}")