import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import requests
import yaml
import streamlit as st
import streamlit_authenticator as stauth
from yaml.loader import SafeLoader
from history import init_db, guardar_consulta, obtener_historial

API_URL = "http://localhost:8000"

st.set_page_config(page_title="Lexia", page_icon="L", layout="centered")

init_db()

with open("config.yaml", encoding="utf-8") as f:
    config = yaml.load(f, Loader=SafeLoader)

authenticator = stauth.Authenticate(
    config["credentials"],
    config["cookie"]["name"],
    config["cookie"]["key"],
    config["cookie"]["expiry_days"],
)

authenticator.login(location="main")
auth_status = st.session_state.get("authentication_status")

if auth_status is False:
    st.error("Usuario o contrasena incorrectos.")
    st.stop()
elif auth_status is None:
    st.warning("Por favor ingresa tus credenciales.")
    st.stop()

nombre = st.session_state.get("name", "")
username = st.session_state.get("username", "")

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 2rem;}
    h1 {color: #4ade80; font-weight: 700; letter-spacing: -1px;}
    .fuente-card {
        background: #16241d;
        border-left: 3px solid #4ade80;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
    }
    .fuente-card a {color: #4ade80; text-decoration: none;}
    .fuente-card a:hover {text-decoration: underline;}
    .derogada {color: #f87171; font-weight: 600; font-size: 0.85em;}
    .stButton button {border-radius: 8px; font-weight: 600;}
    .block-container {padding-bottom: 4rem;}
    .pie-fijo {
        position: fixed; left: 0; bottom: 0; width: 100%;
        background: #0b1612; border-top: 1px solid #1f3329;
        color: #7fa890; font-size: 0.72rem; text-align: center;
        padding: 8px 12px; z-index: 999;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Sidebar: sesion, logout, historial ---
with st.sidebar:
    st.markdown("Sesion: **" + nombre + "**")
    authenticator.logout("Cerrar sesion", location="sidebar")
    st.divider()
    st.markdown("**Historial**")
    historial = obtener_historial(username)
    if not historial:
        st.caption("Sin consultas aun.")
    else:
        for i, h in enumerate(historial):
            if st.button(h["query"], key="hist_" + str(i), use_container_width=True):
                st.session_state["query_pendiente"] = h["query"]
                st.rerun()

st.title("Lexia")
st.caption("Asistente normativo - San Martin de los Andes")

# Si se clickeo una consulta del historial, precargarla
valor_inicial = st.session_state.pop("query_pendiente", "")
query = st.text_input("Consulta", value=valor_inicial, placeholder="Ej: como se regula la Banca del Vecino", label_visibility="collapsed")
buscar = st.button("Buscar", type="primary")

# Disparar busqueda si: se apreto Buscar, o vino una consulta precargada del historial
ejecutar = (buscar and query.strip()) or (valor_inicial and query.strip())

if ejecutar:
    with st.spinner("Buscando en la normativa..."):
        try:
            resp = requests.post(
                API_URL + "/responder",
                json={"query": query, "n_chunks": 12, "dos_fases": False},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError:
            st.error("No me puedo conectar a la API. Esta corriendo uvicorn en el puerto 8000?")
            st.stop()
        except Exception as e:
            st.error("Error al consultar: " + str(e))
            st.stop()

    guardar_consulta(username, query)

    st.markdown(data["respuesta"])

    st.divider()
    st.subheader("Fuentes")
    for f in data["fuentes"]:
        cita = f.get("cita", "Sin cita")
        link = f.get("link", "")
        derog = '<span class=\"derogada\"> DEROGADA</span>' if f.get("es_derogada") else ""
        num = f.get("numero", "")
        if link:
            cuerpo = '<a href=\"' + link + '\" target=\"_blank\">' + cita + '</a>'
        else:
            cuerpo = cita
        st.markdown('<div class=\"fuente-card\"><b>[' + str(num) + ']</b> ' + cuerpo + derog + '</div>', unsafe_allow_html=True)

    uso = data.get("uso", {})
    st.caption("Modelo: " + str(data.get("modelo","")) + " | chunks: " + str(data.get("chunks_usados",0)) + " | costo: U " + format(uso.get("costo_usd",0), ".4f"))

elif buscar:
    st.warning("Escribi una consulta primero.")

st.markdown(
    '<div class=\"pie-fijo\">Lexia (beta) &middot; Powered by GPT-4.1 &middot; '
    'Herramienta orientativa, no constituye asesoramiento legal &middot; '
    'Verifica contra el texto oficial vigente</div>',
    unsafe_allow_html=True,
)