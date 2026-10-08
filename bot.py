import os

import time

import requests

from datetime import datetime

# ============================================================

# CONFIGURACIÓN

# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

MIN_ODD = 1.50

# Calidad mínima para considerar una oportunidad.

# 10 = señal muy fuerte.

MIN_QUALITY = 7.0

# Máximo de alertas que puede enviar.

# NO significa que tenga que enviar 5.

MAX_ALERTS = 5

REQUEST_DELAY = 0.15

TIMEOUT = 20

session = requests.Session()

session.headers.update({

    "x-apisports-key": API_KEY or "",

    "Accept": "application/json"

})

cache = {}

# ============================================================

# API-FOOTBALL

# ============================================================

def api_get(endpoint, params=None):

    params = params or {}

    cache_key = (

        endpoint,

        tuple(sorted(params.items()))

    )

    if cache_key in cache:

        return cache[cache_key]

    for attempt in range(3):

        try:

            response = session.get(

                BASE_URL + endpoint,

                params=params,

                timeout=TIMEOUT

            )

            if response.status_code == 200:

                data = response.json()

                if data.get("errors"):

                    print(

                        f"Error API {endpoint}: "

                        f"{data.get('errors')}"

                    )

                    return {}

                cache[cache_key] = data

                time.sleep(REQUEST_DELAY)

                return data

            print(

                f"HTTP {response.status_code} "

                f"en {endpoint}"

            )

        except requests.RequestException as error:

            print(

                f"Error de conexión: {error}"

            )

            time.sleep(

                1.5 * (attempt + 1)

            )

    return {}

def get_all_pages(endpoint, params):

    first = api_get(

        endpoint,

        params

    )

    if not first:

        return []

    results = list(

        first.get("response", [])

    )

    paging = first.get(

        "paging",

        {}

    ) or {}

    total_pages = int(

        paging.get("total", 1) or 1

    )

    for page in range(2, total_pages + 1):

        page_params = dict(params)

        page_params["page"] = page

        data = api_get(

            endpoint,

            page_params

        )

        results.extend(

            data.get("response", [])

        )

    return results

# ============================================================

# TELEGRAM

# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN:

        print(

            "ERROR: falta "

            "TELEGRAM_BOT_TOKEN"

        )

        return False

    if not CHAT_ID:

        print(

            "ERROR: falta "

            "TELEGRAM_CHAT_ID"

        )

        return False

    url = (

        f"https://api.telegram.org/"

        f"bot{TELEGRAM_TOKEN}/sendMessage"

    )

    try:

        response = requests.post(

            url,

            json={

                "chat_id": CHAT_ID,

                "text": message,

                "parse_mode": "HTML",

                "disable_web_page_preview": True

            },

            timeout=TIMEOUT

        )

        if response.status_code == 200:

            return True

        print(

            "Error Telegram:",

            response.text

        )

    except requests.RequestException as error:

        print(

            "Error enviando Telegram:",

            error

        )

    return False

# ============================================================

# UTILIDADES

# ============================================================

def today():

    return datetime.now().strftime(

        "%Y-%m-%d"

    )

def number(value):

    try:

        return float(value)

    except (

        TypeError,

        ValueError

    ):

        return None

def normalize(text):

    return (

        str(text or "")

        .lower()

        .strip()

        .replace(" ", "")

        .replace("-", "")

        .replace("_", "")

    )

def team_names(fixture):

    home = (

        fixture

        .get("teams", {})

        .get("home", {})

        .get("name", "?")

    )

    away = (

        fixture

        .get("teams", {})

        .get("away", {})

        .get("name", "?")

    )

    return home, away

# ============================================================

# ODDS

# ============================================================

def get_odds(fixture_id):

    data = api_get(

        "/odds",

        {

            "fixture": fixture_id

        }

    )

    response = data.get(

        "response",

        []

    )

    markets = []

    for bookmaker in response:

        bookmaker_name = (

            bookmaker

            .get("bookmaker", {})

            .get("name", "")

        )

        for bet in bookmaker.get(

            "bets",

            []

        ):

            bet_name = str(

                bet.get("name", "")

            )

            # ------------------------------------------------

            # TARJETAS EXCLUIDAS

            # ------------------------------------------------

            normalized_bet = normalize(

                bet_name

            )

            if any(

                word in normalized_bet

                for word in [

                    "card",

                    "cards",

                    "yellowcard",

                    "redcard",

                    "tarjeta",

                    "tarjetas"

                ]

            ):

                continue

            market_type = identify_market(

                bet_name

            )

            if not market_type:

                continue

            for value in bet.get(

                "values",

                []

            ):

                odd = number(

                    value.get("odd")

                )

                if odd is None:

                    continue

                if odd < MIN_ODD:

                    continue

                label = str(

                    value.get(

                        "value",

                        ""

                    )

                ).strip()

                markets.append({

                    "type": market_type,

                    "name": bet_name,

                    "label": label,

                    "odd": odd,

                    "bookmaker": bookmaker_name

                })

    return markets

def identify_market(bet_name):

    b = normalize(

        bet_name

    )

    # --------------------------------------------------------

    # BTTS

    # --------------------------------------------------------

    if (

        "bothteamscores" in b

        or "btts" in b

    ):

        return "btts"

    # --------------------------------------------------------

    # GOLES OVER / UNDER

    # --------------------------------------------------------

    if (

        "goalsoverunder" in b

        or "overunder" in b

        or "totalgoals" in b

    ):

        return "goals"

    # --------------------------------------------------------

    # GANADOR

    # --------------------------------------------------------

    if (

        "matchwinner" in b

        or b == "winner"

        or "fulltimeresult" in b

    ):

        return "winner"

    # --------------------------------------------------------

    # DOBLE OPORTUNIDAD

    # --------------------------------------------------------

    if "doublechance" in b:

        return "double_chance"

    # --------------------------------------------------------

    # DNB

    # --------------------------------------------------------

    if (

        "drawnobet" in b

        or "dnb" in b

    ):

        return "dnb"

    # --------------------------------------------------------

    # HANDICAP

    # --------------------------------------------------------

    if "handicap" in b:

        return "handicap"

    # --------------------------------------------------------

    # GOLES DE EQUIPO

    # --------------------------------------------------------

    if (

        "teamtotal" in b

        or "teamgoals" in b

    ):

        return "team_goals"

    # --------------------------------------------------------

    # CORNERS

    # --------------------------------------------------------

    if "corner" in b:

        return "corners"

    return None

# ============================================================

# PREDICCIONES API-FOOTBALL

# ============================================================

def get_prediction(fixture_id):

    data = api_get(

        "/predictions",

        {

            "fixture": fixture_id

        }

    )

    response = data.get(

        "response",

        []

    )

    if not response:

        return None

    return response[0]

def prediction_info(prediction):

    if not prediction:

        return {}

    predictions = (

        prediction

        .get("predictions", {})

        or {}

    )

    percentages = (

        predictions

        .get("percent", {})

        or {}

    )

    goals = (

        predictions

        .get("goals", {})

        or {}

    )

    def percentage(key):

        value = percentages.get(key)

        if value is None:

            return None

        try:

            return float(

                str(value)

                .replace("%", "")

            )

        except ValueError:

            return None

    return {

        "home_probability":

            percentage("home"),

        "draw_probability":

            percentage("draw"),

        "away_probability":

            percentage("away"),

        "predicted_home_goals":

            number(

                goals.get("home")

            ),

        "predicted_away_goals":

            number(

                goals.get("away")

            ),

        "under_over":

            str(

                predictions.get(

                    "under_over",

                    ""

                )

            ),

        "advice":

            str(

                predictions.get(

                    "advice",

                    ""

                )

            )

    }

# ============================================================

# ÚLTIMOS 5 PARTIDOS

# ============================================================

def get_recent_form(

    team_id,

    date

):

    if not team_id:

        return None

    fixtures = get_all_pages(

        "/fixtures",

        {

            "team": team_id,

            "to": date,

            "status": "FT"

        }

    )

    fixtures = sorted(

        fixtures,

        key=lambda x:

            x.get(

                "fixture",

                {}

            ).get(

                "date",

                ""

            ),

        reverse=True

    )

    fixtures = fixtures[:5]

    if not fixtures:

        return None

    goals_scored = 0

    goals_conceded = 0

    total_goals = 0

    over25 = 0

    btts = 0

    valid_matches = 0

    for fixture in fixtures:

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

        home_goals = number(

            fixture

            .get("goals", {})

            .get("home")

        )

        away_goals = number(

            fixture

            .get("goals", {})

            .get("away")

        )

        if (

            home_goals is None

            or away_goals is None

        ):

            continue

        valid_matches += 1

        if home.get("id") == team_id:

            scored = home_goals

            conceded = away_goals

        else:

            scored = away_goals

            conceded = home_goals

        goals_scored += scored

        goals_conceded += conceded

        total = (

            home_goals

            + away_goals

        )

        total_goals += total

        if total >= 3:

            over25 += 1

        if (

            home_goals >= 1

            and away_goals >= 1

        ):

            btts += 1

    if valid_matches == 0:

        return None

    return {

        "matches":

            valid_matches,

        "scored_avg":

            goals_scored /

            valid_matches,

        "conceded_avg":

            goals_conceded /

            valid_matches,

        "total_avg":

            total_goals /

            valid_matches,

        "over25_rate":

            over25 /

            valid_matches,

        "btts_rate":

            btts /

            valid_matches

    }

# ============================================================

# ANÁLISIS DE GOLES

# ============================================================

def analyze_goals(

    market,

    prediction,

    home_form,

    away_form

):

    label = market["label"].lower()

    if "over" not in label:

        return None

    line_text = (

        label

        .replace("over", "")

        .strip()

    )

    line = number(

        line_text

    )

    if line is None:

        return None

    score = 0

    reasons = []

    predicted_home = (

        prediction

        .get(

            "predicted_home_goals"

        )

        or 0

    )

    predicted_away = (

        prediction

        .get(

            "predicted_away_goals"

        )

        or 0

    )

    predicted_total = (

        predicted_home

        + predicted_away

    )

    # --------------------------------------------------------

    # PROYECCIÓN DE GOLES

    # --------------------------------------------------------

    if (

        line <= 1.5

        and predicted_total >= 2.5

    ):

        score += 2

        reasons.append(

            "la proyección de goles "

            "es favorable al Over 1.5"

        )

    if (

        line <= 2.5

        and predicted_total >= 3.0

    ):

        score += 2

        reasons.append(

            "la proyección apunta "

            "a 3 o más goles"

        )

    # --------------------------------------------------------

    # FORMA RECIENTE

    # --------------------------------------------------------

    if home_form and away_form:

        combined_total = (

            home_form["total_avg"]

            + away_form["total_avg"]

        )

        if combined_total >= 3.2:

            score += 2

            reasons.append(

                f"ambos perfiles suman "

                f"{combined_total:.1f} goles "

                f"por partido"

            )

        if combined_total >= 3.6:

            score += 1

        over_rate = (

            home_form["over25_rate"]

            + away_form["over25_rate"]

        ) / 2

        if (

            line <= 2.5

            and over_rate >= 0.60

        ):

            score += 1.5

            reasons.append(

                f"Over 2.5 en "

                f"{over_rate * 100:.0f}% "

                f"de sus últimos partidos"

            )

        if (

            home_form["conceded_avg"] >= 1.4

            and

            away_form["conceded_avg"] >= 1.4

        ):

            score += 1

            reasons.append(

                "ambos equipos vienen "

                "concediendo goles"

            )

    # --------------------------------------------------------

    # PREDICCIÓN DE API

    # --------------------------------------------------------

    api_under_over = normalize(

        prediction.get(

            "under_over",

            ""

        )

    )

    if (

        line <= 2.5

        and "over2.5" in api_under_over

    ):

        score += 1.5

        reasons.append(

            "la predicción de API-Football "

            "también apunta al Over 2.5"

        )

    if score < MIN_QUALITY:

        return None

    return score, reasons

# ============================================================

# ANÁLISIS BTTS

# ============================================================

def analyze_btts(

    market,

    prediction,

    home_form,

    away_form

):

    if market["label"].lower() != "yes":

        return None

    score = 0

    reasons = []

    home_goals = (

        prediction

        .get(

            "predicted_home_goals"

        )

        or 0

    )

    away_goals = (

        prediction

        .get(

            "predicted_away_goals"

        )

        or 0

    )

    if (

        home_goals >= 1

        and away_goals >= 1

    ):

        score += 3

        reasons.append(

            "la proyección da gol "

            "a ambos equipos"

        )

    if home_form and away_form:

        if (

            home_form["scored_avg"] >= 1.2

            and

            away_form["scored_avg"] >= 1.2

        ):

            score += 2

            reasons.append(

                "ambos equipos tienen "

                "buen promedio goleador"

            )

        btts_rate = (

            home_form["btts_rate"]

            + away_form["btts_rate"]

        ) / 2

        if btts_rate >= 0.60:

            score += 2

            reasons.append(

                f"BTTS en "

                f"{btts_rate * 100:.0f}% "

                f"de sus últimos partidos"

            )

        if (

            home_form["conceded_avg"] >= 1.2

            and

            away_form["conceded_avg"] >= 1.2

        ):

            score += 1

            reasons.append(

                "ambas defensas "

                "conceden con frecuencia"

            )

    if score < MIN_QUALITY:

        return None

    return score, reasons

# ============================================================

# GANADOR / DOBLE OPORTUNIDAD / DNB

# ============================================================

def analyze_result(

    market,

    prediction,

    home,

    away

):

    market_type = market["type"]

    home_probability = prediction.get(

        "home_probability"

    )

    draw_probability = prediction.get(

        "draw_probability"

    )

    away_probability = prediction.get(

        "away_probability"

    )

    score = 0

    reasons = []

    label = market["label"].lower()

    # --------------------------------------------------------

    # GANADOR

    # --------------------------------------------------------

    if market_type == "winner":

        if (

            label == "home"

            and

            home_probability is not None

            and

            home_probability >= 65

        ):

            score += 7

            reasons.append(

                f"{home} tiene "

                f"{home_probability:.0f}% "

                f"de probabilidad proyectada"

            )

        elif (

            label == "away"

            and

            away_probability is not None

            and

            away_probability >= 65

        ):

            score += 7

            reasons.append(

                f"{away} tiene "

                f"{away_probability:.0f}% "

                f"de probabilidad proyectada"

            )

        else:

            return None

    # --------------------------------------------------------

    # DOBLE OPORTUNIDAD

    # --------------------------------------------------------

    elif market_type == "double_chance":

        if label in (

            "home/draw",

            "1x",

            "home or draw"

        ):

            if (

                home_probability is not None

                and

                draw_probability is not None

                and

                home_probability

                + draw_probability >= 72

            ):

                score += 7

                reasons.append(

                    f"{home} o empate "

                    f"suman "

                    f"{home_probability + draw_probability:.0f}%"

                )

            else:

                return None

        elif label in (

            "draw/away",

            "x2",

            "away or draw"

        ):

            if (

                away_probability is not None

                and

                draw_probability is not None

                and

                away_probability

                + draw_probability >= 72

            ):

                score += 7

                reasons.append(

                    f"{away} o empate "

                    f"suman "

                    f"{away_probability + draw_probability:.0f}%"

                )

            else:

                return None

        elif label in (

            "home/away",

            "12"

        ):

            if (

                home_probability is not None

                and

                away_probability is not None

                and

                home_probability

                + away_probability >= 78

            ):

                score += 7

                reasons.append(

                    "evitar el empate tiene "

                    f"{home_probability + away_probability:.0f}% "

                    "combinado"

                )

            else:

                return None

        else:

            return None

    # --------------------------------------------------------

    # DNB

    # --------------------------------------------------------

    elif market_type == "dnb":

        if (

            label == "home"

            and

            home_probability is not None

            and

            home_probability >= 62

        ):

            score += 7

            reasons.append(

                f"{home} tiene "

                f"{home_probability:.0f}% "

                f"de victoria proyectada"

            )

        elif (

            label == "away"

            and

            away_probability is not None

            and

            away_probability >= 62

        ):

            score += 7

            reasons.append(

                f"{away} tiene "

                f"{away_probability:.0f}% "

                f"de victoria proyectada"

            )

        else:

            return None

    else:

        return None

    if score < MIN_QUALITY:

        return None

    return score, reasons

# ============================================================

# ANALIZAR UN MERCADO

# ============================================================

def analyze_market(

    fixture,

    market,

    prediction,

    home_form,

    away_form

):

    home, away = team_names(

        fixture

    )

    if market["type"] == "goals":

        result = analyze_goals(

            market,

            prediction,

            home_form,

            away_form

        )

    elif market["type"] == "btts":

        result = analyze_btts(

            market,

            prediction,

            home_form,

            away_form

        )

    elif market["type"] in (

        "winner",

        "double_chance",

        "dnb"

    ):

        result = analyze_result(

            market,

            prediction,

            home,

            away

        )

    else:

        # No inventamos una señal para mercados

        # que todavía no tienen suficiente información

        # estadística disponible.

        return None

    if not result:

        return None

    score, reasons = result

    return {

        "fixture_id":

            fixture["fixture"]["id"],

        "home":

            home,

        "away":

            away,

        "bet":

            market["label"],

        "market":

            market["name"],

        "odd":

            market["odd"],

        "bookmaker":

            market["bookmaker"],

        "score":

            score,

        "reasons":

            reasons,

        "kickoff":

            fixture

            .get("fixture", {})

            .get("date", "")

    }

# ============================================================

# ESCANEAR TODOS LOS PARTIDOS

# ============================================================

def scan_matches():

    date = today()

    print("=" * 60)

    print(

        f"ESCANEO DE PARTIDOS - {date}"

    )

    print("=" * 60)

    fixtures = get_all_pages(

        "/fixtures",

        {

            "date": date,

            "timezone":

                "America/Tegucigalpa"

        }

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

    print(

        f"Partidos pendientes: "

        f"{len(upcoming)}"

    )

    opportunities = []

    for index, fixture in enumerate(

        upcoming,

        1

    ):

        fixture_id = (

            fixture

            .get("fixture", {})

            .get("id")

        )

        if not fixture_id:

            continue

        home, away = team_names(

            fixture

        )

        print(

            f"[{index}/{len(upcoming)}] "

            f"{home} vs {away}"

        )

        # ----------------------------------------------------

        # CUOTAS

        # ----------------------------------------------------

        markets = get_odds(

            fixture_id

        )

        if not markets:

            continue

        # ----------------------------------------------------

        # PREDICCIÓN

        # ----------------------------------------------------

        prediction_data = (

            get_prediction(

                fixture_id

            )

        )

        prediction = (

            prediction_info(

                prediction_data

            )

        )

        # ----------------------------------------------------

        # FORMA RECIENTE

        # ----------------------------------------------------

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

        form_date = (

            fixture

            .get("fixture", {})

            .get("date", "")

        )[:10]

        if not form_date:

            form_date = date

        home_form = get_recent_form(

            home_id,

            form_date

        )

        away_form = get_recent_form(

            away_id,

            form_date

        )

        # ----------------------------------------------------

        # ANALIZAR TODOS LOS MERCADOS

        # ----------------------------------------------------

        for market in markets:

            result = analyze_market(

                fixture,

                market,

                prediction,

                home_form,

                away_form

            )

            if result:

                opportunities.append(

                    result

                )

    return opportunities

# ============================================================

# ELEGIR LAS MEJORES

# ============================================================

def choose_best(opportunities):

    if not opportunities:

        return []

    # Ordenamos primero por calidad.

    # La cuota NO es lo primero:

    # primero queremos que el evento sea sólido.

    opportunities.sort(

        key=lambda item: (

            item["score"],

            item["odd"]

        ),

        reverse=True

    )

    selected = []

    used_fixtures = set()

    for opportunity in opportunities:

        fixture_id = (

            opportunity["fixture_id"]

        )

        # No enviar 3 mercados diferentes

        # del mismo partido.

        if fixture_id in used_fixtures:

            continue

        selected.append(

            opportunity

        )

        used_fixtures.add(

            fixture_id

        )

        if len(selected) >= MAX_ALERTS:

            break

    return selected

# ============================================================

# MENSAJE TELEGRAM

# ============================================================

def build_message(alerts):

    date = today()

    if not alerts:

        return (

            "🤖 <b>ESCANEO COMPLETADO</b>\n"

            f"📅 {date}\n\n"

            "❌ No encontré una oportunidad "

            "suficientemente clara.\n\n"

            f"💰 Cuota mínima: {MIN_ODD:.2f}\n"

            "🚫 No se forzó ningún pick."

        )

    lines = [

        "🤖 <b>MEJORES OPORTUNIDADES</b>",

        f"📅 {date}",

        ""

    ]

    for number_pick, alert in enumerate(

        alerts,

        1

    ):

        reasons = "; ".join(

            alert["reasons"][:3]

        )

        lines.append(

            f"🔥 <b>{number_pick}. "

            f"{alert['home']} vs "

            f"{alert['away']}</b>"

        )

        lines.append(

            f"🎯 Pick: <b>{alert['bet']}</b>"

        )

        lines.append(

            f"💰 Cuota: <b>"

            f"{alert['odd']:.2f}</b>"

        )

        lines.append(

            f"📊 Fuerza de la señal: "

            f"<b>{alert['score']:.1f}/10</b>"

        )

        lines.append(

            f"🧠 {reasons}"

        )

        lines.append("")

    lines.append(

        "ℹ️ El bot no busca llenar un número "

        "fijo de alertas. Solo muestra las "

        "oportunidades que considera más claras."

    )

    return "\n".join(lines)

# ============================================================

# MAIN

# ============================================================

def main():

    if not API_KEY:

        raise RuntimeError(

            "Falta API_FOOTBALL_KEY"

        )

    if not TELEGRAM_TOKEN:

        raise RuntimeError(

            "Falta TELEGRAM_BOT_TOKEN"

        )

    if not CHAT_ID:

        raise RuntimeError(

            "Falta TELEGRAM_CHAT_ID"

        )

    opportunities = scan_matches()

    print(

        f"\nOportunidades encontradas: "

        f"{len(opportunities)}"

    )

    alerts = choose_best(

        opportunities

    )

    print(

        f"Oportunidades seleccionadas: "

        f"{len(alerts)}"

    )

    for alert in alerts:

        print(

            f"- {alert['home']} vs "

            f"{alert['away']} | "

            f"{alert['bet']} @ "

            f"{alert['odd']:.2f} | "

            f"Fuerza: "

            f"{alert['score']:.1f}/10"

        )

    message = build_message(

        alerts

    )

    if send_telegram(message):

        print(

            "✅ Mensaje enviado a Telegram."

        )

    else:

        print(

            "❌ No se pudo enviar "

            "el mensaje a Telegram."

        )

# ============================================================

# EJECUCIÓN

# ============================================================

if __name__ == "__main__":

    main()
