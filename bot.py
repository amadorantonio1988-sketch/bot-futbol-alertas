import os

import requests

from datetime import datetime, timezone, timedelta

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

# ============================================================

# FILTROS PRINCIPALES

# ============================================================

MIN_ODD = 1.50

MAX_ODD = 1.90

MIN_PROBABILITY = 0.60

MAX_PROBABILITY = 0.80

MIN_VALUE = 0.05

MAX_PREDICTIONS = 20

MAX_ALERTS = 5

# ============================================================

# API

# ============================================================

def api_get(endpoint, params=None):

    headers = {

        "x-apisports-key": API_KEY

    }

    response = requests.get(

        f"{BASE_URL}/{endpoint}",

        headers=headers,

        params=params or {},

        timeout=30

    )

    if response.status_code == 429:

        raise Exception(

            "API-Football: límite de solicitudes alcanzado (429)"

        )

    response.raise_for_status()

    data = response.json()

    if data.get("errors"):

        raise Exception(str(data["errors"]))

    return data.get("response", [])

# ============================================================

# TELEGRAM

# ============================================================

def telegram(message):

    url = (

        f"https://api.telegram.org/"

        f"bot{TELEGRAM_TOKEN}/sendMessage"

    )

    response = requests.post(

        url,

        data={

            "chat_id": CHAT_ID,

            "text": message

        },

        timeout=30

    )

    response.raise_for_status()

# ============================================================

# UTILIDADES

# ============================================================

def clean_percent(value):

    """

    Convierte:

    '65%' -> 0.65

    65 -> 0.65

    None -> 0.0

    """

    if value is None:

        return 0.0

    try:

        text = str(value).replace("%", "").strip()

        number = float(text)

        if number > 1:

            number = number / 100

        return max(0.0, min(1.0, number))

    except:

        return 0.0

def implied_probability(odd):

    try:

        odd = float(odd)

        if odd <= 1:

            return 0.0

        return 1 / odd

    except:

        return 0.0

def calculate_value(probability, odd):

    return probability - implied_probability(odd)

# ============================================================

# PROBABILIDAD DEL MERCADO

# ============================================================

def get_probability(prediction, market, selection):

    """

    IMPORTANTE:

    NO usamos predictions.goals.home/away como medias

    de Poisson.

    API-Football entrega en predictions:

    - winner

    - win_or_draw

    - under_over

    - goals

    - advice

    - percent

    El campo percent corresponde al ganador:

    home / draw / away.

    Para goles utilizamos la predicción under_over del

    propio modelo solamente como filtro de dirección.

    """

    if market == "winner":

        percent = prediction.get("percent", {})

        if selection == "Home":

            return clean_percent(

                percent.get("home")

            )

        if selection == "Draw":

            return clean_percent(

                percent.get("draw")

            )

        if selection == "Away":

            return clean_percent(

                percent.get("away")

            )

        return 0.0

    if market == "goals":

        under_over = str(

            prediction.get("under_over", "")

        ).lower().strip()

        selected = selection.lower().strip()

        # API predice Over 2.5

        if selected == "over 2.5":

            if "over 2.5" in under_over:

                return 0.70

            return 0.0

        # API predice Under 2.5

        if selected == "under 2.5":

            if "under 2.5" in under_over:

                return 0.70

            return 0.0

        # Over 1.5:

        # si API predice Over 2.5, también es compatible.

        if selected == "over 1.5":

            if (

                "over 2.5" in under_over

                or "over 1.5" in under_over

            ):

                return 0.75

            return 0.0

        return 0.0

    if market == "btts":

        advice = str(

            prediction.get("advice", "")

        ).lower()

        selected = selection.lower().strip()

        # API-Football no proporciona un porcentaje BTTS

        # independiente en predictions.

        #

        # No inventamos una probabilidad.

        #

        # Por seguridad dejamos estos mercados fuera

        # hasta disponer de una fuente estadística específica.

        return 0.0

    return 0.0

# ============================================================

# CUOTAS

# ============================================================

def find_odds(odds_item, home, away):

    candidates = []

    for bookmaker in odds_item.get("bookmakers", []):

        bookmaker_name = bookmaker.get(

            "name",

            "Bookmaker"

        )

        for bet in bookmaker.get("bets", []):

            market_name = bet.get(

                "name",

                ""

            )

            normalized = market_name.lower()

            for value in bet.get(

                "values",

                []

            ):

                try:

                    odd = float(

                        value.get("odd")

                    )

                except:

                    continue

                if not (

                    MIN_ODD

                    <= odd

                    <= MAX_ODD

                ):

                    continue

                selection = str(

                    value.get(

                        "value",

                        ""

                    )

                )

                market = None

                # -------------------------

                # GANADOR

                # -------------------------

                if "match winner" in normalized:

                    if selection == home:

                        selection = "Home"

                        market = "winner"

                    elif selection == away:

                        selection = "Away"

                        market = "winner"

                    elif (

                        selection.lower()

                        == "draw"

                    ):

                        selection = "Draw"

                        market = "winner"

                # -------------------------

                # BTTS

                # -------------------------

                elif (

                    "both teams to score"

                    in normalized

                    or normalized == "btts"

                ):

                    if selection.lower() in [

                        "yes",

                        "no"

                    ]:

                        market = "btts"

                # -------------------------

                # GOLES

                # -------------------------

                elif (

                    "over/under"

                    in normalized

                    or "goals over/under"

                    in normalized

                ):

                    if selection.lower() in [

                        "over 1.5",

                        "over 2.5",

                        "under 2.5"

                    ]:

                        market = "goals"

                if market:

                    candidates.append({

                        "market": market,

                        "selection": selection,

                        "odd": odd,

                        "bookmaker": bookmaker_name

                    })

    return candidates

# ============================================================

# ANALIZAR PARTIDO

# ============================================================

def analyze_fixture(

    fixture,

    odds_item

):

    fixture_id = fixture[

        "fixture"

    ]["id"]

    home = fixture[

        "teams"

    ]["home"]["name"]

    away = fixture[

        "teams"

    ]["away"]["name"]

    candidates = find_odds(

        odds_item,

        home,

        away

    )

    if not candidates:

        return []

    prediction_data = api_get(

        "predictions",

        {

            "fixture": fixture_id

        }

    )

    if not prediction_data:

        return []

    prediction = prediction_data[0].get(

        "predictions",

        {}

    )

    results = []

    for candidate in candidates:

        probability = get_probability(

            prediction,

            candidate["market"],

            candidate["selection"]

        )

        probability = max(

            0.0,

            min(1.0, probability)

        )

        # -------------------------

        # FILTRO DE PROBABILIDAD

        # -------------------------

        if probability < MIN_PROBABILITY:

            continue

        if probability > MAX_PROBABILITY:

            continue

        # -------------------------

        # VALOR

        # -------------------------

        value = calculate_value(

            probability,

            candidate["odd"]

        )

        if value < MIN_VALUE:

            continue

        score = (

            probability * 100

            + value * 100

        )

        results.append({

            "fixture_id": fixture_id,

            "home": home,

            "away": away,

            "market": candidate["market"],

            "selection": candidate["selection"],

            "odd": candidate["odd"],

            "probability": probability,

            "value": value,

            "score": score,

            "bookmaker": candidate["bookmaker"],

            "date": fixture[

                "fixture"

            ]["date"]

        })

    return results

# ============================================================

# ELIMINAR DUPLICADOS

# ============================================================

def remove_duplicates(results):

    best = {}

    for item in results:

        key = (

            item["fixture_id"],

            item["market"],

            item["selection"]

        )

        if key not in best:

            best[key] = item

            continue

        # Conservamos la cuota más alta

        if item["odd"] > best[key]["odd"]:

            best[key] = item

    return list(best.values())

# ============================================================

# MAIN

# ============================================================

def main():

    if not API_KEY:

        raise Exception(

            "Falta API_FOOTBALL_KEY"

        )

    if not TELEGRAM_TOKEN:

        raise Exception(

            "Falta TELEGRAM_BOT_TOKEN"

        )

    if not CHAT_ID:

        raise Exception(

            "Falta TELEGRAM_CHAT_ID"

        )

    honduras = timezone(

        timedelta(hours=-6)

    )

    today = datetime.now(

        honduras

    ).strftime("%Y-%m-%d")

    print(

        f"Fecha Honduras: {today}"

    )

    # ========================================================

    # CUOTAS

    # ========================================================

    print(

        "Buscando cuotas del día..."

    )

    odds_response = api_get(

        "odds",

        {

            "date": today

        }

    )

    print(

        "Partidos con cuotas encontrados: "

        f"{len(odds_response)}"

    )

    if not odds_response:

        telegram(

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "⚠️ No se encontraron cuotas "

            "disponibles para analizar."

        )

        return

    # ========================================================

    # FIXTURES

    # ========================================================

    print(

        "Buscando partidos del día..."

    )

    fixtures = api_get(

        "fixtures",

        {

            "date": today

        }

    )

    fixture_map = {}

    for fixture in fixtures:

        status = fixture[

            "fixture"

        ]["status"]["short"]

        if status not in [

            "NS",

            "TBD"

        ]:

            continue

        fixture_id = fixture[

            "fixture"

        ]["id"]

        fixture_map[

            fixture_id

        ] = fixture

    # ========================================================

    # PARTIDOS CON CUOTAS VÁLIDAS

    # ========================================================

    candidates_with_odds = []

    for odds_item in odds_response:

        fixture_info = odds_item.get(

            "fixture",

            {}

        )

        fixture_id = fixture_info.get(

            "id"

        )

        if fixture_id not in fixture_map:

            continue

        fixture = fixture_map[

            fixture_id

        ]

        home = fixture[

            "teams"

        ]["home"]["name"]

        away = fixture[

            "teams"

        ]["away"]["name"]

        odds_candidates = find_odds(

            odds_item,

            home,

            away

        )

        if odds_candidates:

            candidates_with_odds.append(

                (

                    fixture,

                    odds_item

                )

            )

    print(

        "Partidos candidatos por cuota: "

        f"{len(candidates_with_odds)}"

    )

    # ========================================================

    # PRIORIZAR CUOTAS CERCANAS A 1.70

    # ========================================================

    def fixture_priority(item):

        fixture, odds_item = item

        home = fixture[

            "teams"

        ]["home"]["name"]

        away = fixture[

            "teams"

        ]["away"]["name"]

        candidates = find_odds(

            odds_item,

            home,

            away

        )

        if not candidates:

            return 999

        return min(

            abs(

                candidate["odd"]

                - 1.70

            )

            for candidate in candidates

        )

    candidates_with_odds.sort(

        key=fixture_priority

    )

    selected = candidates_with_odds[

        :MAX_PREDICTIONS

    ]

    print(

        "Partidos que serán analizados "

        "con predictions: "

        f"{len(selected)}"

    )

    # ========================================================

    # ANALIZAR

    # ========================================================

    all_candidates = []

    for fixture, odds_item in selected:

        try:

            results = analyze_fixture(

                fixture,

                odds_item

            )

            all_candidates.extend(

                results

            )

        except Exception as error:

            print(

                "Error analizando "

                f'{fixture["teams"]["home"]["name"]} '

                "vs "

                f'{fixture["teams"]["away"]["name"]}: '

                f"{error}"

            )

    # ========================================================

    # QUITAR DUPLICADOS

    # ========================================================

    all_candidates = remove_duplicates(

        all_candidates

    )

    # ========================================================

    # ORDENAR

    # ========================================================

    all_candidates.sort(

        key=lambda x: x["score"],

        reverse=True

    )

    print(

        "Apuestas que cumplen todos "

        "los filtros: "

        f"{len(all_candidates)}"

    )

    # ========================================================

    # SIN APUESTAS

    # ========================================================

    if not all_candidates:

        message = (

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "❌ No encontré apuestas "

            "que cumplan todos los filtros.\n\n"

            f"💰 Cuotas: "

            f"{MIN_ODD:.2f}–{MAX_ODD:.2f}\n"

            f"📊 Probabilidad: "

            f"{MIN_PROBABILITY:.0%}"

            f"–{MAX_PROBABILITY:.0%}\n"

            f"📈 Valor mínimo: "

            f"{MIN_VALUE:.0%}\n\n"

            f"Partidos con cuotas: "

            f"{len(candidates_with_odds)}\n"

            f"Predictions analizadas: "

            f"{len(selected)}\n\n"

            "No se fuerza ninguna apuesta."

        )

        telegram(message)

        return

    # ========================================================

    # APUESTA PRINCIPAL

    # ========================================================

    best = all_candidates[0]

    match_time = datetime.fromisoformat(

        best["date"].replace(

            "Z",

            "+00:00"

        )

    ).astimezone(

        honduras

    )

    message = (

        "🔥 APUESTA PRINCIPAL DEL DÍA\n\n"

        f"⚽ {best['home']} vs "

        f"{best['away']}\n\n"

        f"🎯 Mercado: "

        f"{best['market']}\n"

        f"✅ Selección: "

        f"{best['selection']}\n"

        f"💰 Cuota: "

        f"{best['odd']:.2f}\n"

        f"📊 Probabilidad estimada: "

        f"{best['probability']:.1%}\n"

        f"📈 Valor: "

        f"{best['value']:.1%}\n"

        f"🏦 {best['bookmaker']}\n"

        f"🕐 Hora Honduras: "

        f"{match_time.strftime('%H:%M')}\n\n"

        "⭐ MAYOR CONFIANZA DEL DÍA"

    )

    telegram(message)

    # ========================================================

    # OTRAS APUESTAS

    # ========================================================

    if len(all_candidates) > 1:

        extra = (

            "\n🔥 OTRAS APUESTAS "

            "DE ALTA CONFIANZA\n"

        )

        for pick in all_candidates[

            1:MAX_ALERTS

        ]:

            extra += (

                f"\n⚽ {pick['home']} "

                f"vs {pick['away']}\n"

                f"🎯 {pick['selection']}\n"

                f"💰 Cuota "

                f"{pick['odd']:.2f}\n"

                f"📊 Prob. "

                f"{pick['probability']:.1%}\n"

                f"📈 Valor "

                f"{pick['value']:.1%}\n"

            )

        telegram(extra)

# ============================================================

# EJECUCIÓN

# ============================================================

if __name__ == "__main__":

    main()
