

import os

import requests

API_KEY = os.getenv("OPENFOOT_API_KEY")

if not API_KEY:

    print("ERROR: Falta el secreto OPENFOOT_API_KEY")

    raise SystemExit(1)

url = "https://api.openfootapi.com/v1/fixtures"

try:

    response = requests.get(

        url,

        headers={"Authorization": f"Bearer {API_KEY}"},

        timeout=20,

    )

    print("Código HTTP:", response.status_code)

    if response.status_code == 200:

        data = response.json()

        print("Conexión correcta.")

        print("Tipo de respuesta:", type(data).__name__)

        if isinstance(data, dict):

            print("Campos recibidos:", list(data.keys()))

        elif isinstance(data, list):

            print("Partidos recibidos:", len(data))

        print("Muestra de respuesta:", str(data)[:1500])

    else:

        print("Respuesta:", response.text[:1000])

except requests.RequestException as error:

    print("Error de conexión:", error)
