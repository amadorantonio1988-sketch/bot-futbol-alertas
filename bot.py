

import os

import sys

import math

import time

import logging

from datetime import datetime

from zoneinfo import ZoneInfo

import requests

API_BASE = "https://v3.football.api-sports.io"

TZ = ZoneInfo("America/Tegucigalpa")

MAX_API_CALLS = 60

MAX_ALERTS = 5

MAX_MATCHES = 8

RECENT_MATCHES = 10

MIN_PROBABILITY = 0.70

MIN_EXPECTED_GOALS = 1.65

LEAGUES = {

    2: "Champions League",

    3: "Europa League",

    848: "Conference League",

    39: "Premier League",

    140: "LaLiga",

    78: "Bundesliga",

    135: "Serie A",

    61: "Ligue 1",

    94: "Primeira Liga",

    88: "Eredivisie",

    71: "Brasileirao Serie A",

    72: "Brasileirao Serie B",

    128: "Liga Argentina",

    13: "Copa Libertadores",

    11: "Copa Sudamericana",

    239: "Liga Colombia",

    268: "Liga Uruguay",

    265: "Liga Chile",

    242: "Liga Ecuador",

    262: "Liga MX",

    281: "Liga Expansion MX",

    253: "MLS",

}

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s %(levelname)s %(message)s"

)

log = logging.getLogger("bot-futbol")

class FootballAPI:

    def __init__(self, key):

        self.session = requests.Session()

        self.session.headers.update({"x-apisports-key": key})

        self.calls = 0

        self.cache = {}

        self.remaining = None

    def get(self, endpoint, params=None):

        params = params or {}

        cache_key = (endpoint, tuple(sorted(params.items())))

        if cache_key in self.cache:

            return self.cache[cache_key]

        if self.calls >= MAX_API_CALLS:

            log.warning("Límite local de consultas alcanzado.")

            return None

        if self.remaining is not None and self.remaining <= 2:

            log.warning("Quedan muy pocas consultas. Se detiene el bot.")

            return None

        self.calls += 1

        try:

            response = self.session.get(

                API_BASE + endpoint,

                params=params,

                timeout=20

            )

            remaining = response.headers.get(

                "x-ratelimit-requests-remaining"

            )

            if remaining is not None:

                try:

                    self.remaining = int(remaining)

                except ValueError:

                    pass

            if response.status_code == 429:

                log.error("API-Football alcanzó un límite. No se reintenta.")

                return None

            response.raise_for_status()

            payload = response.json()

            if payload.get("errors"):

                log.warning("Error de API: %s", payload["errors"])

                return None

            result = payload.get("response", [])

            self.cache[cache_key] = result

            time.sleep(0.25)

            return result

        except (requests.RequestException, ValueError) as exc:

            log.warning("Consulta fallida: %s", exc)

            return None

def poisson_over(total_xg, line):

    """Probabilidad aproximada de superar una línea de goles."""

    threshold = int(math.floor(line))

    cumulative = 0.0

    for goals in range(threshold + 1):

        cumulative += (

            math.exp(-total_xg)

            * total_xg ** goals

            / math.factorial(goals)

        )

    return max(0.0, min(1.0, 1.0 - cumulative))

def extract_goals(fixture, team_id):

    teams = fixture.get("teams", {})

    goals = fixture.get("goals", {})

    home = teams.get("home", {})

    away = teams.get("away", {})

    gh = goals.get("home")

    ga = goals.get("away")

    if gh is None or ga is None:

        return None

    if team_id == home.get("id"):

        return int(gh), int(ga), True

    if team_id == away.get("id"):

        return int(ga), int(gh), False

    return None

def recent_stats(api, team_id):

    fixtures = api.get(

        "/fixtures",

        {"team": team_id, "last": RECENT_MATCHES}

    )

    if not fixtures:

        return None

    scored, conceded = [], []

    home_scored, home_conceded = [], []

    away_scored, away_conceded = [], []

    for fixture in fixtures:

        status = fixture.get("fixture", {}).get("status", {}).get("short")

        if status not in ("FT", "AET", "PEN"):

            continue

        values = extract_goals(fixture, team_id)

        if values is None:

            continue

        gf, ga, is_home = values

        scored.append(gf)

        conceded.append(ga)

        if is_home:

            home_scored.append(gf)

            home_conceded.append(ga)

        else:

            away_scored.append(gf)

            away_conceded.append(ga)

    if len(scored) < 5:

        return None

    return {

        "n": len(scored),

        "gf": sum(scored) / len(scored),

        "ga": sum(conceded) / len(conceded),

        "hgf": (

            sum(home_scored) / len(home_scored)

            if home_scored else None

        ),

        "hga": (

            sum(home_conceded) / len(home_conceded)

            if home_conceded else None

        ),

        "agf": (

            sum(away_scored) / len(away_scored)

            if away_scored else None

        ),

        "aga": (

            sum(away_conceded) / len(away_conceded)

            if away_conceded else None

        ),

    }

def estimate_goals(home, away):

    home_attack = 0.65 * (

        home["hgf"] if home["hgf"] is not None else home["gf"]

    ) + 0.35 * home["gf"]

    away_defence = 0.65 * (

        away["aga"] if away["aga"] is not None else away["ga"]

    ) + 0.35 * away["ga"]

    away_attack = 0.65 * (

        away["agf"] if away["agf"] is not None else away["gf"]

    ) + 0.35 * away["gf"]

    home_defence = 0.65 * (

        home["hga"] if home["hga"] is not None else home["ga"]

    ) + 0.35 * home["ga"]

    home_xg = max(0.05, (home_attack + away_defence) / 2)

    away_xg = max(0.05, (away_attack + home_defence) / 2)

    return home_xg, away_xg

def get_over_odds(api, fixture_id):

    odds_data = api.get("/odds", {"fixture": fixture_id})

    odds = {}

    if not odds_data:

        return odds

    for entry in odds_data:

        for bookmaker in entry.get("bookmakers", []):

            for bet in bookmaker.get("bets", []):

                name = (bet.get("name") or "").lower()

                if "total" not in name and "over/under" not in name:

                    continue

                for value in bet.get("values", []):

                    label = (value.get("value") or "").lower()

                    label = label.replace(" ", "")

                    for line in (0.5, 1.5, 2.5, 3.5, 4.5):

                        if label in (f"over{line}", f"over{line:.1f}"):

                            try:

                                odds[line] = float(value["odd"])

                            except (KeyError, TypeError, ValueError):

                                pass

    return odds

def send_telegram(message):

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:

        log.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")

        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:

        response = requests.post(

            url,

            json={

                "chat_id": chat_id,

                "text": message,

                "disable_web_page_preview": True

            },

            timeout=20

        )

        response.raise_for_status()

        return bool(response.json().get("ok"))

    except (requests.RequestException, ValueError) as exc:

        log.error("Error de Telegram: %s", exc)

        return False

def main():

    key = os.getenv("API_FOOTBALL_KEY", "").strip()

    if not key:

        log.error("Falta API_FOOTBALL_KEY.")

        sys.exit(1)

    today = datetime.now(TZ).date().isoformat()

    log.info("Analizando partidos del día: %s", today)

    api = FootballAPI(key)

    fixtures = api.get("/fixtures", {"date": today})

    if fixtures is None:

        log.error("No se pudo recuperar la cartelera.")

        sys.exit(1)

    matches = []

    for fixture in fixtures:

        league = fixture.get("league", {})

        if league.get("id") not in LEAGUES:

            continue

        status = fixture.get("fixture", {}).get("status", {}).get("short")

        if status not in ("NS", "TBD"):

            continue

        teams = fixture.get("teams", {})

        home = teams.get("home", {})

        away = teams.get("away", {})

        if not home.get("id") or not away.get("id"):

            continue

        matches.append({

            "id": fixture.get("fixture", {}).get("id"),

            "date": fixture.get("fixture", {}).get("date", ""),

            "league": LEAGUES[league["id"]],

            "home": home,

            "away": away

        })

    # Orden reproducible y límite para proteger el cupo de la API.

    matches.sort(key=lambda m: m["date"])

    matches = matches[:MAX_MATCHES]

    team_cache = {}

    picks = []

    for match in matches:

        home_id = match["home"]["id"]

        away_id = match["away"]["id"]

        if home_id not in team_cache:

            team_cache[home_id] = recent_stats(api, home_id)

        if away_id not in team_cache:

            team_cache[away_id] = recent_stats(api, away_id)

        home = team_cache[home_id]

        away = team_cache[away_id]

        if not home or not away:

            continue

        home_xg, away_xg = estimate_goals(home, away)

        total_xg = home_xg + away_xg

        if total_xg < MIN_EXPECTED_GOALS:

            continue

        qualifying = []

        for line in (0.5, 1.5, 2.5, 3.5, 4.5):

            probability = poisson_over(total_xg, line)

            if probability >= MIN_PROBABILITY:

                qualifying.append((line, probability))

        if not qualifying:

            continue

        # La línea con más goles que todavía supera el umbral de

        # probabilidad se prefiere para no elegir siempre Over 0.5.

        line, probability = max(qualifying, key=lambda item: item[0])

        # Consultar cuotas solo para los candidatos seleccionados.

        odds = get_over_odds(api, match["id"])

        picks.append({

            **match,

            "line": line,

            "probability": probability,

            "total_xg": total_xg,

            "home_xg": home_xg,

            "away_xg": away_xg,

            "odds": odds,

            "home_stats": home,

            "away_stats": away

        })

    picks.sort(

        key=lambda p: (p["probability"], p["total_xg"]),

        reverse=True

    )

    picks = picks[:MAX_ALERTS]

    log.info("Consultas API usadas: %s", api.calls)

    if not picks:

        log.info("No hay picks que cumplan los filtros. No se envía apuesta.")

        return

    message = [

        f"⚽ ALERTAS DE GOLES — {today}",

        f"Picks seleccionados: {len(picks)}",

        ""

    ]

    for i, p in enumerate(picks, 1):

        odd = p["odds"].get(p["line"])

        odd_text = f"{odd:.2f}" if odd is not None else "No disponible"

        message.extend([

            f"🏆 #{i} {p['home']['name']} vs {p['away']['name']}",

            f"🏟️ {p['league']}",

            f"🕒 Inicio: {p['date'].replace('T', ' ')[:16]} UTC",

            f"🎯 Pick: Over {p['line']:.1f} goles",

            f"💰 Cuota: {odd_text}",

            f"📈 Probabilidad estimada: {p['probability'] * 100:.1f}%",

            f"⚽ Goles esperados estimados: {p['total_xg']:.2f}",

            f"Local: GF {p['home_stats']['gf']:.2f}, "

            f"GC {p['home_stats']['ga']:.2f} por partido.",

            f"Visitante: GF {p['away_stats']['gf']:.2f}, "

            f"GC {p['away_stats']['ga']:.2f} por partido.",

            ""

        ])

    message.append(

        "Nota: estimación orientativa basada en goles recientes; "

        "no es garantía. xG real y tiros a puerta no se inventan "

        "si la API no los proporciona."

    )

    if not send_telegram("\n".join(message)):

        sys.exit(1)

    log.info("Alerta enviada correctamente.")

if __name__ == "__main__":

    main()
