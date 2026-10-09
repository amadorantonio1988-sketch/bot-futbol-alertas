

import os

import csv

import time

import requests

from datetime import datetime

from pathlib import Path

API_URL = "https://v3.football.api-sports.io"

API_KEY = os.getenv("API_FOOTBALL_KEY", "")

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MIN_HIT_RATE = 0.75

MIN_SAMPLE = 6

MAX_LINES = 4

DELAY = 0.5

session = requests.Session()

session.headers.update({"x-apisports-key": API_KEY})

cache = {}

requests_used = 0

def api_get(endpoint, params):

    global requests_used

    key = (endpoint, tuple(sorted(params.items())))

    if key in cache:

        return cache[key]

    response = session.get(

        f"{API_URL}/{endpoint}",

        params=params,

        timeout=25

    )

    requests_used += 1

    time.sleep(DELAY)

    if response.status_code == 429:

        raise RuntimeError("Limite diario de API-Football alcanzado.")

    response.raise_for_status()

    data = response.json()

    if data.get("errors"):

        raise RuntimeError(str(data["errors"]))

    cache[key] = data.get("response", [])

    return cache[key]

def send_telegram(message):

    if not TG_TOKEN or not TG_CHAT_ID:

        print("Telegram no configurado.")

        print(message)

        return

    response = requests.post(

        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",

        json={

            "chat_id": TG_CHAT_ID,

            "text": message

        },

        timeout=20

    )

    response.raise_for_status()

def find_team(name, season):

    results = api_get(

        "teams",

        {"search": name, "season": season}

    )

    exact = [

        item for item in results

        if item["team"]["name"].lower() == name.lower()

    ]

    if exact:

        return exact[0]["team"]["id"]

    if results:

        return results[0]["team"]["id"]

    raise RuntimeError(f"Equipo no encontrado: {name}")

def recent_fixtures(team_id, season, venue):

    fixtures = api_get(

        "fixtures",

        {

            "team": team_id,

            "season": season,

            "last": 20

        }

    )

    selected = []

    for item in fixtures:

        status = item["fixture"]["status"]["short"]

        if status not in ("FT", "AET", "PEN"):

            continue

        is_home = (

            item["teams"]["home"]["id"] == team_id

        )

        if venue == "home" and not is_home:

            continue

        if venue == "away" and is_home:

            continue

        selected.append(item)

    selected.sort(

        key=lambda item: item["fixture"]["date"],

        reverse=True

    )

    return selected[:10]

def get_shots(fixture_id, team_id, market):

    data = api_get(

        "fixtures/statistics",

        {"fixture": fixture_id}

    )

    wanted = (

        "Total Shots"

        if market == "shots"

        else "Shots on Goal"

    )

    for team_data in data:

        if team_data["team"]["id"] != team_id:

            continue

        for stat in team_data.get("statistics", []):

            if stat["type"].lower() == wanted.lower():

                value = stat["value"]

                if value is None:

                    return None

                try:

                    return float(

                        str(value).replace("%", "")

                    )

                except ValueError:

                    return None

    return None

def collect_values(fixtures, team_id, market):

    values = []

    for fixture in fixtures:

        fixture_id = fixture["fixture"]["id"]

        value = get_shots(

            fixture_id,

            team_id,

            market

        )

        if value is not None:

            values.append(value)

    return values

def evaluate(values, line, direction):

    if not values:

        return 0, 0, 0

    if direction == "over":

        hits = sum(value > line for value in values)

    else:

        hits = sum(value < line for value in values)

    return hits / len(values), sum(values) / len(values), len(values)

def analyze(row):

    date = datetime.strptime(

        row["date"], "%Y-%m-%d"

    )

    season = int(row.get("season") or date.year)

    home = row["home_team"].strip()

    away = row["away_team"].strip()

    team = row["team"].strip()

    if team.lower() == home.lower():

        venue = "home"

        opponent = away

    elif team.lower() == away.lower():

        venue = "away"

        opponent = home

    else:

        raise RuntimeError(

            "El equipo analizado no coincide con el local o visitante."

        )

    market = row["market"].strip().lower()

    if market in ("shots", "remates", "total shots"):

        market = "shots"

        label = "remates totales"

    elif market in ("sot", "remates a puerta", "shots on target"):

        market = "sot"

        label = "remates a puerta"

    else:

        raise RuntimeError("Mercado debe ser shots o sot.")

    direction = row["direction"].strip().lower()

    if direction not in ("over", "under"):

        raise RuntimeError("direction debe ser over o under.")

    line = float(row["line"])

    odds = float(row["odds"])

    team_id = find_team(team, season)

    opponent_id = find_team(opponent, season)

    fixtures = recent_fixtures(

        team_id, season, venue

    )

    own_values = collect_values(

        fixtures, team_id, market

    )

    opponent_venue = (

        "away" if venue == "home" else "home"

    )

    opponent_fixtures = recent_fixtures(

        opponent_id, season, opponent_venue

    )

    allowed_values = []

    for fixture in opponent_fixtures:

        fixture_id = fixture["fixture"]["id"]

        if opponent_venue == "away":

            rival_id = fixture["teams"]["home"]["id"]

        else:

            rival_id = fixture["teams"]["away"]["id"]

        value = get_shots(

            fixture_id, rival_id, market

        )

        if value is not None:

            allowed_values.append(value)

    own_rate, average, sample = evaluate(

        own_values, line, direction

    )

    rival_rate, _, rival_sample = evaluate(

        allowed_values, line, direction

    )

    if sample == 0:

        estimate = 0

    elif rival_sample:

        estimate = (

            0.70 * own_rate +

            0.30 * rival_rate

        )

    else:

        estimate = own_rate

    qualified = (

        sample >= MIN_SAMPLE and

        estimate >= MIN_HIT_RATE

    )

    return {

        "home": home,

        "away": away,

        "team": team,

        "market": label,

        "direction": direction,

        "line": line,

        "odds": odds,

        "sample": sample,

        "average": average,

        "own_rate": own_rate,

        "rival_rate": rival_rate,

        "rival_sample": rival_sample,

        "estimate": estimate,

        "qualified": qualified

    }

def main():

    if not API_KEY:

        raise RuntimeError("Falta API_FOOTBALL_KEY.")

    file = Path("lines.csv")

    if not file.exists():

        raise RuntimeError("No existe lines.csv.")

    with file.open(

        "r", encoding="utf-8-sig", newline=""

    ) as source:

        rows = list(csv.DictReader(source))

    rows = rows[:MAX_LINES]

    alerts = []

    for row in rows:

        try:

            result = analyze(row)

            print(

                result["team"],

                result["market"],

                result["estimate"]

            )

            if not result["qualified"]:

                continue

            direction = (

                "Más de"

                if result["direction"] == "over"

                else "Menos de"

            )

            message = (

                "🎯 TOMM REMATES SCANNER\n\n"

                f"⚽ {result['home']} vs {result['away']}\n"

                f"📌 {result['team']} {direction} "

                f"{result['line']} {result['market']}\n"

                f"💰 Cuota: {result['odds']:.2f}\n\n"

                f"📊 Muestra válida: {result['sample']} partidos\n"

                f"✅ Acierto histórico: "

                f"{result['own_rate']:.1%}\n"

                f"⚽ Promedio: {result['average']:.2f}\n"

                f"🛡️ Frecuencia rival: "

                f"{result['rival_rate']:.1%} "

                f"({result['rival_sample']} partidos)\n"

                f"📈 Estimación inicial: "

                f"{result['estimate']:.1%}\n\n"

                "Estimación experimental, no garantía. "

                "Verifica la línea y cuota antes de apostar."

            )

            alerts.append(message)

        except Exception as error:

            print(

                "Error analizando",

                row.get("team", "?"),

                str(error)

            )

    if alerts:

        for message in alerts:

            send_telegram(message)

    else:

        send_telegram(

            "📊 TOMM REMATES SCANNER\n\n"

            "No se encontraron líneas que superen "

            "los filtros estadísticos."

        )

    print("Solicitudes API:", requests_used)

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print("BOT DETENIDO:", error)

        try:

            send_telegram(

                "🛑 TOMM REMATES SCANNER\n" + str(error)

            )

        except Exception:

            pass

        raise
