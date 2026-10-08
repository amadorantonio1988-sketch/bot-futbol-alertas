import os

import math

import requests

from datetime import datetime

from zoneinfo import ZoneInfo

# ============================================================

# BOT DE APUESTAS DE VALOR

# API-Football + Telegram

# ============================================================

BASE_URL = "https://v3.football.api-sports.io"

TZ = ZoneInfo("America/Tegucigalpa")

API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# ============================================================

# CONFIGURACIÓN

# ============================================================

# VALOR:

# Edge = probabilidad modelo - probabilidad implícita

# EV = (probabilidad modelo * cuota) - 1

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))

MIN_EV = float(os.getenv("MIN_EV", "0.05"))

MIN_PROBABILITY = float(os.getenv("MIN_PROBABILITY", "0.45"))

# Protección del límite de API.

# No vamos a analizar cientos de partidos.

MAX_ODDS_PAGES = 3

MAX_CANDIDATES = 8

MAX_ALERTS = 5

REQUEST_TIMEOUT = 20

session = requests.Session()

session.headers.update({

    "x-apisports-key": API_KEY,

    "Accept": "application/json"

})

# ============================================================

# UTILIDADES

# ============================================================

def log(message):

    print(message, flush=True)

def safe_float(value, default=None):

    try:

        return float(value)

    except (TypeError, ValueError):

        return default

def clamp(value, minimum, maximum):

    return max(minimum, min(maximum, value))

def poisson_pmf(k, lam):

    if lam <= 0:

        return 1.0 if k == 0 else 0.0

    return math.exp(-lam) * lam ** k / math.factorial(k)

def poisson_cdf(k, lam):

    return sum(

        poisson_pmf(i, lam)

        for i in range(k + 1)

    )

def poisson_over(line, lam):

    return 1.0 - poisson_cdf(

        int(line),

        lam

    )

def poisson_under(line, lam):

    return poisson_cdf(

        int(line),

        lam

    )

# ============================================================

# API

# ============================================================

class APIError(Exception):

    pass

def api_get(endpoint, params=None):

    if not API_KEY:

        raise APIError(

            "Falta API_FOOTBALL_KEY."

        )

    try:

        response = session.get(

            BASE_URL + endpoint,

            params=params or {},

            timeout=REQUEST_TIMEOUT

        )

    except requests.RequestException as error:

        raise APIError(

            f"Error de conexión: {error}"

        )

    try:

        data = response.json()

    except ValueError:

        raise APIError(

            f"Respuesta inválida de API-Football. "

            f"HTTP {response.status_code}"

        )

    errors = data.get("errors") or {}

    if response.status_code >= 400:

        raise APIError(str(errors))

    if errors:

        raise APIError(str(errors))

    remaining = response.headers.get(

        "x-ratelimit-requests-remaining"

    )

    if remaining:

        log(

            f"   Requests restantes: {remaining}"

        )

    return data

# ============================================================

# PARTIDOS

# ============================================================

def get_fixtures(date):

    data = api_get(

        "/fixtures",

        {

            "date": date,

            "timezone": "America/Tegucigalpa"

        }

    )

    return data.get("response", [])

# ============================================================

# CUOTAS

# ============================================================

def get_odds(date, page):

    data = api_get(

        "/odds",

        {

            "date": date,

            "page": page

        }

    )

    return (

        data.get("response", []),

        data.get("paging", {})

    )

def normalize(text):

    return " ".join(

        str(text or "").lower().split()

    )

def extract_best_odds(rows):

    best = {}

    for bookmaker in rows:

        bookmaker_name = (

            bookmaker

            .get("bookmaker", {})

            .get("name", "Bookmaker")

        )

        for bet in bookmaker.get("bets", []):

            market = bet.get("name", "")

            for value in bet.get("values", []):

                selection = str(

                    value.get("value", "")

                ).strip()

                odd = safe_float(

                    value.get("odd")

                )

                if odd is None:

                    continue

                if odd <= 1.01:

                    continue

                if odd > 30:

                    continue

                key = (

                    normalize(market),

                    selection.lower()

                )

                if (

                    key not in best

                    or odd > best[key]["odd"]

                ):

                    best[key] = {

                        "market": market,

                        "selection": selection,

                        "odd": odd,

                        "bookmaker": bookmaker_name

                    }

    return list(best.values())

# ============================================================

# ÚLTIMOS PARTIDOS

# ============================================================

def get_last_team(team_id, last=5):

    data = api_get(

        "/fixtures",

        {

            "team": team_id,

            "last": last,

            "timezone": "America/Tegucigalpa"

        }

    )

    return data.get("response", [])

def get_goals_for_against(

    fixture,

    team_id

):

    home = fixture.get(

        "teams",

        {}

    ).get(

        "home",

        {}

    )

    away = fixture.get(

        "teams",

        {}

    ).get(

        "away",

        {}

    )

    goals = fixture.get(

        "goals",

        {}

    )

    home_goals = goals.get("home")

    away_goals = goals.get("away")

    if (

        home_goals is None

        or away_goals is None

    ):

        return None

    if home.get("id") == team_id:

        return (

            int(home_goals),

            int(away_goals)

        )

    if away.get("id") == team_id:

        return (

            int(away_goals),

            int(home_goals)

        )

    return None

def form_stats(

    fixtures,

    team_id,

    venue=None

):

    results = []

    for fixture in fixtures:

        home_id = (

            fixture

            .get("teams", {})

            .get("home", {})

            .get("id")

        )

        away_id = (

            fixture

            .get("teams", {})

            .get("away", {})

            .get("id")

        )

        if team_id not in (

            home_id,

            away_id

        ):

            continue

        if (

            venue == "home"

            and home_id != team_id

        ):

            continue

        if (

            venue == "away"

            and away_id != team_id

        ):

            continue

        result = get_goals_for_against(

            fixture,

            team_id

        )

        if result:

            results.append(result)

    if not results:

        return {

            "n": 0,

            "gf": 1.25,

            "ga": 1.25,

            "points": 0.50,

            "wins": 0

        }

    goals_for = sum(

        x[0] for x in results

    )

    goals_against = sum(

        x[1] for x in results

    )

    gf = goals_for / len(results)

    ga = goals_against / len(results)

    points = 0

    wins = 0

    for scored, conceded in results:

        if scored > conceded:

            points += 3

            wins += 1

        elif scored == conceded:

            points += 1

    return {

        "n": len(results),

        "gf": gf,

        "ga": ga,

        "points": points / (

            3 * len(results)

        ),

        "wins": wins

    }

# ============================================================

# MODELO DE GOLES

# ============================================================

def expected_goals(

    home,

    away

):

    # Ataque propio + defensa rival.

    home_xg = (

        home["gf"] +

        away["ga"]

    ) / 2

    away_xg = (

        away["gf"] +

        home["ga"]

    ) / 2

    # Suavizado para evitar

    # extremos por muestras pequeñas.

    home_xg = (

        0.75 * home_xg +

        0.25 * 1.35

    )

    away_xg = (

        0.75 * away_xg +

        0.25 * 1.10

    )

    # La forma influye, pero poco.

    home_xg *= (

        0.92 +

        home["points"] * 0.16

    )

    away_xg *= (

        0.92 +

        away["points"] * 0.16

    )

    return (

        clamp(home_xg, 0.20, 4.50),

        clamp(away_xg, 0.15, 4.00)

    )

def calculate_model(

    home_xg,

    away_xg

):

    home_win = 0.0

    draw = 0.0

    away_win = 0.0

    # Distribución Poisson.

    for home_goals in range(9):

        for away_goals in range(9):

            probability = (

                poisson_pmf(

                    home_goals,

                    home_xg

                )

                *

                poisson_pmf(

                    away_goals,

                    away_xg

                )

            )

            if home_goals > away_goals:

                home_win += probability

            elif home_goals == away_goals:

                draw += probability

            else:

                away_win += probability

    total = (

        home_win +

        draw +

        away_win

    )

    home_win /= total

    draw /= total

    away_win /= total

    total_goals = (

        home_xg +

        away_xg

    )

    over15 = poisson_over(

        1.5,

        total_goals

    )

    over25 = poisson_over(

        2.5,

        total_goals

    )

    btts_yes = (

        (1 - math.exp(-home_xg))

        *

        (1 - math.exp(-away_xg))

    )

    return {

        "home": home_win,

        "draw": draw,

        "away": away_win,

        "over1.5": over15,

        "under1.5": 1 - over15,

        "over2.5": over25,

        "under2.5": 1 - over25,

        "btts_yes": btts_yes,

        "btts_no": 1 - btts_yes,

        "home_xg": home_xg,

        "away_xg": away_xg

    }

# ============================================================

# CONVERTIR MERCADO -> PROBABILIDAD

# ============================================================

def market_probability(

    item,

    probabilities

):

    market = normalize(

        item["market"]

    )

    selection = normalize(

        item["selection"]

    ).replace(" ", "")

    # --------------------------------------------------------

    # 1X2

    # --------------------------------------------------------

    if (

        "match winner" in market

        or market == "winner"

        or market == "1x2"

        or market == "fulltime result"

    ):

        if selection in (

            "home",

            "1"

        ):

            return probabilities["home"]

        if selection in (

            "draw",

            "x"

        ):

            return probabilities["draw"]

        if selection in (

            "away",

            "2"

        ):

            return probabilities["away"]

    # --------------------------------------------------------

    # DOBLE OPORTUNIDAD

    # --------------------------------------------------------

    if "double chance" in market:

        if selection in (

            "home/draw",

            "1x"

        ):

            return (

                probabilities["home"] +

                probabilities["draw"]

            )

        if selection in (

            "draw/away",

            "x2"

        ):

            return (

                probabilities["draw"] +

                probabilities["away"]

            )

        if selection in (

            "home/away",

            "12"

        ):

            return (

                probabilities["home"] +

                probabilities["away"]

            )

    # --------------------------------------------------------

    # OVER / UNDER

    # --------------------------------------------------------

    if (

        "over/under" in market

        or "goals over/under" in market

    ):

        if selection.startswith("over"):

            try:

                line = float(

                    selection.replace(

                        "over",

                        ""

                    )

                )

                return poisson_over(

                    line,

                    probabilities["home_xg"]

                    +

                    probabilities["away_xg"]

                )

            except ValueError:

                pass

        if selection.startswith("under"):

            try:

                line = float(

                    selection.replace(

                        "under",

                        ""

                    )

                )

                return poisson_under(

                    line,

                    probabilities["home_xg"]

                    +

                    probabilities["away_xg"]

                )

            except ValueError:

                pass

    # --------------------------------------------------------

    # BTTS

    # --------------------------------------------------------

    if (

        "both teams score" in market

        or

        "both teams to score" in market

        or

        market == "btts"

    ):

        if selection in (

            "yes",

            "y"

        ):

            return probabilities["btts_yes"]

        if selection in (

            "no",

            "n"

        ):

            return probabilities["btts_no"]

    return None

# ============================================================

# DETECTAR VALOR

# ============================================================

def find_value(

    odds,

    probabilities

):

    values = []

    for item in odds:

        probability = market_probability(

            item,

            probabilities

        )

        if probability is None:

            continue

        odd = item["odd"]

        # Probabilidad implícita.

        implied = 1 / odd

        # Edge.

        edge = (

            probability -

            implied

        )

        # Valor esperado.

        ev = (

            probability *

            odd

        ) - 1

        # Filtros de valor.

        if probability < MIN_PROBABILITY:

            continue

        if edge < MIN_EDGE:

            continue

        if ev < MIN_EV:

            continue

        values.append({

            **item,

            "probability":

                probability,

            "implied":

                implied,

            "edge":

                edge,

            "ev":

                ev

        })

    values.sort(

        key=lambda x: (

            x["edge"],

            x["ev"],

            x["probability"]

        ),

        reverse=True

    )

    return values

# ============================================================

# TELEGRAM

# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN:

        raise RuntimeError(

            "Falta TELEGRAM_BOT_TOKEN."

        )

    if not CHAT_ID:

        raise RuntimeError(

            "Falta TELEGRAM_CHAT_ID."

        )

    url = (

        f"https://api.telegram.org/"

        f"bot{TELEGRAM_TOKEN}/sendMessage"

    )

    response = requests.post(

        url,

        json={

            "chat_id": CHAT_ID,

            "text": message,

            "parse_mode": "HTML",

            "disable_web_page_preview": True

        },

        timeout=20

    )

    if response.status_code >= 400:

        raise RuntimeError(

            response.text

        )

def value_level(edge):

    if edge >= 0.12:

        return "🔥 MUY ALTO"

    if edge >= 0.08:

        return "🟢 ALTO"

    return "🟡 VALOR"

def explanation(

    home,

    away

):

    reasons = []

    if home["gf"] >= 1.50:

        reasons.append(

            "buen ataque local"

        )

    if away["gf"] >= 1.40:

        reasons.append(

            "buen ataque visitante"

        )

    if home["ga"] >= 1.50:

        reasons.append(

            "defensa local concede goles"

        )

    if away["ga"] >= 1.50:

        reasons.append(

            "defensa visitante concede goles"

        )

    if not reasons:

        reasons.append(

            "el modelo detecta una diferencia "

            "favorable entre probabilidad y precio"

        )

    return ", ".join(

        reasons[:3]

    )

def format_alert(

    number,

    pick,

    fixture,

    home_stats,

    away_stats

):

    home = (

        fixture["teams"]

        ["home"]

        ["name"]

    )

    away = (

        fixture["teams"]

        ["away"]

        ["name"]

    )

    kickoff = (

        fixture["fixture"]

        .get("date", "")

    )

    try:

        dt = datetime.fromisoformat(

            kickoff.replace(

                "Z",

                "+00:00"

            )

        ).astimezone(TZ)

        time_text = dt.strftime(

            "%H:%M"

        )

    except Exception:

        time_text = "--:--"

    return (

        f"<b>💎 APUESTA DE VALOR #{number}</b>\n\n"

        f"⚽ <b>{home} vs {away}</b>\n"

        f"🕐 {time_text} Honduras\n\n"

        f"🎯 <b>{pick['market']}: "

        f"{pick['selection']}</b>\n"

        f"💰 Cuota: "

        f"<b>{pick['odd']:.2f}</b>\n"

        f"📊 Probabilidad modelo: "

        f"<b>{pick['probability']*100:.1f}%</b>\n"

        f"📉 Probabilidad implícita: "

        f"{pick['implied']*100:.1f}%\n"

        f"📈 Edge: "

        f"<b>+{pick['edge']*100:.1f}%</b>\n"

        f"💵 EV: "

        f"<b>+{pick['ev']*100:.1f}%</b>\n"

        f"⭐ Valor: "

        f"<b>{value_level(pick['edge'])}</b>\n\n"

        f"📝 "

        f"{explanation(home_stats, away_stats)}\n\n"

        f"📊 Últimos partidos analizados:\n"

        f"• {home}: "

        f"{home_stats['gf']:.2f} GF / "

        f"{home_stats['ga']:.2f} GC\n"

        f"• {away}: "

        f"{away_stats['gf']:.2f} GF / "

        f"{away_stats['ga']:.2f} GC"

    )

# ============================================================

# MAIN

# ============================================================

def main():

    if not API_KEY:

        raise RuntimeError(

            "No existe API_FOOTBALL_KEY."

        )

    if not TELEGRAM_TOKEN:

        raise RuntimeError(

            "No existe TELEGRAM_BOT_TOKEN."

        )

    if not CHAT_ID:

        raise RuntimeError(

            "No existe TELEGRAM_CHAT_ID."

        )

    today = datetime.now(

        TZ

    ).strftime(

        "%Y-%m-%d"

    )

    log("=" * 65)

    log(

        f"💎 ESCÁNER DE VALOR — {today}"

    )

    log("=" * 65)

    # ========================================================

    # 1. PARTIDOS

    # ========================================================

    fixtures = get_fixtures(

        today

    )

    upcoming = []

    for fixture in fixtures:

        status = (

            fixture

            .get("fixture", {})

            .get("status", {})

            .get("short")

        )

        if status in (

            "NS",

            "TBD"

        ):

            upcoming.append(

                fixture

            )

    log(

        f"Partidos próximos: "

        f"{len(upcoming)}"

    )

    if not upcoming:

        send_telegram(

            f"💎 <b>ESCÁNER DE VALOR — "

            f"{today}</b>\n\n"

            f"🚫 No hay partidos "

            f"próximos disponibles."

        )

        return

    fixture_map = {

        f["fixture"]["id"]: f

        for f in upcoming

    }

    # ========================================================

    # 2. CUOTAS

    # ========================================================

    odds_by_fixture = {}

    for page in range(

        1,

        MAX_ODDS_PAGES + 1

    ):

        log(

            f"Consultando cuotas "

            f"{page}/{MAX_ODDS_PAGES}"

        )

        rows, paging = get_odds(

            today,

            page

        )

        for row in rows:

            fixture_id = (

                row

                .get("fixture", {})

                .get("id")

            )

            if fixture_id in fixture_map:

                odds_by_fixture.setdefault(

                    fixture_id,

                    []

                ).append(row)

        current = safe_float(

            paging.get(

                "current"

            ),

            page

        )

        total = safe_float(

            paging.get(

                "total"

            ),

            page

        )

        if current >= total:

            break

    log(

        f"Partidos con cuotas: "

        f"{len(odds_by_fixture)}"

    )

    # ========================================================

    # 3. PRESELECCIÓN

    # ========================================================

    candidates = []

    for fixture_id, rows in (

        odds_by_fixture.items()

    ):

        odds = extract_best_odds(

            rows

        )

        if not odds:

            continue

        candidates.append({

            "fixture":

                fixture_map[fixture_id],

            "fixture_id":

                fixture_id,

            "odds":

                odds

        })

    # Los partidos con mayor variedad

    # de mercados disponibles tienen

    # prioridad.

    candidates.sort(

        key=lambda x:

        len(x["odds"]),

        reverse=True

    )

    candidates = candidates[

        :MAX_CANDIDATES

    ]

    log(

        f"Análisis profundo: "

        f"{len(candidates)} partidos"

    )

    # ========================================================

    # 4. ANÁLISIS PROFUNDO

    # ========================================================

    all_values = []

    for index, candidate in enumerate(

        candidates,

        1

    ):

        fixture = candidate[

            "fixture"

        ]

        home = fixture[

            "teams"

        ][

            "home"

        ]

        away = fixture[

            "teams"

        ][

            "away"

        ]

        log(

            f"[{index}/"

            f"{len(candidates)}] "

            f"{home['name']} vs "

            f"{away['name']}"

        )

        try:

            # Solo 2 llamadas de estadísticas

            # por candidato.

            home_last = get_last_team(

                home["id"],

                5

            )

            away_last = get_last_team(

                away["id"],

                5

            )

            # Local del local.

            home_stats = form_stats(

                home_last,

                home["id"],

                "home"

            )

            # Visitante del visitante.

            away_stats = form_stats(

                away_last,

                away["id"],

                "away"

            )

            # Si la muestra local/visitante

            # es demasiado pequeña,

            # utilizamos los últimos 5 generales.

            if home_stats["n"] < 2:

                home_stats = form_stats(

                    home_last,

                    home["id"],

                    None

                )

            if away_stats["n"] < 2:

                away_stats = form_stats(

                    away_last,

                    away["id"],

                    None

                )

            # xG simplificado.

            home_xg, away_xg = (

                expected_goals(

                    home_stats,

                    away_stats

                )

            )

            probabilities = (

                calculate_model(

                    home_xg,

                    away_xg

                )

            )

            # Buscar VALUE.

            values = find_value(

                candidate["odds"],

                probabilities

            )

            for pick in values:

                pick["fixture"] = fixture

                pick["home_stats"] = (

                    home_stats

                )

                pick["away_stats"] = (

                    away_stats

                )

            all_values.extend(

                values

            )

            log(

                f"   xG: "

                f"{home_xg:.2f} - "

                f"{away_xg:.2f}"

            )

            log(

                f"   Valor encontrado: "

                f"{len(values)}"

            )

        except APIError as error:

            log(

                f"   Partido omitido: "

                f"{error}"

            )

    # ========================================================

    # 5. RANKING

    # ========================================================

    all_values.sort(

        key=lambda x: (

            x["edge"],

            x["ev"],

            x["probability"]

        ),

        reverse=True

    )

    final = []

    seen = set()

    for pick in all_values:

        key = (

            pick["fixture"]

            ["fixture"]

            ["id"],

            normalize(

                pick["market"]

            ),

            pick["selection"].lower()

        )

        if key in seen:

            continue

        seen.add(key)

        final.append(

            pick

        )

        if len(final) >= MAX_ALERTS:

            break

    log(

        f"MEJORES APUESTAS DE VALOR: "

        f"{len(final)}"

    )

    # ========================================================

    # 6. SI NO HAY VALUE

    # ========================================================

    if not final:

        send_telegram(

            f"💎 <b>ESCÁNER DE VALOR — "

            f"{today}</b>\n\n"

            f"🚫 <b>NO HAY APUESTAS "

            f"DE VALOR HOY</b>\n\n"

            f"El modelo no encontró "

            f"una diferencia suficiente "

            f"entre probabilidad y precio.\n\n"

            f"✅ No se fuerza ningún pick."

        )

        log(

            "No hay valor. "

            "Telegram notificado."

        )

        return

    # ========================================================

    # 7. ENVIAR ALERTAS

    # ========================================================

    send_telegram(

        f"💎 <b>MEJORES APUESTAS "

        f"DE VALOR — {today}</b>\n\n"

        f"Encontradas: "

        f"<b>{len(final)}</b>\n"

        f"Ordenadas por Edge y EV.\n\n"

        f"⚠️ Valor estadístico, "

        f"no garantía de acierto."

    )

    for number, pick in enumerate(

        final,

        1

    ):

        message = format_alert(

            number,

            pick,

            pick["fixture"],

            pick["home_stats"],

            pick["away_stats"]

        )

        send_telegram(

            message

        )

    log(

        "✅ Alertas enviadas."

    )

# ============================================================

# EJECUCIÓN

# ============================================================

if __name__ == "__main__":

    try:

        main()

    except APIError as error:

        log(

            f"🛑 BOT DETENIDO: "

            f"API error: {error}"

        )

        if (

            TELEGRAM_TOKEN

            and CHAT_ID

        ):

            try:

                send_telegram(

                    "🛑 <b>BOT DE VALOR "

                    "DETENIDO</b>\n\n"

                    f"<code>"

                    f"{str(error)[:900]}"

                    f"</code>"

                )

            except Exception:

                pass

        raise SystemExit(1)

    except Exception as error:

        log(

            f"🛑 ERROR: {error}"

        )

        if (

            TELEGRAM_TOKEN

            and CHAT_ID

        ):

            try:

                send_telegram(

                    "🛑 <b>ERROR EN "

                    "BOT DE VALOR</b>\n\n"

                    f"<code>"

                    f"{str(error)[:900]}"

                    f"</code>"

                )

            except Exception:

                pass

        raise SystemExit(1)
