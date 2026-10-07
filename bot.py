import os

import requests

from collections import defaultdict

from datetime import datetime, timezone, timedelta

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

# ============================================================

# CONFIGURACIÓN

# ============================================================

MIN_ODD = 1.50

MIN_PROBABILITY = 0.60

MIN_CONFIDENCE = 70

HIGH_CONFIDENCE = 75

VERY_HIGH_CONFIDENCE = 85

MAX_PREDICTIONS = 20

MAX_ALERTS = 5

# Evita analizar mercados extremadamente raros

# con información insuficiente.

MIN_BOOKMAKERS = 2

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

            "API-Football límite 429"

        )

    response.raise_for_status()

    data = response.json()

    if data.get("errors"):

        raise Exception(

            str(data["errors"])

        )

    return data.get(

        "response",

        [])

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

def safe_float(value):

    try:

        return float(value)

    except:

        return None

def implied_probability(odd):

    try:

        odd = float(odd)

        if odd <= 1:

            return 0.0

        return 1.0 / odd

    except:

        return 0.0

def clean_percent(value):

    if value is None:

        return None

    try:

        value = str(value).replace(

            "%",

            ""

        ).strip()

        number = float(value)

        if number > 1:

            number /= 100

        return max(

            0.0,

            min(1.0, number)

        )

    except:

        return None

# ============================================================

# NORMALIZAR NOMBRE DEL MERCADO

# ============================================================

def normalize_market_name(name):

    name = str(name).lower().strip()

    replacements = {

        "match winner": "match winner",

        "1x2": "match winner",

        "both teams to score": "btts",

        "btts": "btts",

        "goals over/under": "goals over/under",

        "over/under": "goals over/under",

        "total goals": "goals over/under",

        "double chance": "double chance",

        "asian handicap": "asian handicap",

        "handicap": "handicap"

    }

    for key, value in replacements.items():

        if key in name:

            return value

    return name

# ============================================================

# CONVERTIR SELECCIÓN

# ============================================================

def normalize_selection(selection):

    return str(

        selection

    ).strip()

# ============================================================

# EXTRAER MERCADOS

# ============================================================

def extract_markets(

    odds_item,

    home,

    away

):

    markets = []

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

            market_key = normalize_market_name(

                market_name

            )

            values = bet.get(

                "values",

                []

            )

            for value in values:

                odd = safe_float(

                    value.get("odd")

                )

                if odd is None:

                    continue

                if odd < MIN_ODD:

                    continue

                selection = normalize_selection(

                    value.get(

                        "value",

                        ""

                    )

                )

                if not selection:

                    continue

                markets.append({

                    "market_name": market_name,

                    "market_key": market_key,

                    "selection": selection,

                    "odd": odd,

                    "bookmaker": bookmaker_name,

                    "home": home,

                    "away": away

                })

    return markets

# ============================================================

# PROBABILIDAD DE CONSENSO

# ============================================================

def calculate_consensus_probability(

    markets,

    target

):

    """

    Calcula una probabilidad de consenso

    usando las cuotas disponibles de varios

    bookmakers.

    No se presenta como probabilidad garantizada.

    Es una estimación derivada del mercado.

    """

    relevant = []

    target_market = target[

        "market_key"

    ]

    target_selection = target[

        "selection"

    ]

    for market in markets:

        if market[

            "market_key"

        ] != target_market:

            continue

        if market[

            "selection"

        ] != target_selection:

            continue

        relevant.append(

            market

        )

    if not relevant:

        return 0.0, 0

    # Agrupar por bookmaker

    by_bookmaker = defaultdict(list)

    for market in markets:

        if market[

            "market_key"

        ] != target_market:

            continue

        by_bookmaker[

            market["bookmaker"]

        ].append(

            market

        )

    probabilities = []

    for bookmaker, values in by_bookmaker.items():

        target_value = None

        for value in values:

            if (

                value["selection"]

                == target_selection

            ):

                target_value = value

                break

        if not target_value:

            continue

        odds = []

        for value in values:

            odd = safe_float(

                value["odd"]

            )

            if odd is not None:

                odds.append(

                    odd

                )

        if len(odds) < 2:

            continue

        # Probabilidad implícita normalizada

        inverse_total = sum(

            1 / odd

            for odd in odds

            if odd > 1

        )

        if inverse_total <= 0:

            continue

        probability = (

            (1 / target_value["odd"])

            / inverse_total

        )

        probabilities.append(

            probability

        )

    if not probabilities:

        return (

            implied_probability(

                target["odd"]

            ),

            1

        )

    # Promedio de consenso

    probability = sum(

        probabilities

    ) / len(probabilities)

    return (

        max(

            0.0,

            min(

                1.0,

                probability

            )

        ),

        len(probabilities)

    )

# ============================================================

# COMPATIBILIDAD CON PREDICTION

# ============================================================

def prediction_alignment(

    prediction,

    market

):

    """

    Devuelve:

        1.0  = fuerte coincidencia

        0.5  = información parcial

        0.0  = sin información

       -1.0  = contradicción

    """

    if not prediction:

        return 0.0

    market_key = market[

        "market_key"

    ]

    selection = market[

        "selection"

    ].lower().strip()

    # --------------------------------------------------------

    # GANADOR

    # --------------------------------------------------------

    if market_key == "match winner":

        winner = prediction.get(

            "winner",

            {}

        )

        winner_name = str(

            winner.get(

                "name",

                ""

            )

        ).lower().strip()

        if not winner_name:

            return 0.0

        home = market[

            "home"

        ].lower().strip()

        away = market[

            "away"

        ].lower().strip()

        if (

            selection.lower()

            == market["home"].lower()

        ):

            if winner_name == home:

                return 1.0

            return -1.0

        if (

            selection.lower()

            == market["away"].lower()

        ):

            if winner_name == away:

                return 1.0

            return -1.0

        if selection == "draw":

            # Si API dice un ganador concreto,

            # el empate no recibe confirmación.

            return -1.0

    # --------------------------------------------------------

    # GOLES

    # --------------------------------------------------------

    if market_key == "goals over/under":

        under_over = str(

            prediction.get(

                "under_over",

                ""

            )

        ).lower().strip()

        if not under_over:

            return 0.0

        if (

            selection

            == under_over

        ):

            return 1.0

        # Coincidencia por dirección

        if (

            "over" in selection

            and "over" in under_over

        ):

            return 1.0

        if (

            "under" in selection

            and "under" in under_over

        ):

            return 1.0

        return -1.0

    # --------------------------------------------------------

    # OTROS MERCADOS

    # --------------------------------------------------------

    return 0.0

# ============================================================

# PROBABILIDAD DE GANADOR DE API

# ============================================================

def api_winner_probability(

    prediction,

    market

):

    if market[

        "market_key"

    ] != "match winner":

        return None

    percent = prediction.get(

        "percent",

        {}

    )

    selection = market[

        "selection"

    ]

    home = market[

        "home"

    ]

    away = market[

        "away"

    ]

    if selection == home:

        return clean_percent(

            percent.get(

                "home"

            )

        )

    if selection == away:

        return clean_percent(

            percent.get(

                "away"

            )

        )

    if selection.lower() == "draw":

        return clean_percent(

            percent.get(

                "draw"

            )

        )

    return None

# ============================================================

# CALCULAR CONFIANZA

# ============================================================

def calculate_confidence(

    market_probability,

    prediction_match,

    bookmaker_count,

    api_probability=None

):

    # Base: probabilidad del mercado

    confidence = (

        market_probability

        * 100

    )

    # Más bookmakers = mayor estabilidad

    if bookmaker_count >= 5:

        confidence += 7

    elif bookmaker_count >= 3:

        confidence += 4

    elif bookmaker_count >= 2:

        confidence += 2

    # Confirmación del modelo

    if prediction_match > 0:

        confidence += 8

    elif prediction_match < 0:

        confidence -= 12

    # Para ganador tenemos además

    # la probabilidad explícita de API-Football.

    if api_probability is not None:

        difference = (

            api_probability

            - market_probability

        )

        if difference >= 0.10:

            confidence += 8

        elif difference >= 0.05:

            confidence += 5

        elif difference <= -0.10:

            confidence -= 10

        elif difference <= -0.05:

            confidence -= 5

    confidence = max(

        0,

        min(

            100,

            confidence

        )

    )

    return confidence

# ============================================================

# CLASIFICACIÓN

# ============================================================

def classify_confidence(

    confidence

):

    if confidence >= VERY_HIGH_CONFIDENCE:

        return (

            "🟢 MUY ALTA",

            "MUY ALTA"

        )

    if confidence >= HIGH_CONFIDENCE:

        return (

            "🔵 ALTA",

            "ALTA"

        )

    if confidence >= MIN_CONFIDENCE:

        return (

            "🟡 BUENA",

            "BUENA"

        )

    return (

        "🔴 DESCARTADA",

        "DESCARTADA"

    )

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

    markets = extract_markets(

        odds_item,

        home,

        away

    )

    if not markets:

        return []

    # --------------------------------------------------------

    # PREDICTION

    # --------------------------------------------------------

    prediction_data = api_get(

        "predictions",

        {

            "fixture": fixture_id

        }

    )

    prediction = {}

    if prediction_data:

        prediction = prediction_data[

            0

        ].get(

            "predictions",

            {}

        )

    results = []

    # --------------------------------------------------------

    # ANALIZAR CADA MERCADO

    # --------------------------------------------------------

    for market in markets:

        market_probability, bookmaker_count = (

            calculate_consensus_probability(

                markets,

                market

            )

        )

        if market_probability < MIN_PROBABILITY:

            continue

        alignment = prediction_alignment(

            prediction,

            market

        )

        # Si el modelo contradice fuertemente

        # el mercado, descartamos.

        if alignment < 0:

            continue

        api_probability = api_winner_probability(

            prediction,

            market

        )

        confidence = calculate_confidence(

            market_probability,

            alignment,

            bookmaker_count,

            api_probability

        )

        level_icon, level_name = (

            classify_confidence(

                confidence

            )

        )

        if confidence < MIN_CONFIDENCE:

            continue

        # ----------------------------------------------------

        # VALOR

        # ----------------------------------------------------

        implied = implied_probability(

            market["odd"]

        )

        value = (

            market_probability

            - implied

        )

        results.append({

            "fixture_id": fixture_id,

            "home": home,

            "away": away,

            "market": market[

                "market_name"

            ],

            "market_key": market[

                "market_key"

            ],

            "selection": market[

                "selection"

            ],

            "odd": market[

                "odd"

            ],

            "probability": market_probability,

            "api_probability": api_probability,

            "value": value,

            "confidence": confidence,

            "level": level_name,

            "level_icon": level_icon,

            "bookmakers": bookmaker_count,

            "alignment": alignment,

            "bookmaker": market[

                "bookmaker"

            ],

            "date": fixture[

                "fixture"

            ]["date"]

        })

    return results

# ============================================================

# ELIMINAR DUPLICADOS

# ============================================================

def remove_duplicates(

    results

):

    best = {}

    for item in results:

        key = (

            item["fixture_id"],

            item["market_key"],

            item["selection"]

        )

        if key not in best:

            best[key] = item

            continue

        current = best[key]

        # Primero confianza

        if (

            item["confidence"]

            > current["confidence"]

        ):

            best[key] = item

        # Si confianza igual,

        # conservar cuota mayor

        elif (

            item["confidence"]

            == current["confidence"]

            and item["odd"]

            > current["odd"]

        ):

            best[key] = item

    return list(

        best.values()

    )

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

            "disponibles."

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

    # CANDIDATOS

    # ========================================================

    candidates_with_odds = []

    for odds_item in odds_response:

        fixture_id = (

            odds_item

            .get(

                "fixture",

                {}

            )

            .get(

                "id"

            )

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

        markets = extract_markets(

            odds_item,

            home,

            away

        )

        if markets:

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

    # PRIORIZAR

    # ========================================================

    def priority(item):

        fixture, odds_item = item

        home = fixture[

            "teams"

        ]["home"]["name"]

        away = fixture[

            "teams"

        ]["away"]["name"]

        markets = extract_markets(

            odds_item,

            home,

            away

        )

        if not markets:

            return 999

        # Preferimos cuotas cercanas

        # al rango de alta probabilidad.

        return min(

            abs(

                market["odd"]

                - 1.60

            )

            for market in markets

        )

    candidates_with_odds.sort(

        key=priority

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

    # ANALISIS

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

                f'vs '

                f'{fixture["teams"]["away"]["name"]}: '

                f'{error}'

            )

    # ========================================================

    # DEDUPLICAR

    # ========================================================

    all_candidates = remove_duplicates(

        all_candidates

    )

    # ========================================================

    # ORDENAR POR CONFIANZA

    # ========================================================

    all_candidates.sort(

        key=lambda x: (

            x["confidence"],

            x["probability"],

            x["value"]

        ),

        reverse=True

    )

    print(

        "Apuestas que cumplen todos "

        "los filtros: "

        f"{len(all_candidates)}"

    )

    # Mostrar diagnóstico

    for pick in all_candidates[:10]:

        print(

            f'{pick["home"]} vs '

            f'{pick["away"]} | '

            f'{pick["selection"]} | '

            f'Cuota {pick["odd"]:.2f} | '

            f'Prob {pick["probability"]:.1%} | '

            f'Confianza {pick["confidence"]:.1f} | '

            f'{pick["level"]}'

        )

    # ========================================================

    # SIN APUESTAS

    # ========================================================

    if not all_candidates:

        telegram(

            "🤖 BOT DE APUESTAS\n\n"

            f"📅 {today}\n\n"

            "❌ No encontré apuestas "

            "con suficiente confianza.\n\n"

            f"💰 Cuota mínima: "

            f"{MIN_ODD:.2f}\n"

            f"📊 Probabilidad mínima: "

            f"{MIN_PROBABILITY:.0%}\n"

            f"⭐ Confianza mínima: "

            f"{MIN_CONFIDENCE}/100\n\n"

            "No se fuerza ninguna apuesta."

        )

        return

    # ========================================================

    # ALERTAS

    # ========================================================

    selected_alerts = all_candidates[

        :MAX_ALERTS

    ]

    # ========================================================

    # PRINCIPAL

    # ========================================================

    best = selected_alerts[0]

    match_time = datetime.fromisoformat(

        best["date"].replace(

            "Z",

            "+00:00"

        )

    ).astimezone(

        honduras

    )

    message = (

        "🔥 APUESTA PRINCIPAL\n\n"

        f"⚽ {best['home']} vs "

        f"{best['away']}\n\n"

        f"🎯 Mercado: "

        f"{best['market']}\n"

        f"✅ Selección: "

        f"{best['selection']}\n"

        f"💰 Cuota: "

        f"{best['odd']:.2f}\n\n"

        f"📊 Probabilidad consenso: "

        f"{best['probability']:.1%}\n"

        f"⭐ Confianza: "

        f"{best['confidence']:.0f}/100 "

        f"{best['level_icon']}\n"

        f"📈 Valor estimado: "

        f"{best['value']:.1%}\n"

        f"🏦 Mejor cuota: "

        f"{best['bookmaker']}\n"

        f"🔎 Bookmakers comparados: "

        f"{best['bookmakers']}\n"

        f"🕐 Hora Honduras: "

        f"{match_time.strftime('%H:%M')}\n"

    )

    if best["api_probability"] is not None:

        message += (

            f"\n🤖 Prob. API-Football: "

            f"{best['api_probability']:.1%}"

        )

    message += (

        "\n\n"

        "⚠️ Alta confianza estadística, "

        "no garantía de acierto."

    )

    telegram(

        message

    )

    # ========================================================

    # OTRAS

    # ========================================================

    if len(selected_alerts) > 1:

        extra = (

            "🔥 OTRAS OPORTUNIDADES\n"

        )

        for index, pick in enumerate(

            selected_alerts[1:],

            start=2

        ):

            extra += (

                f"\n{index}. "

                f"{pick['level_icon']} "

                f"{pick['home']} vs "

                f"{pick['away']}\n"

                f"🎯 {pick['market']}: "

                f"{pick['selection']}\n"

                f"💰 Cuota: "

                f"{pick['odd']:.2f}\n"

                f"📊 Prob.: "

                f"{pick['probability']:.1%}\n"

                f"⭐ Confianza: "

                f"{pick['confidence']:.0f}/100\n"

            )

        telegram(

            extra

        )

# ============================================================

# EJECUTAR

# ============================================================

if __name__ == "__main__":

    main()
