import os

import math

import time

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import requests

# ==================================================

# BOT DE ALERTAS DE FUTBOL - SOLO OVER 2.5 GOLES

# ==================================================

OPENFOOT_API_KEY = os.getenv("OPENFOOT_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://openfootapi.com/v1"

LOCAL_TZ = ZoneInfo("America/Tegucigalpa")

MIN_PROBABILITY = 0.60

MIN_ODD = 1.50

MAX_ALERTS = 5

HISTORY_SIZE = 10

session = requests.Session()

history_cache = {}

# Banderas por país o competición.

COUNTRY_FLAGS = {

    "spain": "🇪🇸", "españa": "🇪🇸", "espana": "🇪🇸",

    "brazil": "🇧🇷", "brasil": "🇧🇷",

    "honduras": "🇭🇳",

    "england": "🏴", "inglaterra": "🏴",

    "italy": "🇮🇹", "italia": "🇮🇹",

    "germany": "🇩🇪", "alemania": "🇩🇪",

    "france": "🇫🇷", "francia": "🇫🇷",

    "portugal": "🇵🇹",

    "netherlands": "🇳🇱", "holanda": "🇳🇱",

    "argentina": "🇦🇷",

    "mexico": "🇲🇽", "méxico": "🇲🇽", "mexico": "🇲🇽",

    "peru": "🇵🇪", "perú": "🇵🇪",

    "colombia": "🇨🇴",

    "chile": "🇨🇱",

    "uruguay": "🇺🇾",

    "united states": "🇺🇸", "usa": "🇺🇸",

    "south korea": "🇰🇷", "korea": "🇰🇷",

    "japan": "🇯🇵",

    "scotland": "🏴",

    "turkey": "🇹🇷", "türkiye": "🇹🇷",

    "saudi arabia": "🇸🇦",

    "switzerland": "🇨🇭",

    "belgium": "🇧🇪",

    "australia": "🇦🇺",

    "international": "🌍",

}

def api_get(endpoint, params=None):

    url = f"{BASE_URL}/{endpoint.lstrip('/')}"

    headers = {

        "Accept": "application/json",

        "Authorization": f"Bearer {OPENFOOT_API_KEY}",

    }

    for attempt in range(3):

        try:

            response = session.get(

                url, headers=headers, params=params or {}, timeout=20

            )

            if response.status_code == 429:

                time.sleep(2 ** attempt)

                continue

            if response.status_code != 200:

                print(

                    f"OpenFoot HTTP {response.status_code}: "

                    f"{response.text[:250]}"

                )

                return None

            time.sleep(0.15)

            return response.json()

        except (requests.RequestException, ValueError) as error:

            print("Error de OpenFoot:", error)

            time.sleep(2 ** attempt)

    return None

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("Faltan los secretos de Telegram.")

        return False

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

                "parse_mode": "HTML",

                "disable_web_page_preview": True,

            },

            timeout=20,

        )

        if response.status_code != 200:

            print("Error de Telegram:", response.text[:250])

            return False

        return True

    except requests.RequestException as error:

        print("No se pudo enviar Telegram:", error)

        return False

def parse_date(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    except (TypeError, ValueError):

        return None

def get_flag(match):

    competition = match.get("competition") or {}

    country = str(

        competition.get("country")

        or match.get("country")

        or ""

    ).strip().lower()

    if country in COUNTRY_FLAGS:

        return COUNTRY_FLAGS[country]

    home = match.get("homeTeam") or {}

    country = str(home.get("country") or "").strip().lower()

    if country in COUNTRY_FLAGS:

        return COUNTRY_FLAGS[country]

    # Si no se puede identificar el país, no se inventa una bandera.

    return "⚽"

def get_today_matches():

    now_utc = datetime.now(timezone.utc)

    today_honduras = datetime.now(LOCAL_TZ).date()

    dates = {

        now_utc.date().isoformat(),

        (now_utc.date() + timedelta(days=1)).isoformat(),

    }

    matches_by_id = {}

    for date in sorted(dates):

        result = api_get("matches", {"date": date})

        if not result:

            continue

        for match in result.get("data", []) or []:

            match_id = match.get("id")

            if match_id:

                matches_by_id[match_id] = match

    upcoming = []

    for match in matches_by_id.values():

        kickoff = parse_date(match.get("kickoffAt"))

        if not kickoff or kickoff <= now_utc:

            continue

        if kickoff.astimezone(LOCAL_TZ).date() != today_honduras:

            continue

        status = str(match.get("status", "")).lower()

        if status not in ("scheduled", "unknown"):

            continue

        home = match.get("homeTeam") or {}

        away = match.get("awayTeam") or {}

        if not home.get("id") or not away.get("id"):

            continue

        upcoming.append(match)

    upcoming.sort(

        key=lambda item: parse_date(item.get("kickoffAt"))

        or now_utc

    )

    return upcoming

def get_history(team_id, season):

    cache_key = (team_id, str(season or ""))

    if cache_key in history_cache:

        return history_cache[cache_key]

    params = {

        "team": team_id,

        "status": "finished",

    }

    if season:

        params["season"] = season

    result = api_get("matches", params)

    if not result:

        history_cache[cache_key] = []

        return []

    history_cache[cache_key] = result.get("data", []) or []

    return history_cache[cache_key]

def team_stats(team_id, season, target_kickoff):

    history = get_history(team_id, season)

    valid = []

    for match in history:

        kickoff = parse_date(match.get("kickoffAt"))

        if not kickoff or kickoff >= target_kickoff:

            continue

        if str(match.get("status", "")).lower() != "finished":

            continue

        score = match.get("score") or {}

        home_goals = score.get("home")

        away_goals = score.get("away")

        if not isinstance(home_goals, (int, float)):

            continue

        if not isinstance(away_goals, (int, float)):

            continue

        home = match.get("homeTeam") or {}

        away = match.get("awayTeam") or {}

        if home.get("id") == team_id:

            goals_for = home_goals

            goals_against = away_goals

        elif away.get("id") == team_id:

            goals_for = away_goals

            goals_against = home_goals

        else:

            continue

        valid.append({

            "date": kickoff,

            "gf": float(goals_for),

            "ga": float(goals_against),

            "total": float(home_goals + away_goals),

        })

    valid.sort(key=lambda item: item["date"], reverse=True)

    valid = valid[:HISTORY_SIZE]

    if len(valid) < HISTORY_SIZE:

        return None

    count = len(valid)

    return {

        "matches": count,

        "avg_for": sum(x["gf"] for x in valid) / count,

        "avg_against": sum(x["ga"] for x in valid) / count,

        "over_count": sum(x["total"] >= 3 for x in valid),

        "over_rate": sum(x["total"] >= 3 for x in valid) / count,

    }

def poisson_over_25(expected_goals):

    lam = max(0.01, expected_goals)

    p0 = math.exp(-lam)

    p1 = p0 * lam

    p2 = p1 * lam / 2

    return 1 - p0 - p1 - p2

def analyze(match):

    kickoff = parse_date(match.get("kickoffAt"))

    if not kickoff:

        return None

    home = match.get("homeTeam") or {}

    away = match.get("awayTeam") or {}

    season = match.get("season")

    home_stats = team_stats(home["id"], season, kickoff)

    away_stats = team_stats(away["id"], season, kickoff)

    if not home_stats or not away_stats:

        print(

            "Historial insuficiente:",

            home.get("name"), "vs", away.get("name")

        )

        return None

    home_expected = (

        home_stats["avg_for"] + away_stats["avg_against"]

    ) / 2

    away_expected = (

        away_stats["avg_for"] + home_stats["avg_against"]

    ) / 2

    expected_total = home_expected + away_expected

    model_probability = poisson_over_25(expected_total)

    probability = (

        0.50 * model_probability

        + 0.25 * home_stats["over_rate"]

        + 0.25 * away_stats["over_rate"]

    )

    probability = min(0.95, max(0.0, probability))

    return {

        "home": home.get("name", "Local"),

        "away": away.get("name", "Visitante"),

        "flag": get_flag(match),

        "probability": probability,

        "expected_goals": expected_total,

        "kickoff": kickoff,

    }

def main():

    if not OPENFOOT_API_KEY:

        raise SystemExit("Falta el secreto OPENFOOT_API_KEY.")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        raise SystemExit(

            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."

        )

    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    print("Fecha Honduras:", today)

    matches = get_today_matches()

    print("Partidos futuros para hoy:", len(matches))

    picks = []

    for match in matches:

        result = analyze(match)

        if result and result["probability"] >= MIN_PROBABILITY:

            picks.append(result)

    picks.sort(

        key=lambda item: item["probability"],

        reverse=True,

    )

    picks = picks[:MAX_ALERTS]

    if not picks:

        send_telegram(

            f"🔎 <b>OVER 2.5 GOLES — {today}</b>\n"

            "No se encontraron selecciones con datos suficientes "

            "y probabilidad estimada mínima del 60%.\n"

            "No se dispone de cuotas verificadas; no se envían picks."

        )

        print("No hay selecciones válidas.")

        return

    # No se envían alertas hasta verificar la cuota real >= 1.50.

    # OpenFoot Starter no ha confirmado una fuente de cuotas.

    print(

        f"Hay {len(picks)} candidatos estadísticos, pero no se envían "

        "alertas porque no se han verificado cuotas >= 1.50."

    )

    send_telegram(

        f"🔎 <b>ESCÁNER OVER 2.5 — {today}</b>\n"

        f"Candidatos estadísticos encontrados: {len(picks)}.\n"

        "No se enviaron picks porque esta configuración todavía no "

        "tiene una fuente de cuotas verificadas."

    )

if __name__ == "__main__":

    main()

    
