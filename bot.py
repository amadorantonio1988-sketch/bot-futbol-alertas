import os

import math

import requests

from datetime import datetime, timezone, timedelta

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

MIN_ODD = 1.50

MAX_ODD = 1.90

MIN_PROBABILITY = 0.62

MIN_VALUE = 0.05

MAX_PREDICTIONS = 20

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

def poisson_probability(lam, goals):

    try:

        lam = float(lam)

        goals = int(goals)

    except:

        return 0.0

    if lam < 0 or lam > 10:

        return 0.0

    return (

        math.exp(-lam)

        * (lam ** goals)

        / math.factorial(goals)

    )

def probability_over(lam, line):

    try:

        lam = float(lam)

        line = float(line)

    except:

        return 0.0

    if lam < 0 or lam > 10:

        return 0.0

    max_goals = int(math.floor(line))

    under_or_equal = 0.0

    for goals in range(max_goals + 1):

        under_or_equal += poisson_probability(

            lam,

            goals

        )

    probability = 1.0 - under_or_equal

    return max(

        0.0,

        min(1.0, probability)

    )

def probability_btts(home_lambda, away_lambda):

    try:

        home_lambda = float(home_lambda)

        away_lambda = float(away_lambda)

    except:

        return 0.0

    if (

        home_lambda < 0

        or away_lambda < 0

        or home_lambda > 10

        or away_lambda > 10

    ):

        return 0.0

    home_scores = 1 - math.exp(-home_lambda)

    away_scores = 1 - math.exp(-away_lambda)

    probability = (

        home_scores

        * away_scores

    )

    return max(

        0.0,

        min(1.0, probability)

    )

def get_probability(

    prediction,

    market,

    selection

):

    percent = prediction.get(

        "percent",

        {}

    )

    if market == "winner":

        if selection == "Home":

            try:

                return (

                    float(

                        percent.get(

                            "home",

                            "0"

                        ).replace("%", "")

                    ) / 100

                )

            except:

                return 0.0

        if selection == "Draw":

            try:

                return (

                    float(

                        percent.get(

                            "draw",

                            "0"

                        ).replace("%", "")

                    ) / 100

                )

            except:

                return 0.0

        if selection == "Away":

            try:

                return (

                    float(

                        percent.get(

                            "away",

                            "0"

                        ).replace("%", "")

                    ) / 100

                )

            except:

                return 0.0

    goals = prediction.get(

        "goals",

        {}

    )

    try:

        home_goals = float(

            goals.get(

                "home",

                1.0

            )

        )

        away_goals = float(

            goals.get(

                "away",

                1.0

            )

        )

    except:

        home_goals = 1.0

        away_goals = 1.0

    # Protección contra valores imposibles.

    home_goals = max(

        0.0,

        min(10.0, home_goals)

    )

    away_goals = max(

        0.0,

        min(10.0, away_goals)

    )

    total_goals = max(

        0.0,

        min(

            10.0,

            home_goals + away_goals

        )

    )

    if market == "goals":

        if selection.lower() == "over 1.5":

            return probability_over(

                total_goals,

                1.5

            )

        if selection.lower() == "over 2.5":

            return probability_over(

                total_goals,

                2.5

            )

        if selection.lower() == "under 2.5":

            return (

                1

                - probability_over(

                    total_goals,

                    2.5

                )

            )

    if market == "btts":

        probability = probability_btts(

            home_goals,

            away_goals

        )

        if selection.lower() in [

            "yes",

            "btts yes",

            "both teams to score - yes"

        ]:

            return probability

        return 1 - probability

    return 0.0

def find_odds(

    odds_item,

    home,

    away

):

    candidates = []

    for bookmaker in odds_item.get(

        "bookmakers",

        []

    ):

        bookmaker_name = bookmaker.get(

            "name",

            "Bookmaker"

        )

        for bet in bookmaker.get(

            "bets",

            []

        ):

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

                if "match winner" in normalized:

                    if selection == home:

                        selection = "Home"

                        market = "winner"

                    elif selection == away:

                        selection = "Away"

                        market = "winner"

                    elif selection.lower() == "draw":

                        selection = "Draw"

                        market = "winner"

                elif normalized in [

                    "both teams to score",

                    "btts"

                ]:

                    if selection.lower() in [

                        "yes",

                        "no"

                    ]:

                        market = "btts"

                elif (

                    "over/under" in normalized

                    or "goals over/under" in normalized

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

    prediction = prediction_data[

        0

    ].get(

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

        # Protección adicional:

        # ninguna probabilidad puede superar 100%.

        probability = max(

            0.0,

            min(1.0, probability)

        )

        if probability < MIN_PROBABILITY:

            continue

        implied_probability = (

            1 / candidate["odd"]

        )

        value = (

            probability

            - implied_probability

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

            "date": fixture["fixture"]["date"]

        })

    return results

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

        message = (

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "⚠️ No se encontraron cuotas "

            "disponibles para analizar."

        )

        telegram(message)

        return

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

    def fixture_priority(item):

        fixture, odds_item = item

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

        if not odds_candidates:

            return 999

        best_distance = min(

            abs(

                candidate["odd"] - 1.70

            )

            for candidate in odds_candidates

        )

        return best_distance

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

                f'{fixture["teams"]["home"]["name"]} vs '

                f'{fixture["teams"]["away"]["name"]}: '

                f"{error}"

            )

    all_candidates.sort(

        key=lambda x: x["score"],

        reverse=True

    )

    print(

        "Apuestas que cumplen todos "

        "los filtros: "

        f"{len(all_candidates)}"

    )

    if not all_candidates:

        message = (

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "❌ No encontré apuestas que "

            "cumplan todos los filtros.\n\n"

            f"Cuotas: "

            f"{MIN_ODD:.2f}–{MAX_ODD:.2f}\n"

            f"Probabilidad mínima: "

            f"{MIN_PROBABILITY:.0%}\n"

            f"Partidos con cuotas: "

            f"{len(candidates_with_odds)}\n"

            f"Predictions analizadas: "

            f"{len(selected)}\n\n"

            "No se fuerza ninguna apuesta."

        )

        telegram(message)

        return

    best = all_candidates[0]

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

    )

    match_time = datetime.fromisoformat(

        best["date"].replace(

            "Z",

            "+00:00"

        )

    ).astimezone(honduras)

    message += (

        f"🕐 Hora Honduras: "

        f"{match_time.strftime('%H:%M')}\n\n"

        "⭐ MAYOR CONFIANZA DEL DÍA"

    )

    telegram(message)

    if len(all_candidates) > 1:

        extra = (

            "\n\n🔥 OTRAS APUESTAS "

            "DE ALTA CONFIANZA\n"

        )

        for pick in all_candidates[1:6]:

            extra += (

                f"\n⚽ {pick['home']} vs "

                f"{pick['away']}\n"

                f"🎯 {pick['selection']}\n"

                f"💰 Cuota "

                f"{pick['odd']:.2f}\n"

                f"📊 Prob. "

                f"{pick['probability']:.1%}\n"

            )

        telegram(extra)

if __name__ == "__main__":

    main()
