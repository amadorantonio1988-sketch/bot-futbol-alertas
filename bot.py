

import os

import requests

from datetime import datetime, timezone

from zoneinfo import ZoneInfo

# ==========================================

# BOT FUTBOL: OVER 2.5 + CUOTAS + TELEGRAM

# Fuente de cuotas: The Odds API

# ==========================================

ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://api.the-odds-api.com/v4"

LOCAL_TZ = ZoneInfo("America/Tegucigalpa")

MIN_ODD = 1.50

MIN_PROBABILITY = 0.60

MAX_ALERTS = 5

# Competiciones disponibles en The Odds API.

SPORTS = [

    "soccer_epl",

    "soccer_spain_la_liga",

    "soccer_germany_bundesliga",

    "soccer_italy_serie_a",

    "soccer_france_ligue_one",

    "soccer_netherlands_eredivisie",

    "soccer_portugal_primeira_liga",

    "soccer_brazil_campeonato",

    "soccer_usa_mls",

    "soccer_mexico_ligamx",

    "soccer_argentina_primera_division",

]

session = requests.Session()

def send_telegram(message):

    url = (

        f"https://api.telegram.org/bot"

        f"{TELEGRAM_BOT_TOKEN}/sendMessage"

    )

    try:

        response = session.post(

            url,

            json={

                "chat_id": TELEGRAM_CHAT_ID,

                "text": message,

                "disable_web_page_preview": True,

            },

            timeout=20,

        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):

            print("Telegram rechazó el mensaje:", data)

            return False

        return True

    except requests.RequestException as error:

        print("Error enviando Telegram:", error)

        return False

def get_odds(sport):

    url = f"{BASE_URL}/sports/{sport}/odds/"

    params = {

        "apiKey": ODDS_API_KEY,

        "regions": "uk,eu",

        "markets": "totals",

        "oddsFormat": "decimal",

        "dateFormat": "iso",

    }

    try:

        response = session.get(url, params=params, timeout=25)

        if response.status_code == 401:

            raise SystemExit(

                "THE_ODDS_API_KEY no es válida o no está autorizada."

            )

        if response.status_code == 429:

            raise SystemExit(

                "The Odds API rechazó la petición por límite de uso."

            )

        if response.status_code in (404, 422):

            print(f"Competición no disponible: {sport}")

            return []

        if response.status_code != 200:

            print(

                f"Error HTTP {response.status_code} en {sport}: "

                f"{response.text[:200]}"

            )

            return []

        remaining = response.headers.get(

            "x-requests-remaining", "desconocido"

        )

        print(f"{sport}: consultas restantes según API: {remaining}")

        return response.json()

    except requests.RequestException as error:

        print(f"Error consultando {sport}: {error}")

        return []

def parse_date(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    except (TypeError, ValueError):

        return None

def analyze_event(event):

    kickoff = parse_date(event.get("commence_time"))

    now = datetime.now(timezone.utc)

    today = datetime.now(LOCAL_TZ).date()

    if not kickoff or kickoff <= now:

        return None

    if kickoff.astimezone(LOCAL_TZ).date() != today:

        return None

    best_pick = None

    for bookmaker in event.get("bookmakers", []):

        for market in bookmaker.get("markets", []):

            if market.get("key") != "totals":

                continue

            outcomes = market.get("outcomes", [])

            over_prices = [

                item for item in outcomes

                if item.get("name", "").lower() == "over"

                and item.get("point") == 2.5

            ]

            under_prices = [

                item for item in outcomes

                if item.get("name", "").lower() == "under"

                and item.get("point") == 2.5

            ]

            if not over_prices or not under_prices:

                continue

            over_odd = over_prices[0].get("price")

            under_odd = under_prices[0].get("price")

            if not isinstance(over_odd, (int, float)):

                continue

            if not isinstance(under_odd, (int, float)):

                continue

            if over_odd < MIN_ODD or under_odd <= 1:

                continue

            # Probabilidad implícita del mercado, ajustada

            # aproximadamente para eliminar el margen de la casa.

            over_raw = 1 / over_odd

            under_raw = 1 / under_odd

            probability = over_raw / (over_raw + under_raw)

            if probability < MIN_PROBABILITY:

                continue

            candidate = {

                "home": event.get("home_team", "Local"),

                "away": event.get("away_team", "Visitante"),

                "kickoff": kickoff,

                "odd": over_odd,

                "probability": probability,

                "bookmaker": bookmaker.get("title", "Casa no identificada"),

            }

            if (

                best_pick is None

                or candidate["odd"] > best_pick["odd"]

            ):

                best_pick = candidate

    return best_pick

def main():

    if not ODDS_API_KEY:

        raise SystemExit("Falta el secreto THE_ODDS_API_KEY.")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        raise SystemExit(

            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."

        )

    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    print(f"ESCÁNER OVER 2.5 — Honduras: {today}")

    events_seen = set()

    picks = []

    for sport in SPORTS:

        events = get_odds(sport)

        for event in events:

            event_id = event.get("id")

            if not event_id or event_id in events_seen:

                continue

            events_seen.add(event_id)

            pick = analyze_event(event)

            if pick:

                picks.append(pick)

    # Evita repetir un partido y conserva el mejor precio encontrado.

    unique = {}

    for pick in picks:

        key = (pick["home"], pick["away"])

        if (

            key not in unique

            or pick["odd"] > unique[key]["odd"]

        ):

            unique[key] = pick

    picks = list(unique.values())

    picks.sort(

        key=lambda item: (

            item["probability"],

            item["odd"],

        ),

        reverse=True,

    )

    picks = picks[:MAX_ALERTS]

    if not picks:

        message = (

            f"🔎 ESCÁNER OVER 2.5 — {today}\n\n"

            "No se encontraron picks que cumplan los filtros "

            "con cuotas disponibles.\n"

            f"Cuota mínima: {MIN_ODD:.2f}\n"

            "Probabilidad implícita ajustada mínima: 60%.\n\n"

            "No se fuerza ninguna apuesta."

        )

        send_telegram(message)

        print("Sin picks válidos.")

        return

    lines = [

        f"⚽ ESCÁNER OVER 2.5 — {today}",

        f"Selecciones encontradas: {len(picks)}",

        "",

    ]

    for number, pick in enumerate(picks, start=1):

        local_time = pick["kickoff"].astimezone(LOCAL_TZ)

        lines.extend([

            f"#{number} {pick['home']} vs {pick['away']}",

            "Pick: Más de 2.5 goles",

            f"Cuota: {pick['odd']:.2f}",

            (

                "Probabilidad implícita ajustada: "

                f"{pick['probability']:.1%}"

            ),

            f"Casa: {pick['bookmaker']}",

            f"Hora Honduras: {local_time:%H:%M}",

            "",

        ])

    lines.append(

        "Nota: la probabilidad procede de las cuotas del mercado; "

        "no es una predicción estadística independiente."

    )

    message = "\n".join(lines)

    sent = send_telegram(message)

    print(message)

    print("Telegram:", "mensaje enviado" if sent else "falló el envío")

if __name__ == "__main__":

    main()
