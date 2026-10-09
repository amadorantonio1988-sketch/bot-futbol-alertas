import os
import time
import math
import requests
from datetime import datetime, timezone, timedelta

# Bot de alertas de fútbol: API-Football + Telegram
# GitHub Actions secrets necesarios:
# API_FOOTBALL_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MIN_ODD = float(os.getenv("MIN_ODD", "1.50"))
MAX_ODD = float(os.getenv("MAX_ODD", "2.20"))
MIN_PROB = float(os.getenv("MIN_PROBABILITY", "0.61"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))
MAX_FIXTURES_TO_CHECK = int(os.getenv("MAX_FIXTURES_TO_CHECK", "20"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.35"))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET_HOURS", "-6"))

session = requests.Session()
session.headers.update({"x-apisports-key": API_KEY})
request_count = 0


def telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram no configurado; se muestra el mensaje en el log.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=20)
    r.raise_for_status()


def api_get(endpoint, params=None):
    global request_count
    if not API_KEY:
        raise RuntimeError("Falta el secreto API_FOOTBALL_KEY.")
    request_count += 1
    for attempt in range(3):
        try:
            r = session.get(f"{BASE_URL}/{endpoint}", params=params or {}, timeout=25)
            if r.status_code == 429:
                raise RuntimeError("API-Football respondió 429: límite de solicitudes alcanzado.")
            r.raise_for_status()
            data = r.json()
            errors = data.get("errors")
            if errors:
                raise RuntimeError(f"Error API-Football: {errors}")
            time.sleep(REQUEST_DELAY)
            return data.get("response", [])
        except RuntimeError:
            raise
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def local_date():
    return (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)).date().isoformat()


def get_fixtures(date_str):
    return api_get("fixtures", {"date": date_str, "timezone": "America/Tegucigalpa"})


def get_last_five(team_id, before_date):
    # Una sola llamada por equipo; se consulta solo para un máximo pequeño de partidos.
    rows = api_get("fixtures", {
        "team": team_id,
        "last": 5,
        "status": "FT",
        "timezone": "America/Tegucigalpa"
    })
    # El endpoint puede devolver los últimos partidos completados, independientemente
    # de la fecha local; excluir por seguridad los que no estén terminados.
    games = []
    for item in rows:
        fixture = item.get("fixture", {})
        goals = item.get("goals", {})
        home = item.get("teams", {}).get("home", {})
        away = item.get("teams", {}).get("away", {})
        if goals.get("home") is None or goals.get("away") is None:
            continue
        if home.get("id") == team_id:
            gf, ga = goals["home"], goals["away"]
        else:
            gf, ga = goals["away"], goals["home"]
        games.append((int(gf), int(ga)))
    return games[:5]


def get_odds_for_fixture(fixture_id):
    rows = api_get("odds", {"fixture": fixture_id})
    found = []
    for row in rows:
        for bookmaker in row.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                name = (bet.get("name") or "").lower()
                for value in bet.get("values", []):
                    label = (value.get("value") or "").strip()
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1.0:
                        continue
                    # Normalizar nombres comunes de mercados.
                    market = None
                    pick = None
                    if "both teams score" in name or name == "btts":
                        if label.lower() == "yes":
                            market, pick = "BTTS", "Ambos anotan: Sí"
                    elif "over/under" in name or "goals over/under" in name or name == "goals":
                        normalized = label.replace(",", ".").lower()
                        if "over 1.5" in normalized:
                            market, pick = "OVER15", "Más de 1.5 goles"
                        elif "over 2.5" in normalized:
                            market, pick = "OVER25", "Más de 2.5 goles"
                        elif "under 3.5" in normalized:
                            market, pick = "UNDER35", "Menos de 3.5 goles"
                    if market:
                        found.append({
                            "market": market,
                            "pick": pick,
                            "odd": odd,
                            "bookmaker": bookmaker.get("name", "Casa no identificada")
                        })
    # Mantener la cuota más alta disponible para cada mercado.
    best = {}
    for item in found:
        if item["market"] not in best or item["odd"] > best[item["market"]]["odd"]:
            best[item["market"]] = item
    return list(best.values())


def safe_rate(values):
    return sum(values) / len(values) if values else 0.0


def estimate_probability(market, home_games, away_games):
    # Estimación heurística basada en resultados recientes. No es un modelo calibrado.
    if len(home_games) < 3 or len(away_games) < 3:
        return None
    if market == "OVER15":
        home_rate = sum(1 for gf, ga in home_games if gf + ga >= 2) / len(home_games)
        away_rate = sum(1 for gf, ga in away_games if gf + ga >= 2) / len(away_games)
        return 0.45 * home_rate + 0.45 * away_rate + 0.10 * 0.72
    if market == "OVER25":
        home_rate = sum(1 for gf, ga in home_games if gf + ga >= 3) / len(home_games)
        away_rate = sum(1 for gf, ga in away_games if gf + ga >= 3) / len(away_games)
        return 0.45 * home_rate + 0.45 * away_rate + 0.10 * 0.48
    if market == "BTTS":
        home_rate = sum(1 for gf, ga in home_games if gf > 0 and ga > 0) / len(home_games)
        away_rate = sum(1 for gf, ga in away_games if gf > 0 and ga > 0) / len(away_games)
        return 0.45 * home_rate + 0.45 * away_rate + 0.10 * 0.50
    if market == "UNDER35":
        home_rate = sum(1 for gf, ga in home_games if gf + ga <= 3) / len(home_games)
        away_rate = sum(1 for gf, ga in away_games if gf + ga <= 3) / len(away_games)
        return 0.45 * home_rate + 0.45 * away_rate + 0.10 * 0.70
    return None


def form_text(games):
    if not games:
        return "sin datos"
    avg_for = safe_rate([gf for gf, ga in games])
    avg_against = safe_rate([ga for gf, ga in games])
    return f"{avg_for:.2f} a favor / {avg_against:.2f} en contra por partido"


def league_priority(league_name, country):
    """Prioriza competiciones conocidas sin excluir automáticamente otras ligas."""
    text = f"{league_name} {country}".lower()
    priority_terms = [
        ("champions league", 100), ("premier league", 95), ("la liga", 95),
        ("serie a", 95), ("bundesliga", 95), ("ligue 1", 90),
        ("europa league", 90), ("conference league", 85),
        ("libertadores", 90), ("sudamericana", 85),
        ("brasileirão", 85), ("brasileirao", 85), ("copa do brasil", 80),
        ("eredivisie", 80), ("primeira liga", 80), ("mls", 75),
        ("liga mx", 75), ("primera división", 75), ("primera division", 75),
        ("world cup", 95), ("qualifier", 75), ("qualifying", 75)
    ]
    for term, score in priority_terms:
        if term in text:
            return score
    # Otras competiciones siguen siendo elegibles, pero quedan detrás de las prioritarias.
    return 40


def main():
    date_str = local_date()
    print("=" * 58)
    print(f"BOT DE ALERTAS DE FÚTBOL — {date_str} (Honduras)")
    print("=" * 58)

    if not API_KEY:
        raise RuntimeError("Configura el secreto API_FOOTBALL_KEY en GitHub.")
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Aviso: faltan secretos de Telegram; el resultado solo aparecerá en el log.")

    fixtures = get_fixtures(date_str)
    upcoming = []
    for item in fixtures:
        fixture = item.get("fixture", {})
        status = fixture.get("status", {}).get("short", "")
        if status not in ("NS", "TBD"):
            continue
        home = item.get("teams", {}).get("home", {})
        away = item.get("teams", {}).get("away", {})
        if not home.get("id") or not away.get("id"):
            continue
        league = item.get("league", {})
        upcoming.append({
            "id": fixture.get("id"),
            "timestamp": fixture.get("timestamp", 0),
            "home": home,
            "away": away,
            "league": league.get("name", "Liga no identificada"),
            "country": league.get("country", ""),
            "fixture": fixture
        })
    # Primero competiciones de mayor interés; dentro de cada nivel, los partidos más próximos.
    upcoming.sort(key=lambda x: (-league_priority(x["league"], x["country"]), x["timestamp"]))

    if not upcoming:
        telegram(f"⚽ <b>BOT DE FÚTBOL — {date_str}</b>\nNo encontré partidos pendientes para hoy en la respuesta de la API.")
        print("No hay partidos pendientes.")
        return

    # Límite deliberado para proteger la cuota diaria. Cada partido puede requerir
    # una consulta de cuotas y dos consultas de forma reciente.
    candidates = upcoming[:MAX_FIXTURES_TO_CHECK]
    print(f"Partidos pendientes encontrados: {len(upcoming)}")
    print(f"Partidos seleccionados para análisis detallado: {len(candidates)}")
    picks = []

    for idx, game in enumerate(candidates, 1):
        try:
            print(f"[{idx}/{len(candidates)}] {game['home'].get('name')} vs {game['away'].get('name')}")
            odds = get_odds_for_fixture(game["id"])
            if not odds:
                print("  Sin cuotas compatibles.")
                continue

            home_games = get_last_five(game["home"]["id"], date_str)
            away_games = get_last_five(game["away"]["id"], date_str)

            for odd in odds:
                price = odd["odd"]
                if price <= MIN_ODD or price > MAX_ODD:
                    continue
                probability = estimate_probability(odd["market"], home_games, away_games)
                if probability is None:
                    continue
                implied = 1.0 / price
                edge = probability - implied
                if probability >= MIN_PROB and edge >= MIN_EDGE:
                    kickoff = datetime.fromtimestamp(
                        game["timestamp"], timezone.utc
                    ).astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET))).strftime("%H:%M")
                    picks.append({
                        **odd,
                        "home": game["home"].get("name", "Local"),
                        "away": game["away"].get("name", "Visitante"),
                        "league": game["league"],
                        "country": game["country"],
                        "kickoff": kickoff,
                        "probability": probability,
                        "implied": implied,
                        "edge": edge,
                        "home_form": form_text(home_games),
                        "away_form": form_text(away_games)
                    })
        except Exception as exc:
            print(f"  Error en este partido: {exc}")
            if "límite de solicitudes" in str(exc).lower() or "429" in str(exc):
                telegram("🛑 <b>BOT DETENIDO</b>\nLa API de fútbol alcanzó su límite de solicitudes. No se harán más consultas en esta ejecución.")
                return

    picks.sort(key=lambda p: (p["edge"], p["probability"]), reverse=True)
    picks = picks[:MAX_ALERTS]

    if not picks:
        message = (
            f"⚽ <b>ESCÁNER DE FÚTBOL — {date_str}</b>\n\n"
            f"Partidos pendientes: {len(upcoming)}\n"
            f"Partidos revisados: {len(candidates)}\n\n"
            "No encontré picks que cumplan todos los filtros hoy.\n"
            f"Filtros: cuota > {MIN_ODD:.2f} y ≤ {MAX_ODD:.2f}, probabilidad estimada ≥ {MIN_PROB:.0%}, "
            f"edge ≥ {MIN_EDGE:.0%}.\n\n"
            "No apostar también es una decisión válida."
        )
        telegram(message)
        print(message)
        return

    lines = [f"⚽ <b>MEJORES PICKS DEL DÍA — {date_str}</b>",
             f"Revisados: {len(candidates)} de {len(upcoming)} partidos",
             "Probabilidades heurísticas basadas en últimos 5 partidos; no garantizan resultados.\n"]
    for i, p in enumerate(picks, 1):
        lines.extend([
            f"🏆 <b>#{i} {p['home']} vs {p['away']}</b>",
            f"🏟️ {p['league']} {('· ' + p['country']) if p['country'] else ''} | ⏰ {p['kickoff']} (Honduras)",
            f"🎯 {p['pick']}",
            f"💰 Cuota: <b>{p['odd']:.2f}</b> ({p['bookmaker']})",
            f"📈 Prob. estimada: {p['probability']:.1%} | Implícita: {p['implied']:.1%}",
            f"💎 Edge estimado: {p['edge']:+.1%}",
            f"📊 Local últimos 5: {p['home_form']}",
            f"📊 Visitante últimos 5: {p['away_form']}",
            "—"
        ])
    lines.append("⚠️ Solo análisis estadístico; no existe apuesta segura. Verifica que la cuota siga disponible.")
    message = "\n".join(lines)
    telegram(message)
    print(message)
    print(f"Solicitudes API en esta ejecución: {request_count}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR FATAL: {exc}")
        try:
            telegram(f"🛑 <b>BOT DE FÚTBOL</b>\nLa ejecución terminó con error:\n{str(exc)[:700]}")
        except Exception:
            pass
        raise
