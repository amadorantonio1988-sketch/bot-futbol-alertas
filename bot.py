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

def api_get(endpoint, params=None):

    headers = {"x-apisports-key": API_KEY}

    response = requests.get(

        f"{BASE_URL}/{endpoint}",

        headers=headers,

        params=params or {},

        timeout=30

    )

    response.raise_for_status()

    data = response.json()

    if data.get("errors"):

        raise Exception(str(data["errors"]))

    return data.get("response", [])

def telegram(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

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

    return math.exp(-lam) * (lam ** goals) / math.factorial(goals)

def probability_over(lam, line):

    max_goals = int(math.floor(line))

    under = sum(poisson_probability(lam, g) for g in range(max_goals + 1))

    return 1 - under

def probability_btts(home_lambda, away_lambda):

    home_scores = 1 - math.exp(-home_lambda)

    away_scores = 1 - math.exp(-away_lambda)

    return home_scores * away_scores

def get_probability(prediction, market, selection):

    percent = prediction.get("percent", {})

    if market == "winner":

        if selection == "Home":

            return float(percent.get("home", "0").replace("%", "")) / 100

        if selection == "Draw":

            return float(percent.get("draw", "0").replace("%", "")) / 100

        if selection == "Away":

            return float(percent.get("away", "0").replace("%", "")) / 100

    goals = prediction.get("goals", {})

    try:

        home_goals = float(goals.get("home", 1.0))

        away_goals = float(goals.get("away", 1.0))

    except:

        home_goals = 1.0

        away_goals = 1.0

    total_goals = home_goals + away_goals

    if market == "goals":

        if selection.lower() == "over 1.5":

            return probability_over(total_goals, 1.5)

        if selection.lower() == "over 2.5":

            return probability_over(total_goals, 2.5)

        if selection.lower() == "under 2.5":

            return 1 - probability_over(total_goals, 2.5)

    if market == "btts":

        p = probability_btts(home_goals, away_goals)

        if selection.lower() in ["yes", "btts yes", "both teams to score - yes"]:

            return p

        return 1 - p

    return 0

def find_odds(odds_data, home, away):

    candidates = []

    for bookmaker in odds_data:

        bookmaker_name = bookmaker.get("bookmaker", {}).get("name", "Bookmaker")

        for bet in bookmaker.get("bets", []):

            market_name = bet.get("name", "")

            for value in bet.get("values", []):

                odd_text = value.get("odd")

                try:

                    odd = float(odd_text)

                except:

                    continue

                if not (MIN_ODD <= odd <= MAX_ODD):

                    continue

                selection = str(value.get("value", ""))

                market = None

                normalized = market_name.lower()

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

                elif normalized in ["both teams to score", "btts"]:

                    if selection.lower() in ["yes", "no"]:

                        market = "btts"

                elif "over/under" in normalized or "goals over/under" in normalized:

                    if selection.lower() in ["over 1.5", "over 2.5", "under 2.5"]:

                        market = "goals"

                if market:

                    candidates.append({

                        "market": market,

                        "selection": selection,

                        "odd": odd,

                        "bookmaker": bookmaker_name

                    })

    return candidates

def analyze_fixture(fixture):

    fixture_id = fixture["fixture"]["id"]

    home = fixture["teams"]["home"]["name"]

    away = fixture["teams"]["away"]["name"]

    prediction_data = api_get(

        "predictions",

        {"fixture": fixture_id}

    )

    if not prediction_data:

        return []

    prediction = prediction_data[0].get("predictions", {})

    odds_data = api_get(

        "odds",

        {"fixture": fixture_id}

    )

    if not odds_data:

        return []

    candidates = find_odds(

        odds_data,

        home,

        away

    )

    results = []

    for candidate in candidates:

        probability = get_probability(

            prediction,

            candidate["market"],

            candidate["selection"]

        )

        if probability < MIN_PROBABILITY:

            continue

        implied_probability = 1 / candidate["odd"]

        value = probability - implied_probability

        if value < MIN_VALUE:

            continue

        score = probability * 100 + value * 100

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

        raise Exception("Falta API_FOOTBALL_KEY")

    if not TELEGRAM_TOKEN:

        raise Exception("Falta TELEGRAM_BOT_TOKEN")

    if not CHAT_ID:

        raise Exception("Falta TELEGRAM_CHAT_ID")

    honduras = timezone(timedelta(hours=-6))

    today = datetime.now(honduras).strftime("%Y-%m-%d")

    fixtures = api_get(

        "fixtures",

        {

            "date": today

        }

    )

    future_matches = []

    for fixture in fixtures:

        status = fixture["fixture"]["status"]["short"]

        if status not in ["NS", "TBD"]:

            continue

        future_matches.append(fixture)

    all_candidates = []

    for fixture in future_matches:

        try:

            results = analyze_fixture(fixture)

            all_candidates.extend(results)

        except Exception as error:

            print(

                f"Error analizando "

                f'{fixture["teams"]["home"]["name"]} vs '

                f'{fixture["teams"]["away"]["name"]}: {error}'

            )

    all_candidates.sort(

        key=lambda x: x["score"],

        reverse=True

    )

    if not all_candidates:

        message = (

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "❌ No encontré apuestas que cumplan todos los filtros.\n\n"

            f"Cuotas: {MIN_ODD:.2f}–{MAX_ODD:.2f}\n"

            f"Probabilidad mínima: {MIN_PROBABILITY:.0%}\n"

            "No se fuerza ninguna apuesta."

        )

        telegram(message)

        return

    best = all_candidates[0]

    message = (

        "🔥 APUESTA PRINCIPAL DEL DÍA\n\n"

        f"⚽ {best['home']} vs {best['away']}\n\n"

        f"🎯 Mercado: {best['market']}\n"

        f"✅ Selección: {best['selection']}\n"

        f"💰 Cuota: {best['odd']:.2f}\n"

        f"📊 Probabilidad estimada: {best['probability']:.1%}\n"

        f"📈 Valor: {best['value']:.1%}\n"

        f"🏦 {best['bookmaker']}\n"

    )

    match_time = datetime.fromisoformat(

        best["date"].replace("Z", "+00:00")

    ).astimezone(honduras)

    message += (

        f"🕐 Hora Honduras: {match_time.strftime('%H:%M')}\n\n"

        "⭐ MAYOR CONFIANZA DEL DÍA"

    )

    telegram(message)

    if len(all_candidates) > 1:

        extra = "\n\n🔥 OTRAS APUESTAS DE ALTA CONFIANZA\n"

        for pick in all_candidates[1:6]:

            extra += (

                f"\n⚽ {pick['home']} vs {pick['away']}\n"

                f"🎯 {pick['selection']}\n"

                f"💰 Cuota {pick['odd']:.2f}\n"

                f"📊 Prob. {pick['probability']:.1%}\n"

            )

        telegram(extra)

if __name__ == "__main__":

    main()
