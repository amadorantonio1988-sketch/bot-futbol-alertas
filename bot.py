

import os

import math

import time

import requests

from datetime import datetime, timezone

from zoneinfo import ZoneInfo

# ============================================================

# ESCANER DE FUTBOL - API-FOOTBALL + TELEGRAM

# Selecciona el mejor mercado disponible por partido.

# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

LOCAL_TZ = ZoneInfo("America/Tegucigalpa")

# Proteccion del plan API

MAX_API_CALLS = 18

SAFETY_REMAINING = 8

MAX_FIXTURES = 4

MAX_ALERTS = 5

REQUEST_PAUSE = 0.25

# Filtros de seleccion

MIN_ODD = 1.50

MAX_ODD = 3.50

MIN_PROB = 0.60

MIN_EDGE = 0.025

MIN_EV = 0.03

MIN_TEAM_MATCHES = 4

session = requests.Session()

session.headers.update({

    "x-apisports-key": API_KEY or "",

    "Accept": "application/json",

})

api_calls = 0

daily_remaining = None

def now_local():

    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)

def api_get(endpoint, params=None):

    global api_calls, daily_remaining

    if api_calls >= MAX_API_CALLS:

        raise RuntimeError("Limite de seguridad de llamadas API alcanzado.")

    if daily_remaining is not None and daily_remaining <= SAFETY_REMAINING:

        raise RuntimeError(

            f"Quedan {daily_remaining} llamadas API. "

            "Se detiene para proteger el limite diario."

        )

    try:

        r = session.get(

            BASE_URL + endpoint,

            params=params or {},

            timeout=20

        )

    except requests.RequestException as e:

        raise RuntimeError(f"Error de conexion API-Football: {e}")

    api_calls += 1

    remaining = r.headers.get("x-ratelimit-requests-remaining")

    if remaining is not None:

        try:

            daily_remaining = int(remaining)

        except ValueError:

            pass

    if r.status_code == 429:

        raise RuntimeError("API-Football alcanzo el limite de solicitudes.")

    if r.status_code >= 400:

        raise RuntimeError(f"API-Football HTTP {r.status_code}: {r.text[:200]}")

    try:

        data = r.json()

    except ValueError:

        raise RuntimeError("Respuesta JSON no valida.")

    if data.get("errors"):

        raise RuntimeError(str(data["errors"]))

    time.sleep(REQUEST_PAUSE)

    return data

def telegram(message):

    if not TELEGRAM_TOKEN or not CHAT_ID:

        print("Telegram no configurado.")

        return

    try:

        r = requests.post(

            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",

            json={

                "chat_id": CHAT_ID,

                "text": message,

                "parse_mode": "HTML",

                "disable_web_page_preview": True,

            },

            timeout=15,

        )

        if r.status_code >= 400:

            print("Error Telegram:", r.status_code, r.text[:200])

    except requests.RequestException as e:

        print("Error Telegram:", e)

def safe_float(value):

    try:

        if value is None:

            return None

        return float(str(value).replace("%", "").strip())

    except (TypeError, ValueError):

        return None

def poisson(k, lam):

    if lam <= 0:

        return 1.0 if k == 0 else 0.0

    return math.exp(-lam) * lam ** k / math.factorial(k)

def poisson_probs(home_xg, away_xg, max_goals=10):

    home = [poisson(k, home_xg) for k in range(max_goals + 1)]

    away = [poisson(k, away_xg) for k in range(max_goals + 1)]

    p_home = p_draw = p_away = 0.0

    p_over15 = p_over25 = p_btts = 0.0

    total = 0.0

    for h in range(max_goals + 1):

        for a in range(max_goals + 1):

            p = home[h] * away[a]

            total += p

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

    if total:

        p_home /= total

        p_draw /= total

        p_away /= total

        p_over15 /= total

        p_over25 /= total

        p_btts /= total

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

def fixture_name(f):

    teams = f.get("teams", {})

    home = teams.get("home", {}).get("name", "?")

    away = teams.get("away", {}).get("name", "?")

    return f"{home} vs {away}"

def completed_rows(fixtures, team_id, venue=None):

    rows = []

    for f in fixtures:

        status = f.get("fixture", {}).get("status", {}).get("short")

        if status not in ("FT", "AET", "PEN"):

            continue

        teams = f.get("teams", {})

        home_id = teams.get("home", {}).get("id")

        away_id = teams.get("away", {}).get("id")

        if venue == "home" and home_id != team_id:

            continue

        if venue == "away" and away_id != team_id:

            continue

        goals = f.get("goals", {})

        if home_id == team_id:

            gf, ga = goals.get("home"), goals.get("away")

        elif away_id == team_id:

            gf, ga = goals.get("away"), goals.get("home")

        else:

            continue

        if gf is not None and ga is not None:

            rows.append((int(gf), int(ga)))

    return rows

def mean_goals(rows):

    if not rows:

        return None

    gf = sum(x for x, _ in rows) / len(rows)

    ga = sum(y for _, y in rows) / len(rows)

    return gf, ga

def get_team_rows(team_id, venue):

    data = api_get("/fixtures", {"team": team_id, "last": 10})

    fixtures = data.get("response", [])

    all_rows = completed_rows(fixtures, team_id)

    venue_rows = completed_rows(fixtures, team_id, venue)

    if len(venue_rows) >= MIN_TEAM_MATCHES:

        return venue_rows, "local/visitante"

    if len(all_rows) >= MIN_TEAM_MATCHES:

        return all_rows, "forma general"

    return [], "muestra insuficiente"

def parse_percent(value):

    v = safe_float(value)

    if v is None:

        return None

    return max(0.0, min(1.0, v / 100.0))

def get_api_prediction(fixture_id):

    data = api_get("/predictions", {"fixture": fixture_id})

    response = data.get("response", [])

    if not response:

        return None

    percent = response[0].get("predictions", {}).get("percent", {})

    result = {

        "home": parse_percent(percent.get("home")),

        "draw": parse_percent(percent.get("draw")),

        "away": parse_percent(percent.get("away")),

    }

    if any(v is None for v in result.values()):

        return None

    total = sum(result.values())

    if total <= 0:

        return None

    return {k: v / total for k, v in result.items()}

def model_probabilities(home_rows, away_rows, api_probs=None):

    home_avg = mean_goals(home_rows)

    away_avg = mean_goals(away_rows)

    if home_avg is None or away_avg is None:

        return None, None

    if len(home_rows) < MIN_TEAM_MATCHES:

        return None, None

    if len(away_rows) < MIN_TEAM_MATCHES:

        return None, None

    home_gf, home_ga = home_avg

    away_gf, away_ga = away_avg

    # Estimacion simple: ataque propio y goles concedidos por rival.

    home_xg = max(0.25, min(3.2, 0.55 * home_gf + 0.45 * away_ga))

    away_xg = max(0.20, min(3.0, 0.55 * away_gf + 0.45 * home_ga))

    probs = poisson_probs(home_xg, away_xg)

    # Mezcla conservadora solo para 1X2 si la API aporta datos.

    if api_probs:

        for key in ("home", "draw", "away"):

            probs[key] = 0.70 * probs[key] + 0.30 * api_probs[key]

        total = sum(probs[k] for k in ("home", "draw", "away"))

        if total:

            for key in ("home", "draw", "away"):

                probs[key] /= total

    return probs, (home_xg, away_xg)

def extract_odds(odds_data):

    best = {}

    for item in odds_data.get("response", []):

        for bookmaker in item.get("bookmakers", []):

            for bet in bookmaker.get("bets", []):

                name = str(bet.get("name", "")).strip().lower()

                for value in bet.get("values", []):

                    label = str(value.get("value", "")).strip()

                    odd = safe_float(value.get("odd"))

                    if odd is None or not MIN_ODD <= odd <= MAX_ODD:

                        continue

                    low = label.lower()

                    market = key = None

                    if "match winner" in name or name in ("winner", "1x2"):

                        mapping = {

                            "home": "home",

                            "draw": "draw",

                            "away": "away",

                        }

                        if low in mapping:

                            market = "1X2"

                            key = mapping[low]

                    elif "over/under 1.5" in name or "goals over/under 1.5" in name:

                        if low in ("over 1.5", "under 1.5"):

                            market = "Goles 1.5"

                            key = "over15" if low.startswith("over") else "under15"

                    elif "over/under 2.5" in name or "goals over/under 2.5" in name:

                        if low in ("over 2.5", "under 2.5"):

                            market = "Goles 2.5"

                            key = "over25" if low.startswith("over") else "under25"

                    elif "both teams to score" in name or name == "btts":

                        if low in ("yes", "no"):

                            market = "Ambos anotan"

                            key = "btts" if low == "yes" else "nobtts"

                    if market and key:

                        if key not in best or odd > best[key]["odd"]:

                            best[key] = {

                                "market": market,

                                "key": key,

                                "label": label,

                                "odd": odd,

                                "bookmaker": bookmaker.get("name", "Casa"),

                            }

    return list(best.values())

def main():

    if not API_KEY:

        raise RuntimeError("Falta API_FOOTBALL_KEY.")

    if not TELEGRAM_TOKEN or not CHAT_ID:

        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")

    date = now_local().strftime("%Y-%m-%d")

    print("=" * 60)

    print(f"ESCANER DE FUTBOL — {date}")

    print("=" * 60)

    data = api_get(

        "/fixtures",

        {"date": date, "timezone": "America/Tegucigalpa"},

    )

    fixtures = [

        f for f in data.get("response", [])

        if f.get("fixture", {}).get("status", {}).get("short") in ("NS", "TBD")

        and f.get("fixture", {}).get("id")

        and f.get("teams", {}).get("home", {}).get("id")

        and f.get("teams", {}).get("away", {}).get("id")

    ]

    print(f"Partidos pendientes: {len(fixtures)}")

    if not fixtures:

        telegram(f"📊 <b>ESCANER DE FUTBOL — {date}</b>\n\nNo hay partidos pendientes.")

        return

    # Por el limite gratuito, cada partido necesita varias consultas.

    fixtures = fixtures[:MAX_FIXTURES]

    results = []

    for f in fixtures:

        if api_calls >= MAX_API_CALLS - 3:

            break

        fid = f["fixture"]["id"]

        try:

            odds_data = api_get("/odds", {"fixture": fid})

            markets = extract_odds(odds_data)

            if not markets:

                print("Sin cuotas entre 1.50 y 3.50:", fixture_name(f))

                continue

            api_probs = None

            if api_calls < MAX_API_CALLS - 3:

                try:

                    api_probs = get_api_prediction(fid)

                except RuntimeError as e:

                    print("Prediccion API omitida:", e)

            home_id = f["teams"]["home"]["id"]

            away_id = f["teams"]["away"]["id"]

            if api_calls >= MAX_API_CALLS - 2:

                print("Sin presupuesto para las dos muestras:", fixture_name(f))

                continue

            home_rows, home_source = get_team_rows(home_id, "home")

            if api_calls >= MAX_API_CALLS - 1:

                print("Sin presupuesto para muestra visitante:", fixture_name(f))

                continue

            away_rows, away_source = get_team_rows(away_id, "away")

            probs, xgs = model_probabilities(home_rows, away_rows, api_probs)

            # Si faltan estadisticas, no inventamos probabilidades.

            if probs is None:

                print("Muestra insuficiente:", fixture_name(f))

                continue

            for market in markets:

                prob = probs.get(market["key"])

                if prob is None or prob < MIN_PROB:

                    continue

                implied = 1.0 / market["odd"]

                edge = prob - implied

                ev = prob * market["odd"] - 1.0

                if edge < MIN_EDGE or ev < MIN_EV:

                    continue

                results.append({

                    "fixture": f,

                    "market": market,

                    "prob": prob,

                    "implied": implied,

                    "edge": edge,

                    "ev": ev,

                    "xgs": xgs,

                    "sample": (

                        f"Local: {len(home_rows)}; visitante: {len(away_rows)}"

                    ),

                    "source": f"{home_source}; {away_source}",

                })

        except RuntimeError as e:

            print("Partido omitido:", fixture_name(f), "-", e)

            if daily_remaining is not None and daily_remaining <= SAFETY_REMAINING:

                break

    # Una sola seleccion por partido: no repetir varios mercados del mismo juego.

    results.sort(

        key=lambda x: (x["ev"], x["edge"], x["prob"]),

        reverse=True

    )

    final = []

    seen = set()

    for item in results:

        fid = item["fixture"]["fixture"]["id"]

        if fid in seen:

            continue

        seen.add(fid)

        final.append(item)

        if len(final) >= MAX_ALERTS:

            break

    print("Pronosticos validos:", len(final))

    print("Llamadas API usadas:", api_calls)

    print("Requests restantes:", daily_remaining)

    if not final:

        telegram(

            f"📊 <b>ESCANER DE FUTBOL — {date}</b>\n\n"

            "❌ <b>NO HAY PRONOSTICOS QUE SUPEREN LOS FILTROS.</b>\n\n"

            f"Cuota: {MIN_ODD:.2f}–{MAX_ODD:.2f}\n"

            f"Probabilidad estimada minima: {MIN_PROB:.0%}\n"

            "El bot no fuerza apuestas. Se necesita validar el modelo "

            "con resultados historicos antes de confiar en su rentabilidad."

        )

        return

    lines = [

        f"⚽ <b>ESCANER DE FUTBOL — {date}</b>",

        "",

        f"Mejores pronosticos: <b>{len(final)}</b>",

        "",

    ]

    for i, item in enumerate(final, 1):

        m = item["market"]

        lines.extend([

            f"🏆 <b>#{i} {fixture_name(item['fixture'])}</b>",

            f"🎯 Mercado: <b>{m['market']} — {m['label']}</b>",

            f"💰 Cuota: <b>{m['odd']:.2f}</b>",

            f"🏦 Casa: {m['bookmaker']}",

            f"📈 Probabilidad estimada: <b>{item['prob']:.1%}</b>",

            f"📉 Probabilidad implicita: <b>{item['implied']:.1%}</b>",

            f"🔎 Diferencia estimada: <b>{item['edge']:+.1%}</b>",

            f"💎 EV teorico: <b>{item['ev']:+.1%}</b>",

            f"⚽ Goles esperados: local {item['xgs'][0]:.2f} — visitante {item['xgs'][1]:.2f}",

            f"📊 Muestra: {item['sample']}",

            "",

        ])

    lines.extend([

        "⚠️ <i>Estimaciones del modelo, no garantias. "

        "Las probabilidades aun necesitan validacion historica.</i>",

        f"🔧 Llamadas API usadas: {api_calls}",

    ])

    telegram("\n".join(lines))

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        message = f"🛑 <b>BOT DETENIDO</b>\n\n{e}"

        print(message)

        if TELEGRAM_TOKEN and CHAT_ID:

            telegram(message)

        raise
