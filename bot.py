#!/usr/bin/env python3
"""Bot diario: mejores señales Over 2.5 en ligas seleccionadas."""
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
MAX_API_CALLS = int(os.getenv("MAX_API_CALLS", "60"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
MAX_MATCHES_TO_ANALYZE = int(os.getenv("MAX_MATCHES_TO_ANALYZE", "10"))
RECENT_TEAM_MATCHES = 10
MIN_PROBABILITY = float(os.getenv("MIN_PROBABILITY", "0.70"))
MIN_ODDS = float(os.getenv("MIN_ODDS", "1.50"))  # superior a 1.49
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.25"))

# Competiciones objetivo: grandes ligas europeas, torneos continentales,
# primeras divisiones sudamericanas y Liga MX.
LEAGUES = {
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Conference League",
    39: "Premier League",
    140: "LaLiga",
    135: "Serie A italiana",
    78: "Bundesliga",
    61: "Ligue 1",
    94: "Primeira Liga",
    88: "Eredivisie",
    71: "Brasileirão Série A",
    128: "Liga Profesional Argentina",
    13: "Copa Libertadores",
    11: "Copa Sudamericana",
    239: "Primera A Colombia",
    268: "Primera División Uruguay",
    265: "Primera División Chile",
    242: "LigaPro Ecuador",
    262: "Liga MX",
}

LEAGUE_PRIORITY = {
    39: 0, 140: 1, 135: 2, 78: 3, 61: 4,
    128: 5, 262: 6, 71: 7, 2: 8, 3: 9,
    13: 10, 11: 11, 94: 12, 88: 13, 848: 14,
    239: 15, 242: 16, 265: 17, 268: 18,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot-futbol")


class FootballAPI:
    def __init__(self, key):
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": key})
        self.calls = 0
        self.cache = {}
        self.remaining = None
        self.stopped = False

    def get(self, endpoint, params=None):
        params = params or {}
        cache_key = (endpoint, tuple(sorted(params.items())))
        if cache_key in self.cache:
            return self.cache[cache_key]
        if self.stopped or self.calls >= MAX_API_CALLS:
            log.warning("Presupuesto local de consultas alcanzado; no se hacen más solicitudes.")
            return None
        if self.remaining is not None and self.remaining <= 2:
            log.warning("Quedan solo %s consultas según la API; se detiene el bot.", self.remaining)
            self.stopped = True
            return None

        self.calls += 1
        try:
            response = self.session.get(API_BASE + endpoint, params=params, timeout=20)
            remaining = response.headers.get("x-ratelimit-requests-remaining")
            if remaining is not None:
                try:
                    self.remaining = int(remaining)
                except ValueError:
                    pass
            if response.status_code == 429:
                log.error("API-Football respondió 429/límite alcanzado. No se reintenta.")
                self.stopped = True
                return None
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                log.warning("Error de API en %s: %s", endpoint, payload["errors"])
                # Si la API indica que se agotó el límite, detener las consultas.
                error_text = str(payload["errors"]).lower()
                if "limit" in error_text or "requests" in error_text:
                    self.stopped = True
                return None
            result = payload.get("response", [])
            self.cache[cache_key] = result
            time.sleep(REQUEST_DELAY)
            return result
        except (requests.RequestException, ValueError) as exc:
            log.warning("Falló consulta %s: %s", endpoint, exc)
            return None


def poisson_over(total_goals, line=2.5):
    """Probabilidad de marcar más de 2.5 goles con una Poisson simple."""
    threshold = int(math.floor(line))
    cumulative = sum(
        math.exp(-total_goals) * total_goals ** goals / math.factorial(goals)
        for goals in range(threshold + 1)
    )
    return max(0.0, min(1.0, 1.0 - cumulative))


def get_team_recent_stats(api, team_id):
    """Calcula estadísticas de los últimos 10 partidos del equipo, en cualquier condición."""
    today = datetime.now(TZ).date()
    from datetime import timedelta
    date_from = (today - timedelta(days=365)).isoformat()
    date_to = today.isoformat()
    collected = {}

    # La API gratuita exige season y no permite el parámetro `last`.
    # Consultamos la temporada actual y, si hace falta, la anterior para completar la muestra.
    for season in (today.year, today.year - 1):
        rows = api.get("/fixtures", {
            "team": team_id,
            "season": season,
            "from": date_from,
            "to": date_to,
            "timezone": "America/Tegucigalpa",
        })
        if rows is None:
            # Si la API se detuvo por límite/error, no continuar haciendo llamadas.
            if api.stopped or api.calls >= MAX_API_CALLS:
                break
            continue

        for fixture in rows:
            fixture_id = (fixture.get("fixture") or {}).get("id")
            status = ((fixture.get("fixture") or {}).get("status") or {}).get("short")
            if not fixture_id or status not in ("FT", "AET", "PEN"):
                continue
            teams = fixture.get("teams") or {}
            goals = fixture.get("goals") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home_goals, away_goals = goals.get("home"), goals.get("away")
            if home_goals is None or away_goals is None:
                continue

            if home.get("id") == team_id:
                gf, ga = int(home_goals), int(away_goals)
            elif away.get("id") == team_id:
                gf, ga = int(away_goals), int(home_goals)
            else:
                continue
            collected[fixture_id] = {
                "date": (fixture.get("fixture") or {}).get("date") or "",
                "gf": gf,
                "ga": ga,
            }

        if len(collected) >= RECENT_TEAM_MATCHES:
            break
        if api.stopped or api.calls >= MAX_API_CALLS:
            break

    recent = sorted(collected.values(), key=lambda row: row["date"], reverse=True)[:RECENT_TEAM_MATCHES]
    if len(recent) < RECENT_TEAM_MATCHES:
        log.info(
            "Equipo ID %s: solo se obtuvieron %s partidos generales válidos; se necesitan %s.",
            team_id, len(recent), RECENT_TEAM_MATCHES
        )
        return None

    totals = [row["gf"] + row["ga"] for row in recent]
    return {
        "n": len(recent),
        "gf": sum(row["gf"] for row in recent) / len(recent),
        "ga": sum(row["ga"] for row in recent) / len(recent),
        "over25_rate": sum(total >= 3 for total in totals) / len(totals),
        "over25_count": sum(total >= 3 for total in totals),
    }


def estimate_expected_goals(home_stats, away_stats):
    # Estima goles con las medias generales de los últimos 10 partidos de cada equipo,
    # sin separar sus resultados como local o visitante.
    home_xg = max(0.05, (home_stats["gf"] + away_stats["ga"]) / 2)
    away_xg = max(0.05, (away_stats["gf"] + home_stats["ga"]) / 2)
    return home_xg, away_xg


def get_over25_odds(api, fixture_id):
    """Devuelve una cuota Over 2.5 disponible; no inventa cuotas si faltan."""
    rows = api.get("/odds", {"fixture": fixture_id})
    found = []
    if not rows:
        return None

    for entry in rows:
        for bookmaker in entry.get("bookmakers", []) or []:
            for bet in bookmaker.get("bets", []) or []:
                bet_name = (bet.get("name") or "").lower()
                # Totals/Over-Under de goles, evitando mercados de córners y tarjetas.
                if not any(term in bet_name for term in ("goal", "total", "over/under")):
                    continue
                if any(term in bet_name for term in ("corner", "card", "booking", "player")):
                    continue
                for value in bet.get("values", []) or []:
                    label = "".join((value.get("value") or "").lower().split())
                    if label not in ("over2.5", "over2,5", "over2.50", "over2,50"):
                        continue
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if odd >= MIN_ODDS:
                        found.append(odd)
    # Usa la cuota más baja que cumple el umbral entre las disponibles, opción conservadora.
    return min(found) if found else None


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en GitHub Secrets.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            log.error("Telegram no confirmó el envío: %s", body)
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        log.error("Error enviando a Telegram: %s", exc)
        return False


def main():
    api_key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not api_key:
        log.error("Falta API_FOOTBALL_KEY en GitHub Secrets.")
        sys.exit(1)

    today = datetime.now(TZ).date().isoformat()
    log.info("Fecha Honduras: %s", today)
    log.info("Filtros: Over 2.5, últimos 10 partidos generales por equipo, probabilidad estimada >= %.1f%%, cuota >= %.2f; máximo %d alertas.",
             MIN_PROBABILITY * 100, MIN_ODDS, MAX_ALERTS)

    api = FootballAPI(api_key)
    fixtures = api.get("/fixtures", {"date": today, "timezone": "America/Tegucigalpa"})
    if fixtures is None:
        log.error("No se pudo recuperar la cartelera del día.")
        sys.exit(1)

    log.info("Partidos devueltos por API para la fecha: %d", len(fixtures))
    matches = []
    for fixture in fixtures:
        league = fixture.get("league") or {}
        league_id = league.get("id")
        if league_id not in LEAGUES:
            continue
        status = ((fixture.get("fixture") or {}).get("status") or {}).get("short", "")
        if status not in ("NS", "TBD"):
            continue
        teams = fixture.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        fixture_id = (fixture.get("fixture") or {}).get("id")
        if not fixture_id or not home.get("id") or not away.get("id"):
            continue
        matches.append({
            "id": fixture_id,
            "date": (fixture.get("fixture") or {}).get("date", ""),
            "league_id": league_id,
            "league": LEAGUES[league_id],
            "home": home,
            "away": away,
        })

    matches.sort(key=lambda match: (
        LEAGUE_PRIORITY.get(match["league_id"], 99), match["date"]
    ))
    log.info("Partidos futuros de ligas objetivo: %d", len(matches))
    if len(matches) > MAX_MATCHES_TO_ANALYZE:
        log.info("Se evaluarán hasta %d partidos para respetar el presupuesto de API.", MAX_MATCHES_TO_ANALYZE)
    matches = matches[:MAX_MATCHES_TO_ANALYZE]

    team_cache = {}
    picks = []
    for match in matches:
        if api.stopped or api.calls >= MAX_API_CALLS:
            log.warning("Se detiene el análisis por presupuesto de API.")
            break
        home_id, away_id = match["home"]["id"], match["away"]["id"]
        home_key, away_key = home_id, away_id
        if home_key not in team_cache:
            team_cache[home_key] = get_team_recent_stats(api, home_id)
        if away_key not in team_cache:
            team_cache[away_key] = get_team_recent_stats(api, away_id)
        home_stats, away_stats = team_cache[home_key], team_cache[away_key]
        if not home_stats or not away_stats:
            log.info("Se omite %s vs %s: no se obtuvieron 10 partidos generales válidos.",
                     match["home"].get("name"), match["away"].get("name"))
            continue

        home_xg, away_xg = estimate_expected_goals(home_stats, away_stats)
        total_xg = home_xg + away_xg
        probability = poisson_over(total_xg, 2.5)
        log.info("%s vs %s | liga=%s | Over 2.5 estimado=%.1f%% | goles esperados=%.2f",
                 match["home"].get("name"), match["away"].get("name"),
                 match["league"], probability * 100, total_xg)

        if probability < MIN_PROBABILITY:
            log.info("Se descarta: probabilidad %.1f%% menor que %.1f%%.",
                     probability * 100, MIN_PROBABILITY * 100)
            continue

        # Solo consulta cuotas para partidos con suficiente probabilidad estadística.
        odd = get_over25_odds(api, match["id"])
        if odd is None:
            log.info("Se descarta %s vs %s: no hay cuota Over 2.5 disponible >= %.2f.",
                     match["home"].get("name"), match["away"].get("name"), MIN_ODDS)
            continue

        picks.append({
            **match, "probability": probability, "odd": odd,
            "total_xg": total_xg, "home_xg": home_xg, "away_xg": away_xg,
            "home_stats": home_stats, "away_stats": away_stats,
        })

    picks.sort(key=lambda pick: (pick["probability"], pick["total_xg"]), reverse=True)
    picks = picks[:MAX_ALERTS]
    log.info("Consultas API usadas: %d; picks que cumplen todos los filtros: %d", api.calls, len(picks))

    if not picks:
        log.info("No se encontraron picks Over 2.5 con probabilidad y cuota suficientes. No se envía alerta de apuesta.")
        return

    message = [f"⚽ MEJORES SEÑALES OVER 2.5 — {today} (Honduras)",
               f"Picks que cumplen todos los filtros: {len(picks)}", ""]
    for index, pick in enumerate(picks, 1):
        kickoff = pick["date"].replace("T", " ")[:16]
        message.extend([
            f"🏆 #{index} {pick['home'].get('name')} vs {pick['away'].get('name')}",
            f"🏟️ {pick['league']} | Inicio: {kickoff} (hora Honduras)",
            "🎯 Pronóstico: Over 2.5 goles",
            f"💰 Cuota Over 2.5: {pick['odd']:.2f}",
            f"📈 Probabilidad estimada: {pick['probability'] * 100:.1f}%",
            f"⚽ Goles esperados estimados: {pick['total_xg']:.2f} (local {pick['home_xg']:.2f} + visitante {pick['away_xg']:.2f})",
            f"📊 {pick['home'].get('name')}, últimos 10 partidos generales: GF {pick['home_stats']['gf']:.2f}, GC {pick['home_stats']['ga']:.2f}; Over 2.5 en {pick['home_stats']['over25_rate'] * 100:.0f}% ({pick['home_stats']['over25_count']}/10).",
            f"📊 {pick['away'].get('name')}, últimos 10 partidos generales: GF {pick['away_stats']['gf']:.2f}, GC {pick['away_stats']['ga']:.2f}; Over 2.5 en {pick['away_stats']['over25_rate'] * 100:.0f}% ({pick['away_stats']['over25_count']}/10).",
            "",
        ])
    message.append(
        "Nota: la probabilidad es una estimación orientativa basada en goles recientes y Poisson, no una garantía. "
        "Solo se incluyen cuotas disponibles en la API de al menos 1.50."
    )

    if not send_telegram("\n".join(message)):
        sys.exit(1)
    log.info("Alerta enviada correctamente a Telegram.")


if __name__ == "__main__":
    main()
