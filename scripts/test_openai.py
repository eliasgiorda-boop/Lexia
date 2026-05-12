"""
Test de conexion a OpenAI.
Genera un embedding de prueba con la palabra "ordenanza municipal"
y muestra el resultado para confirmar que todo funciona.
"""
import os
from dotenv import load_dotenv
from openai import OpenAI

# Cargar variables del .env
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# Validaciones basicas
if not api_key:
    print("ERROR: no se encontro OPENAI_API_KEY en .env")
    exit(1)

if not api_key.startswith("sk-"):
    print("ERROR: la key no arranca con sk-")
    exit(1)

print(f"Key cargada: {api_key[:12]}... ({len(api_key)} chars)")
print(f"Modelo a usar: {model}")
print()

# Crear cliente OpenAI
client = OpenAI(api_key=api_key)

# Texto de prueba
texto = "ordenanza municipal"
print(f"Generando embedding para: '{texto}'")
print()

# Llamar a la API
try:
    response = client.embeddings.create(
        model=model,
        input=texto
    )
    
    embedding = response.data[0].embedding
    tokens_usados = response.usage.total_tokens
    
    print("=" * 50)
    print("EXITO - conexion a OpenAI funcionando")
    print("=" * 50)
    print(f"Dimensiones del vector: {len(embedding)}")
    print(f"Tokens consumidos: {tokens_usados}")
    print(f"Primeros 5 valores del vector:")
    for i, val in enumerate(embedding[:5]):
        print(f"  [{i}] {val:.6f}")
    print(f"Modelo usado: {response.model}")
    print()
    print("Todo OK - la infraestructura esta lista")
    
except Exception as e:
    print(f"ERROR al llamar a OpenAI: {e}")
    exit(1)
