

import os

import math

import requests

from datetime import datetime, timezone, timedelta

from zoneinfo import ZoneInfo

# ==========================================

# BOT FUTBOL - OPENFOOT STARTER + TELEGRAM

# Mercado: OVER 2.5 GOLES

# ==========================================

OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://openfootapi.com/v1"

LOCAL_TZ = ZoneInfo("America/Tegucigalpa")

MIN_PROBABILITY = 0.60

MAX_ALERTS = 5

HISTORY_SIZE = 10

# Limite de consultas para proteger el plan gratuito.

MAX_FIXTURES_TO_ANALYZE = 16

session = requests.Session()

session.headers.update({

    "Accept": "application/json",

    "Authorization": f"Bearer {OPENFOOT_API_KEY}",

})

# Competiciones que queremos analizar.

LEAGUES = {

    "england": {

        "keywords": ["premier league"],

        "name": "PREMIER LEAGUE",

        "flag": "🇬🇧",

    },

    "spain": {

        "keywords": ["la liga", "laliga", "primera division"],

        "name": "LA LIGA",

        "flag": "🇪🇸",

    },

    "germany": {

        "keywords": ["bundesliga"],

        "name": "BUNDESLIGA",

        "flag": "🇩🇪",

    },

    "italy": {

        "keywords": ["serie a"],

        "name": "SERIE A",

        "flag": "🇮🇹",

    },

    "france": {

        "keywords": ["ligue 1"],

        "name": "LIGUE 1",

        "flag": "🇫🇷",

    },

    "netherlands": {

        "keywords": ["eredivisie"],

        "name": "EREDIVISIE",

        "flag": "🇳🇱",

    },

    "portugal": {

        "keywords": ["primeira liga", "liga portugal"],

        "name": "PRIMEIRA LIGA",

        "flag": "🇵🇹",

    },

    "brazil": {

        "keywords": ["brasileirao serie a", "serie a brazil",

                     "campeonato brasileiro serie a"],

        "name": "BRASIL SERIE A",

        "flag": "🇧🇷",

    },

    "usa": {

        "keywords": ["major league soccer", "mls"],

        "name": "MLS",

        "flag": "🇺🇸",

    },

    "mexico": {

        "keywords": ["liga mx"],

        "name": "LIGA MX",

        "flag": "🇲🇽",

    },

    "argentina": {

        "keywords": ["liga profesional", "primera division argentina"],

        "name": "PRIMERA ARGENTINA",

        "flag": "🇦🇷",

    },

}

def api_get(endpoint, params=None):

    url = f"{BASE_URL}{endpoint}"

    try:

        response = session.get(

            url,

            params=params,

            timeout=30,

        )

        if response.status_code == 401:

            raise RuntimeError(

                "OPENFOOT_API_KEY no válida. Revisa el secreto de GitHub."

            )

        if response.status_code == 403:

            raise RuntimeError(

                "OpenFoot rechazó el acceso. Comprueba el plan y el endpoint."

            )

        if response.status_code == 429:

            raise RuntimeError(

                "Se agotó el límite de consultas de OpenFoot."

            )

        response.raise_for_status()

        payload = response.json()

        if isinstance(payload, dict) and payload.get("error"):

            raise RuntimeError(str(payload["error"]))

        if isinstance(payload, dict):

            return payload.get("data", [])

        return payload

    except requests.RequestException as error:

        raise RuntimeError(f"Error consultando OpenFoot: {error}")

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

            timeout=25,

        )

        response.raise_for_status()

        if not response.json().get("ok"):

            print("Telegram rechazó el mensaje.")

            return False

        return True

    except requests.RequestException as error:

        print(f"Error enviando Telegram: {error}")

        return False

def parse_date(value):

    if not value:

        return None

    try:

        result = datetime.fromisoformat(

            value.replace("Z", "+00:00")

        )

        if result.tzinfo is None:

            result = result.replace(tzinfo=timezone.utc)

        return result

    except (ValueError, TypeError, AttributeError):

        return None

def get_competition_text(competition):

    if isinstance(competition, dict):

        return " ".join([

            str(competition.get("name", "")),

            str(competition.get("country", "")),

            str(competition.get("slug", "")),

        ]).lower()

    return str(competition or "").lower()

def identify_league(competition):

    text = get_competition_text(competition)

    for league in LEAGUES.values():

        for keyword in league["keywords"]:

            if keyword in text:

                return league

    return None

def get_competitions():

    data = api_get("/competitions")

    if not isinstance(data, list):

        raise RuntimeError(

            "OpenFoot no devolvió una lista válida de competiciones."

        )

    result = {}

    for competition in data:

        if not isinstance(competition, dict):

            continue

        league = identify_league(competition)

        if not league:

            continue

        competition_id = (

            competition.get("id")

            or competition.get("competitionId")

        )

        if competition_id:

            result[str(competition_id)] = league

    print(f"Competiciones reconocidas: {len(result)}")

    return result

def get_today_fixtures():

    now = datetime.now(timezone.utc)

    local_today = datetime.now(LOCAL_TZ).date()

    competitions = get_competitions()

    fixtures = []

    seen = set()

    # Consultamos ayer, hoy y mañana UTC para cubrir

    # correctamente el día local de Honduras.

    for offset in (-1, 0, 1):

        utc_date = (now.date() + timedelta(days=offset)).isoformat()

        matches = api_get(

            "/matches",

            params={"date": utc_date},

        )

        if not isinstance(matches, list):

            continue

        for match in matches:

            if not isinstance(match, dict):

                continue

            match_id = match.get("id")

            if not match_id or match_id in seen:

                continue

            seen.add(match_id)

            kickoff = parse_date(match.get("kickoffAt"))

            if not kickoff:

                continue

            if kickoff <= now:

                continue

            if kickoff.astimezone(LOCAL_TZ).date() != local_today:

                continue

            competition_id = str(match.get("competitionId", ""))

            league = competitions.get(competition_id)

            if not league:

                league = identify_league(

                    match.get("competition")

                )

            if not league:

                continue

            home = match.get("homeTeam") or {}

            away = match.get("awayTeam") or {}

            if not home.get("id") or not away.get("id"):

                continue

            if not home.get("name") or not away.get("name"):

                continue

            fixtures.append({

                "id": match_id,

                "home_id": str(home["id"]),

                "away_id": str(away["id"]),

                "home": home["name"],

                "away": away["name"],

                "kickoff": kickoff,

                "league": league,

            })

    fixtures.sort(key=lambda item: item["kickoff"])

    print(f"Partidos encontrados para hoy: {len(fixtures)}")

    if len(fixtures) > MAX_FIXTURES_TO_ANALYZE:

        print(

            f"Se analizarán los primeros "

            f"{MAX_FIXTURES_TO_ANALYZE} partidos "

            "para proteger la cuota gratuita."

        )

    return fixtures[:MAX_FIXTURES_TO_ANALYZE]

def get_score(match):

    possible_scores = [

        match.get("score"),

        match.get("scores"),

        match.get("fullTime"),

    ]

    for score in possible_scores:

        if not isinstance(score, dict):

            continue

        home = score.get("home")

        away = score.get("away")

        if home is None or away is None:

            home = score.get("homeScore", home)

            away = score.get("awayScore", away)

        if isinstance(home, dict):

            home = home.get("goals")

        if isinstance(away, dict):

            away = away.get("goals")

        if (

            isinstance(home, (int, float))

            and isinstance(away, (int, float))

        ):

            return int(home), int(away)

    home = match.get("homeScore")

    away = match.get("awayScore")

    if (

        isinstance(home, (int, float))

        and isinstance(away, (int, float))

    ):

        return int(home), int(away)

    return None

history_cache = {}

def get_team_history(team_id):

    if team_id in history_cache:

        return history_cache[team_id]

    matches = api_get(

        "/matches",

        params={

            "team": team_id,

            "status": "finished",

        },

    )

    if not isinstance(matches, list):

        matches = []

    valid = []

    for match in matches:

        if not isinstance(match, dict):

            continue

        score = get_score(match)

        kickoff = parse_date(match.get("kickoffAt"))

        if score is None or kickoff is None:

            continue

        valid.append({

            "kickoff": kickoff,

            "home_id": str(

                (match.get("homeTeam") or {}).get("id", "")

            ),

            "score": score,

        })

    valid.sort(

        key=lambda item: item["kickoff"],

        reverse=True,

    )

    history = valid[:HISTORY_SIZE]

    history_cache[team_id] = history

    return history

def team_goals(history, team_id):

    scored = []

    conceded = []

    for match in history:

        home_goals, away_goals = match["score"]

        if match["home_id"] == team_id:

            scored.append(home_goals)

            conceded.append(away_goals)

        else:

            scored.append(away_goals)

            conceded.append(home_goals)

    return scored, conceded

def poisson_over_25(expected_goals):

    if expected_goals <= 0:

        return 0.0

    probability = 1 - math.exp(-expected_goals) * (

        1 + expected_goals + (expected_goals ** 2 / 2)

    )

    return max(0.0, min(1.0, probability))

def analyze_match(fixture):

    home_history = get_team_history(fixture["home_id"])

    away_history = get_team_history(fixture["away_id"])

    if (

        len(home_history) < HISTORY_SIZE

        or len(away_history) < HISTORY_SIZE

    ):

        print(

            f"Historial insuficiente: "

            f"{fixture['home']} - {fixture['away']}"

        )

        return None

    home_scored, home_conceded = team_goals(

        home_history,

        fixture["home_id"],

    )

    away_scored, away_conceded = team_goals(

        away_history,

        fixture["away_id"],

    )

    # Promedios generales de los últimos 10 partidos.

    home_attack = sum(home_scored) / HISTORY_SIZE

    home_defense = sum(home_conceded) / HISTORY_SIZE

    away_attack = sum(away_scored) / HISTORY_SIZE

    away_defense = sum(away_conceded) / HISTORY_SIZE

    # Estimación de goles esperados para ambos equipos.

    expected_home = (home_attack + away_defense) / 2

    expected_away = (away_attack + home_defense) / 2

    expected_total = expected_home + expected_away

    probability = poisson_over_25(expected_total)

    print(

        f"{fixture['home']} - {fixture['away']}: "

        f"{probability:.1%}"

    )

    if probability < MIN_PROBABILITY:

        return None

    return {

        **fixture,

        "probability": probability,

        "expected_goals": expected_total,

    }

def main():

    if not OPENFOOT_API_KEY:

        raise SystemExit("Falta OPENFOOT_API_KEY.")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        raise SystemExit(

            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."

        )

    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    print(f"ESCÁNER OVER 2.5 — HONDURAS: {today}")

    fixtures = get_today_fixtures()

    picks = []

    for fixture in fixtures:

        pick = analyze_match(fixture)

        if pick:

            picks.append(pick)

    picks.sort(

        key=lambda item: item["probability"],

        reverse=True,

    )

    picks = picks[:MAX_ALERTS]

    if not picks:

        message = (

            f"🔎 ESCÁNER OVER 2.5 — {today}\n\n"

            "No se encontraron selecciones que cumplan "

            "los filtros estadísticos.\n\n"

            f"Probabilidad mínima: {MIN_PROBABILITY:.0%}\n"

            f"Historial requerido: últimos {HISTORY_SIZE} partidos.\n\n"

            "No se fuerza ninguna apuesta."

        )

        sent = send_telegram(message)

        print(message)

        print("Telegram:", "enviado" if sent else "falló")

        return

    lines = [

        f"⚽ ESCÁNER OVER 2.5 — {today}",

        f"Selecciones encontradas: {len(picks)}",

        "",

    ]

    for number, pick in enumerate(picks, start=1):

        local_time = pick["kickoff"].astimezone(LOCAL_TZ)

        league = pick["league"]

        lines.extend([

            f"{league['flag']} {league['name']}",

            f"#{number} {pick['home']} vs {pick['away']}",

            "Pick: Más de 2.5 goles",

            f"Probabilidad estimada: {pick['probability']:.1%}",

            f"Hora Honduras: {local_time:%H:%M}",

            "",

        ])

    lines.append(

        "Estimación estadística basada en los últimos "

        "10 partidos de cada equipo. No garantiza resultados."

    )

    message = "\n".join(lines)

    sent = send_telegram(message)

    print(message)

    print(

        "Telegram:",

        "mensaje enviado" if sent else "falló el envío"

    )

if __name__ == "__main__":

    main()
