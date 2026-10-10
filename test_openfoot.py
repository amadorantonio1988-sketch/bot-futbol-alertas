import os

import requests

from datetime import datetime, timezone

API_KEY = os.getenv("OPENFOOT_API_KEY")

if not API_KEY:

    raise SystemExit("ERROR: Falta OPENFOOT_API_KEY.")

BASE_URL = "https://openfootapi.com/v1"

HEADERS = {

    "Accept": "application/json",

    "Authorization": f"Bearer {API_KEY}",

}

def consultar(endpoint, params=None):

    response = requests.get(

        f"{BASE_URL}/{endpoint}",

        headers=HEADERS,

        params=params,

        timeout=20,

    )

    print(f"\nEndpoint: {endpoint}")

    print("Código HTTP:", response.status_code)

    if response.status_code != 200:

        print("Respuesta:", response.text[:400])

        return None

    return response.json()

try:

    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    resultado = consultar(

        "matches",

        {"date": fecha},

    )

    if resultado is None:

        raise SystemExit("No se pudieron obtener los partidos.")

    partidos = resultado.get("data", [])

    print("Partidos encontrados:", len(partidos))

    if not partidos:

        raise SystemExit("No hay partidos para esa fecha.")

    # Preferir un partido programado para analizar sus equipos.

    partido = next(

        (

            p for p in partidos

            if p.get("status") == "scheduled"

        ),

        partidos[0],

    )

    local = partido.get("homeTeam") or {}

    visitante = partido.get("awayTeam") or {}

    print("\nPartido de referencia:")

    print(local.get("name"), "vs", visitante.get("name"))

    for equipo in (local, visitante):

        equipo_id = equipo.get("id")

        if not equipo_id:

            print("No se encontró el ID de un equipo.")

            continue

        print("\nHistorial de:", equipo.get("name"))

        historial = consultar(

            "matches",

            {

                "team": equipo_id,

                "status": "finished",

                "season": partido.get("season"),

            },

        )

        if historial is None:

            continue

        encuentros = historial.get("data", [])

        print("Partidos históricos devueltos:", len(encuentros))

        encuentros.sort(

            key=lambda p: p.get("kickoffAt", ""),

            reverse=True,

        )

        for juego in encuentros[:10]:

            casa = juego.get("homeTeam") or {}

            fuera = juego.get("awayTeam") or {}

            marcador = juego.get("score") or {}

            print(

                f"- {juego.get('kickoffAt', 'Fecha desconocida')}: "

                f"{casa.get('name', '?')} "

                f"{marcador.get('home', '?')}-"

                f"{marcador.get('away', '?')} "

                f"{fuera.get('name', '?')}"

            )

except requests.RequestException as error:

    raise SystemExit(f"Error de conexión: {error}")
