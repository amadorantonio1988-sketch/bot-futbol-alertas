import os
import math
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ============================================================
# BOT DE ALERTAS DE VALOR - API-FOOTBALL + TELEGRAM
# Diseñado para gastar pocas llamadas API.
# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"
LOCAL_TZ = ZoneInfo("America/Tegucigalpa")

# Límites conservadores para proteger el plan gratuito.
MAX_API_CALLS = 18
SAFETY_REMAINING = 8
MAX_FIXTURES = 8
MAX_ALERTS = 5

# Valor mínimo: no se exige cuota mínima.
MIN_EDGE = 0.05       # 5 puntos porcentuales
MIN_EV = 0.05         # 5% de valor esperado
MIN_PROB = 0.45       # evita picks con probabilidad demasiado baja

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
})

api_calls = 0
daily_remaining = None


def now_local():
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def api_get(endpoint, params=None):
    global api_calls, daily_remaining

    if api_calls >= MAX_API_CALLS:
        raise RuntimeError("Límite de seguridad del bot alcanzado.")

    if daily_remaining is not None and daily_remaining <= SAFETY_REMAINING:
        raise RuntimeError(
            f"Quedan solo {daily_remaining} llamadas API; "
            "el bot se detiene para proteger la cuota diaria."
        )

    url = BASE_URL + endpoint
    r = session.get(url, params=params or {}, timeout=20)

    api_calls += 1

    header_remaining = r.headers.get("x-ratelimit-requests-remaining")
    if header_remaining is not None:
        try:
            daily_remaining = int(header_remaining)
        except ValueError:
            pass

    if r.status_code == 429:
        raise RuntimeError("API-FOOTBALL devolvió 429 (límite de solicitudes).")

    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Respuesta no válida de API-FOOTBALL: HTTP {r.status_code}")

    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))

    return data


def telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("TELEGRAM no configurado.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Error Telegram:", e)


def poisson(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_probs(home_xg, away_xg, max_goals=8):
    home = [poisson(k, home_xg) for k in range(max_goals + 1)]
    away = [poisson(k, away_xg) for k in range(max_goals + 1)]

    p_home = p_draw = p_away = 0.0
    p_over15 = p_over25 = p_btts = 0.0

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = home[h] * away[a]

            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p

            if h + a >= 2:
                p_over15 += p
            if h + a >= 3:
                p_over25 += p
            if h >= 1 and a >= 1:
                p_btts += p

    return {
        "home": p_home,
        "draw": p_draw,
        "away": p_away,
        "over15": p_over15,
        "under15": 1 - p_over15,
        "over25": p_over25,
        "under25": 1 - p_over25,
        "btts": p_btts,
        "nobtts": 1 - p_btts,
    }


def parse_goals(fixture, team_id):
    teams = fixture.get("teams", {})
    goals = fixture.get("goals", {})

    home_id = teams.get("home", {}).get("id")
    away_id = teams.get("away", {}).get("id")

    if team_id == home_id:
        gf = goals.get("home")
        ga = goals.get("away")
    elif team_id == away_id:
        gf = goals.get("away")
        ga = goals.get("home")
    else:
        return None

    if gf is None or ga is None:
        return None

    return int(gf), int(ga)


def form_from_fixtures(fixtures, team_id, home_only=False, away_only=False):
    rows = []

    for f in fixtures:
        status = f.get("fixture", {}).get("status", {}).get("short")
        if status not in ("FT", "AET", "PEN"):
            continue

        teams = f.get("teams", {})
        home_id = teams.get("home", {}).get("id")
        away_id = teams.get("away", {}).get("id")

        if home_only and home_id != team_id:
            continue
        if away_only and away_id != team_id:
            continue

        result = parse_goals(f, team_id)
        if result is None:
            continue

        gf, ga = result
        rows.append((gf, ga))

    return rows


def avg(rows):
    if not rows:
        return None
    return sum(x for x, _ in rows) / len(rows), sum(y for _, y in rows) / len(rows)


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def extract_odds(odds_data):
    """
    Extrae solo mercados que podemos modelar de forma sencilla:
    Match Winner, Over/Under 1.5 y 2.5, BTTS.
    Toma la mejor cuota disponible por selección.
    """
    best = {}

    for item in odds_data.get("response", []):
        for bookmaker in item.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                name = str(bet.get("name", "")).lower()

                for value in bet.get("values", []):
                    label = str(value.get("value", "")).strip()
                    odd = safe_float(value.get("odd"))

                    if odd is None or odd <= 1.01:
                        continue

                    market = None
                    key = None

                    if "match winner" in name or name == "winner":
                        mapping = {
                            "Home": "home",
                            "Draw": "draw",
                            "Away": "away",
                        }
                        if label in mapping:
                            market = "1X2"
                            key = mapping[label]

                    elif "over/under 1.5" in name or "goals over/under 1.5" in name:
                        if label in ("Over 1.5", "Under 1.5"):
                            market = "OU15"
                            key = "over15" if "Over" in label else "under15"

                    elif "over/under 2.5" in name or "goals over/under 2.5" in name:
                        if label in ("Over 2.5", "Under 2.5"):
                            market = "OU25"
                            key = "over25" if "Over" in label else "under25"

                    elif "both teams to score" in name or name == "btts":
                        if label in ("Yes", "No"):
                            market = "BTTS"
                            key = "btts" if label == "Yes" else "nobtts"

                    if market and key:
                        if key not in best or odd > best[key]["odd"]:
                            best[key] = {
                                "market": market,
                                "key": key,
                                "label": label,
                                "odd": odd,
                                "bookmaker": bookmaker.get("name", "Bookmaker"),
                            }

    return list(best.values())


def implied_probability(odd):
    return 1.0 / odd if odd > 0 else 0.0


def score_value(prob, odd):
    imp = implied_probability(odd)
    edge = prob - imp
    ev = prob * odd - 1.0
    return imp, edge, ev


def fixture_name(f):
    return (
        f.get("teams", {}).get("home", {}).get("name", "?")
        + " vs "
        + f.get("teams", {}).get("away", {}).get("name", "?")
    )


def main():
    if not API_KEY:
        raise RuntimeError("Falta API_FOOTBALL_KEY.")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")

    date = now_local().strftime("%Y-%m-%d")

    print("=" * 64)
    print(f"💎 ESCÁNER DE VALOR — {date}")
    print("=" * 64)

    # 1 sola llamada: todos los partidos del día.
    fixtures_data = api_get(
        "/fixtures",
        {
            "date": date,
            "timezone": "America/Tegucigalpa",
        },
    )

    fixtures = []
    for f in fixtures_data.get("response", []):
        status = f.get("fixture", {}).get("status", {}).get("short")
        if status not in ("NS", "TBD"):
            continue
        fixtures.append(f)

    print(f"Partidos encontrados: {len(fixtures)}")

    if not fixtures:
        telegram(f"📊 <b>ESCÁNER DE VALOR — {date}</b>\n\nNo hay partidos pendientes hoy.")
        return

    # Preselección local: damos prioridad a ligas con partidos entre equipos
    # conocidos y a fixtures con IDs válidos. No descargamos estadísticas de todos.
    fixtures = fixtures[:MAX_FIXTURES]

    candidates = []

    # La API permite consultar odds por fixture_id.
    # Solo usamos una llamada de odds por candidato.
    for idx, f in enumerate(fixtures, 1):
        if api_calls >= MAX_API_CALLS - 2:
            break

        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue

        try:
            odds = api_get("/odds", {"fixture": fid})
            markets = extract_odds(odds)

            if not markets:
                continue

            candidates.append({
                "fixture": f,
                "fixture_id": fid,
                "markets": markets,
            })

        except RuntimeError as e:
            print(f"Odds omitidas para {fixture_name(f)}: {e}")
            break

    print(f"Candidatos con cuotas: {len(candidates)}")

    if not candidates:
        telegram(
            f"📊 <b>ESCÁNER DE VALOR — {date}</b>\n\n"
            "No encontré mercados con cuotas disponibles para analizar."
        )
        return

    # Para ahorrar API, primero usamos /predictions para cada candidato.
    # Este endpoint devuelve probabilidades y contexto estadístico de API-Football
    # en una sola llamada.
    evaluated = []

    for c in candidates:
        if api_calls >= MAX_API_CALLS - 2:
            break

        f = c["fixture"]
        fid = c["fixture_id"]

        try:
            pred = api_get("/predictions", {"fixture": fid})
            response = pred.get("response", [])
            if not response:
                continue

            p = response[0]
            percent = p.get("predictions", {}).get("percent", {})

            # API-Football entrega strings como "55%".
            ph = safe_float(str(percent.get("home", "0")).replace("%", "")) / 100
            pd = safe_float(str(percent.get("draw", "0")).replace("%", "")) / 100
            pa = safe_float(str(percent.get("away", "0")).replace("%", "")) / 100

            if max(ph, pd, pa) <= 0:
                continue

            pred_data = p.get("predictions", {})
            under_over = str(pred_data.get("under_over", "")).lower()
            advice = str(pred_data.get("advice", ""))

            # Las probabilidades de goles de la API no siempre vienen como %
            # utilizable para todos los mercados; para esos mercados usamos
            # un modelo Poisson construido con los goles recientes.
            c["prediction"] = {
                "home": ph,
                "draw": pd,
                "away": pa,
                "under_over": under_over,
                "advice": advice,
            }

            evaluated.append(c)

        except RuntimeError as e:
            print(f"Predicción omitida para {fixture_name(f)}: {e}")

    # Si ya estamos cerca del límite, usamos solo 1X2.
    # Si hay margen, analizamos últimos 5 partidos de los 2 equipos.
    final = []

    for c in evaluated:
        if api_calls >= MAX_API_CALLS - 2:
            # Solo 1X2 cuando no queda margen para estadísticas adicionales.
            probs = c["prediction"]
            poisson_p = {
                "home": probs["home"],
                "draw": probs["draw"],
                "away": probs["away"],
            }
        else:
            f = c["fixture"]
            home_id = f.get("teams", {}).get("home", {}).get("id")
            away_id = f.get("teams", {}).get("away", {}).get("id")

            try:
                # Dos llamadas: últimos 5 de cada equipo.
                home_recent = api_get("/fixtures", {"team": home_id, "last": 5})
                away_recent = api_get("/fixtures", {"team": away_id, "last": 5})

                home_rows = form_from_fixtures(
                    home_recent.get("response", []),
                    home_id,
                    home_only=True,
                )
                away_rows = form_from_fixtures(
                    away_recent.get("response", []),
                    away_id,
                    away_only=True,
                )

                # Si no hay suficientes partidos estrictamente casa/visitante,
                # usamos sus últimos 5 generales.
                if len(home_rows) < 3:
                    home_rows = form_from_fixtures(
                        home_recent.get("response", []), home_id
                    )
                if len(away_rows) < 3:
                    away_rows = form_from_fixtures(
                        away_recent.get("response", []), away_id
                    )

                ha = avg(home_rows)
                aa = avg(away_rows)

                if ha and aa:
                    home_gf, home_ga = ha
                    away_gf, away_ga = aa

                    # Mezcla conservadora de ataque propio + defensa rival.
                    home_xg = max(
                        0.20,
                        min(3.80, (home_gf * 0.60) + (away_ga * 0.40)),
                    )
                    away_xg = max(
                        0.20,
                        min(3.80, (away_gf * 0.60) + (home_ga * 0.40)),
                    )

                    pp = poisson_probs(home_xg, away_xg)
                else:
                    pp = None

            except RuntimeError as e:
                print(f"Estadísticas omitidas para {fixture_name(f)}: {e}")
                pp = None

            if pp is None:
                pp = {
                    "home": c["prediction"]["home"],
                    "draw": c["prediction"]["draw"],
                    "away": c["prediction"]["away"],
                }

        for m in c["markets"]:
            if m["key"] not in pp:
                continue

            prob = pp[m["key"]]
            if prob < MIN_PROB:
                continue

            imp, edge, ev = score_value(prob, m["odd"])

            if edge >= MIN_EDGE and ev >= MIN_EV:
                final.append({
                    "fixture": c["fixture"],
                    "market": m,
                    "prob": prob,
                    "implied": imp,
                    "edge": edge,
                    "ev": ev,
                    "advice": c["prediction"].get("advice", ""),
                })

    final.sort(key=lambda x: (x["edge"], x["ev"]), reverse=True)
    final = final[:MAX_ALERTS]

    print(f"Valor encontrado: {len(final)}")
    print(f"Llamadas API usadas: {api_calls}")
    print(f"Requests restantes según header: {daily_remaining}")

    if not final:
        telegram(
            f"📊 <b>ESCÁNER DE VALOR — {date}</b>\n\n"
            "❌ <b>NO HAY APUESTAS DE VALOR HOY.</b>\n\n"
            "El bot no fuerza picks. Solo alerta cuando la probabilidad "
            "estimada supera claramente la probabilidad implícita de la cuota."
        )
        return

    lines = [
        f"💎 <b>APUESTAS DE VALOR — {date}</b>",
        "",
        f"Encontradas: <b>{len(final)}</b>",
        "",
    ]

    for i, x in enumerate(final, 1):
        f = x["fixture"]
        m = x["market"]

        prob = x["prob"] * 100
        imp = x["implied"] * 100
        edge = x["edge"] * 100
        ev = x["ev"] * 100

        lines += [
            f"🏆 <b>#{i} {fixture_name(f)}</b>",
            f"🎯 Pick: <b>{m['label']}</b>",
            f"💰 Cuota: <b>{m['odd']:.2f}</b>",
            f"📈 Probabilidad estimada: <b>{prob:.1f}%</b>",
            f"📉 Prob. implícita: <b>{imp:.1f}%</b>",
            f"🔥 Edge: <b>+{edge:.1f}%</b>",
            f"💎 EV: <b>+{ev:.1f}%</b>",
            f"🏦 Cuota tomada de: {m['bookmaker']}",
            "",
        ]

    lines += [
        "⚠️ <i>Son estimaciones estadísticas, no garantías.</i>",
        f"🔧 API usadas: {api_calls}",
    ]

    telegram("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        msg = f"🛑 <b>BOT DETENIDO</b>\n\n{e}"
        print(msg)
        if TELEGRAM_TOKEN and CHAT_ID:
            telegram(msg)
        raise
