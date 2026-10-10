import os

from datetime import datetime, timezone

import requests

API_KEY = os.getenv("OPENFOOT_API_KEY")

if not API_KEY:

    raise SystemExit(

        "ERROR: Falta OPENFOOT_API_KEY en el entorno."

    )

url = "https://openfootapi.com/v1/matches"

params = {

    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")

}

headers = {

    "Accept": "application/json",

    "Authorization": f"Bearer {API_KEY}",

}

try:

    response = requests.get(

        url,

        headers=headers,

        params=params,

        timeout=20,

    )

    print("Código HTTP:", response.status_code)

    if response.status_code != 200:

        print("La API rechazó la petición.")

        print("Respuesta:", response.text[:500])

        raise SystemExit(1)

    result = response.json()

    matches = result.get("data", [])

    print("Conexión correcta.")

    print("Partidos devueltos:", len(matches))

    print(

        "Acceso:",

        result.get("meta", {}).get("access", {})

    )

    if matches:

        print(

            "Campos del partido:",

            list(matches[0].keys())

        )

        print("Datos del primer partido:", matches[0])

    print("\nPrimeros cinco partidos:")

    for match in matches[:5]:

        home_data = match.get("homeTeam") or {}

        away_data = match.get("awayTeam") or {}

        home = home_data.get("name", "Local desconocido")

        away = away_data.get("name", "Visitante desconocido")

        print(f"- {home} vs {away}")

except requests.RequestException as error:

    raise SystemExit(f"Error de conexión: {error}")

except (ValueError, AttributeError) as error:

    raise SystemExit(f"Error al interpretar la respuesta: {error}")
