import os

import time

import requests

from datetime import datetime, timezone, timedelta

# ============================================================

# CONFIGURACIÓN

# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

MIN_ODD = float(os.getenv("MIN_ODD", "1.50"))

MIN_PROBABILITY = float(os.getenv("MIN_PROBABILITY", "0.65"))

MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))

# IMPORTANTE:

# Antes eran hasta 20 predictions por ejecución.

# Ahora máximo 5 para reducir mucho el consumo de API.

MAX_PREDICTIONS = int(os.getenv("MAX_PREDICTIONS", "5"))

MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))

REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.25"))

# ============================================================

# SESIÓN Y CONTROL DE API

# ============================================================

session = requests.Session()

session.headers.update({

    "x-apisports-key": API_KEY or ""

})

cache = {}

# Si API-Football informa que se agotó la cuota diaria,

# dejamos de realizar llamadas inmediatamente.

API_DAILY_LIMIT_REACHED = False

# ============================================================

# API-FOOTBALL

# ============================================================

def api_get(endpoint, params=None, retries=2):

    global API_DAILY_LIMIT_REACHED

    if API_DAILY_LIMIT_REACHED:

        print("API bloqueada temporalmente: límite diario alcanzado.")

        return []

    key = (endpoint, tuple(sorted((params or {}).items())))

    if key in cache:

        return cache[key]

    for attempt in range(retries):

        try:

            r = session.get(

                BASE_URL + "/" + endpoint,

                params=params or {},

                timeout=30

            )

            # ------------------------------------------------

            # HTTP 429

            # ------------------------------------------------

            if r.status_code == 429:

                try:

                    data = r.json()

                except Exception:

                    data = {}

                errors = data.get("errors", {})

                if errors:

                    error_text = str(errors).lower()

                    if (

                        "limit" in error_text

                        or "request limit" in error_text

                        or "daily" in error_text

                    ):

                        API_DAILY_LIMIT_REACHED = True

                        print("API-Football: límite diario alcanzado.")

                        print("No se realizarán más solicitudes.")

                        return []

                wait = 3 + attempt * 3

                print(f"429 API-Football. Esperando {wait}s...")

                time.sleep(wait)

                continue

            # ------------------------------------------------

            # Otros errores HTTP

            # ------------------------------------------------

            r.raise_for_status()

            data = r.json()

            # ------------------------------------------------

            # Errores devueltos dentro del JSON

            # ------------------------------------------------

            if data.get("errors"):

                errors = data["errors"]

                error_text = str(errors).lower()

                print("API error:", errors)

                if (

                    "limit" in error_text

                    or "request limit" in error_text

                    or "daily" in error_text

                ):

                    API_DAILY_LIMIT_REACHED = True

                    print("Límite diario alcanzado.")

                    print("No se realizarán más solicitudes.")

                    return []

                return []

            result = data.get("response", [])

            cache[key] = result

            time.sleep(REQUEST_DELAY)

            return result

        except requests.RequestException as e:

            if attempt == retries - 1:

                print(

                    "API error:",

                    endpoint,

                    params,

                    str(e)

                )

                return []

            wait = 1 + attempt

            print(

                f"Error de conexión. Reintentando en {wait}s..."

            )

            time.sleep(wait)

        except Exception as e:

            print(

                "Error inesperado API:",

                endpoint,

                params,

                str(e)

            )

            return []

    return []

# ============================================================

# TELEGRAM

# ============================================================

def telegram(message):

    if not TELEGRAM_TOKEN or not CHAT_ID:

        raise RuntimeError(

            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID"

        )

    url = (

        f"https://api.telegram.org/"

        f"bot{TELEGRAM_TOKEN}/sendMessage"

    )

    r = requests.post(

        url,

        json={

            "chat_id": CHAT_ID,

            "text": message,

            "parse_mode": "HTML",

            "disable_web_page_preview": True,

        },

        timeout=30

    )

    if not r.ok:

        raise RuntimeError(

            f"Telegram {r.status_code}: {r.text[:500]}"

        )

# ============================================================

# UTILIDADES

# ============================================================

def f(v, default=None):

    try:

        return float(v)

    except Exception:

        return default

def implied(odd):

    if odd and odd > 1:

        return 1 / odd

    return 0

def pct(v):

    x = f(v)

    if x is None:

        return None

    if x > 1:

        x /= 100

    return max(0, min(1, x))

# ============================================================

# NORMALIZACIÓN DE MERCADOS

# ============================================================

def norm_market(name):

    s = str(name or "").lower().strip()

    if "double chance" in s:

        return "double_chance"

    if "match winner" in s or s in ("1x2", "winner"):

        return "winner"

    if "draw no bet" in s:

        return "dnb"

    return s

def norm_selection(value):

    return str(value or "").strip()

# ============================================================

# EXTRAER CUOTAS

# ============================================================

def odds_markets(odds_item, home, away):

    out = []

    for bookmaker in odds_item.get("bookmakers", []):

        bname = bookmaker.get(

            "name",

            "Bookmaker"

        )

        for bet in bookmaker.get("bets", []):

            mk = norm_market(

                bet.get("name")

            )

            if mk not in (

                "winner",

                "double_chance",

                "dnb"

            ):

                continue

            for val in bet.get("values", []):

                odd = f(val.get("odd"))

                if odd is None or odd < MIN_ODD:

                    continue

                sel = norm_selection(

                    val.get("value")

                )

                if not sel:

                    continue

                out.append({

                    "market": mk,

                    "market_name": bet.get(

                        "name",

                        mk

                    ),

                    "selection": sel,

                    "odd": odd,

                    "bookmaker": bname,

                    "home": home,

                    "away": away,

                })

    return out

# ============================================================

# PREDICTIONS

# ============================================================

def get_prediction(fixture_id):

    data = api_get(

        "predictions",

        {"fixture": fixture_id}

    )

    if not data:

        return {}

    return data[0].get(

        "predictions",

        {}

    ) or {}

def winner_probs(pred):

    p = pred.get(

        "percent",

        {}

    ) if pred else {}

    return (

        pct(p.get("home")),

        pct(p.get("draw")),

        pct(p.get("away"))

    )

# ============================================================

# PROBABILIDAD DEL MERCADO

# ============================================================

def market_consensus(

    markets,

    market,

    selection

):

    vals = []

    bookmakers = sorted(

        {

            m["bookmaker"]

            for m in markets

        }

    )

    for b in bookmakers:

        same = [

            m for m in markets

            if (

                m["bookmaker"] == b

                and m["market"] == market

            )

        ]

        if not same:

            continue

        target = next(

            (

                m for m in same

                if m["selection"] == selection

            ),

            None

        )

        odds = [

            m["odd"]

            for m in same

            if m["odd"] > 1

        ]

        if not target or len(odds) < 2:

            continue

        inv = sum(

            1 / o

            for o in odds

        )

        if inv > 0:

            vals.append(

                (1 / target["odd"]) / inv

            )

    if vals:

        return (

            sum(vals) / len(vals),

            len(vals)

        )

    target = next(

        (

            m for m in markets

            if (

                m["market"] == market

                and m["selection"] == selection

            )

        ),

        None

    )

    if target:

        return (

            implied(target["odd"]),

            1

        )

    return 0, 0

def api_market_probability(

    market,

    pred

):

    h, d, a = winner_probs(pred)

    if h is None or d is None or a is None:

        return None

    sel = (

        market["selection"]

        .lower()

        .strip()

    )

    home = (

        market["home"]

        .lower()

        .strip()

    )

    away = (

        market["away"]

        .lower()

        .strip()

    )

    if market["market"] == "winner":

        if sel == home or sel == "home":

            return h

        if sel == away or sel == "away":

            return a

        if sel in ("draw", "tie"):

            return d

    if market["market"] == "double_chance":

        s = (

            market["selection"]

            .upper()

            .replace(" ", "")

        )

        if s == "1X":

            return h + d

        if s == "X2":

            return d + a

        if s == "12":

            return h + a

    if market["market"] == "dnb":

        if sel == home or sel == "home":

            return h / max(

                h + a,

                1e-9

            )

        if sel == away or sel == "away":

            return a / max(

                h + a,

                1e-9

            )

    return None

# ============================================================

# MEJORES CUOTAS

# ============================================================

def select_best_odds(markets):

    best = {}

    for m in markets:

        key = (

            m["market"],

            m["selection"]

        )

        if (

            key not in best

            or m["odd"] > best[key]["odd"]

        ):

            best[key] = m

    return list(best.values())

# ============================================================

# ANALIZAR PARTIDO

# ============================================================

def analyze(

    fixture,

    odds_item

):

    fid = fixture["fixture"]["id"]

    home = fixture["teams"]["home"]["name"]

    away = fixture["teams"]["away"]["name"]

    markets = odds_markets(

        odds_item,

        home,

        away

    )

    if not markets:

        return []

    # --------------------------------------------------------

    # UNA SOLA CONSULTA DE PREDICTIONS POR PARTIDO

    # --------------------------------------------------------

    pred = get_prediction(fid)

    h, d, a = winner_probs(pred)

    results = []

    for market in select_best_odds(markets):

        p_market, books = market_consensus(

            markets,

            market["market"],

            market["selection"]

        )

        p_api = api_market_probability(

            market,

            pred

        )

        if p_api is not None:

            probability = (

                0.65 * p_api

                + 0.35 * p_market

            )

        else:

            probability = p_market

        imp = implied(

            market["odd"]

        )

        edge = probability - imp

        ev = (

            probability

            * market["odd"]

            - 1

        )

        if probability < MIN_PROBABILITY:

            continue

        if edge < MIN_EDGE:

            continue

        # ----------------------------------------------------

        # CONFIANZA

        # ----------------------------------------------------

        conflict = (

            abs(p_api - p_market)

            if p_api is not None

            else 0

        )

        confidence = (

            probability * 100

            + edge * 100 * 0.60

        )

        if books >= 5:

            confidence += 3

        elif books >= 3:

            confidence += 2

        if (

            p_api is not None

            and p_api >= p_market + 0.05

        ):

            confidence += 4

        if conflict >= 0.12:

            confidence -= 8

        confidence = max(

            0,

            min(100, confidence)

        )

        if confidence < 70:

            continue

        scenarios = (

            2

            if market["market"]

            in ("double_chance", "dnb")

            else 1

        )

        results.append({

            "fixture_id": fid,

            "home": home,

            "away": away,

            "market": market,

            "probability": probability,

            "implied": imp,

            "edge": edge,

            "ev": ev,

            "api_probability": p_api,

            "market_probability": p_market,

            "bookmakers": books,

            "confidence": confidence,

            "scenarios": scenarios,

            "date": fixture["fixture"]["date"],

        })

    return results

# ============================================================

# NIVEL DE CONFIANZA

# ============================================================

def level(x):

    if x >= 85:

        return "🔥 MUY FUERTE"

    if x >= 78:

        return "🟢 FUERTE"

    return "🟡 BUENA"

# ============================================================

# MENSAJE TELEGRAM

# ============================================================

def alert(

    item,

    main=True

):

    m = item["market"]

    sel = m["selection"]

    if (

        m["market"] == "double_chance"

        and sel.upper() == "1X"

    ):

        scenarios = (

            "🟢 Local gana → GANA\n"

            "🟢 Empate → GANA\n"

            "🔴 Visitante gana → PIERDE"

        )

    elif (

        m["market"] == "double_chance"

        and sel.upper() == "X2"

    ):

        scenarios = (

            "🔴 Local gana → PIERDE\n"

            "🟢 Empate → GANA\n"

            "🟢 Visitante gana → GANA"

        )

    elif m["market"] == "dnb":

        scenarios = (

            "🟢 Victoria del seleccionado → GANA\n"

            "🟡 Empate → DEVOLUCIÓN\n"

            "🔴 Derrota → PIERDE"

        )

    else:

        scenarios = (

            "🎯 Se necesita acertar "

            "el resultado seleccionado."

        )

    dt = datetime.fromisoformat(

        item["date"].replace(

            "Z",

            "+00:00"

        )

    )

    honduras = dt.astimezone(

        timezone(timedelta(hours=-6))

    )

    title = (

        "🔥 ALERTA DE VALOR"

        if main

        else

        "📌 OTRA OPORTUNIDAD"

    )

    return (

        f"<b>{title}</b>\n\n"

        f"⚽ <b>{item['home']} "

        f"vs {item['away']}</b>\n"

        f"🎯 <b>{m['market_name']}: "

        f"{sel}</b>\n"

        f"💰 Cuota: "

        f"<b>{m['odd']:.2f}</b>\n\n"

        f"📊 Prob. estimada: "

        f"<b>{item['probability']:.1%}</b>\n"

        f"📉 Prob. implícita: "

        f"{item['implied']:.1%}\n"

        f"📈 Ventaja: "

        f"<b>+{item['edge']:.1%}</b>\n"

        f"💵 EV estimado: "

        f"+{item['ev']:.1%}\n"

        f"⭐ Confianza: "

        f"<b>{item['confidence']:.0f}/100</b> "

        f"{level(item['confidence'])}\n\n"

        f"{scenarios}\n\n"

        f"🏦 Cuota de: "

        f"{m['bookmaker']}\n"

        f"🔎 Bookmakers comparados: "

        f"{item['bookmakers']}\n"

        f"🕐 Honduras: "

        f"{honduras.strftime('%H:%M')}\n\n"

        "⚠️ El bot informa una oportunidad; "

        "tú decides si haces el pick."

    )

# ============================================================

# PROGRAMA PRINCIPAL

# ============================================================

def main():

    if (

        not API_KEY

        or not TELEGRAM_TOKEN

        or not CHAT_ID

    ):

        raise RuntimeError(

            "Falta uno de los 3 Secrets: "

            "API_FOOTBALL_KEY, "

            "TELEGRAM_BOT_TOKEN, "

            "TELEGRAM_CHAT_ID"

        )

    honduras = timezone(

        timedelta(hours=-6)

    )

    today = datetime.now(

        honduras

    ).strftime("%Y-%m-%d")

    print(

        "Fecha Honduras:",

        today

    )

    # ========================================================

    # 1. PARTIDOS

    # ========================================================

    fixtures = api_get(

        "fixtures",

        {"date": today}

    )

    # Si la API está sin cuota, no seguimos.

    if API_DAILY_LIMIT_REACHED:

        telegram(

            "🤖 <b>BOT DETENIDO</b>\n\n"

            "⚠️ API-Football informó que "

            "se alcanzó el límite diario.\n\n"

            "El bot no realizará más "

            "consultas en esta ejecución."

        )

        return

    fixture_map = {}

    for fx in fixtures:

        status = (

            fx.get("fixture", {})

            .get("status", {})

            .get("short")

        )

        if status in ("NS", "TBD"):

            fixture_map[

                fx["fixture"]["id"]

            ] = fx

    print(

        "Partidos pendientes:",

        len(fixture_map)

    )

    # ========================================================

    # 2. CUOTAS

    # ========================================================

    odds = api_get(

        "odds",

        {"date": today}

    )

    if API_DAILY_LIMIT_REACHED:

        telegram(

            "🤖 <b>BOT DETENIDO</b>\n\n"

            "⚠️ API-Football agotó "

            "el límite diario al consultar "

            "las cuotas.\n\n"

            "No se hicieron más solicitudes."

        )

        return

    print(

        "Registros de cuotas:",

        len(odds)

    )

    if not odds:

        telegram(

            f"🤖 <b>ESCANEO COMPLETADO</b>\n\n"

            f"📅 {today}\n"

            "⚠️ API-Football no devolvió "

            "cuotas para hoy."

        )

        return

    # ========================================================

    # 3. PRESELECCIÓN

    # ========================================================

    candidates = []

    for oi in odds:

        fid = (

            oi.get("fixture", {})

            .get("id")

        )

        if fid not in fixture_map:

            continue

        fx = fixture_map[fid]

        markets = odds_markets(

            oi,

            fx["teams"]["home"]["name"],

            fx["teams"]["away"]["name"]

        )

        if not markets:

            continue

        has_dc = any(

            m["market"]

            == "double_chance"

            for m in markets

        )

        candidates.append(

            (

                0 if has_dc else 1,

                -max(

                    m["odd"]

                    for m in markets

                ),

                fx,

                oi

            )

        )

    candidates.sort(

        key=lambda x: (

            x[0],

            x[1]

        )

    )

    # ========================================================

    # IMPORTANTE:

    # SOLO 5 PARTIDOS CON PREDICTIONS

    # ========================================================

    candidates = candidates[

        :MAX_PREDICTIONS

    ]

    print(

        "Partidos a analizar con predictions:",

        len(candidates)

    )

    # ========================================================

    # 4. ANALIZAR

    # ========================================================

    results = []

    for _, _, fx, oi in candidates:

        if API_DAILY_LIMIT_REACHED:

            print(

                "Límite API alcanzado. "

                "Se detiene el análisis."

            )

            break

        try:

            results.extend(

                analyze(

                    fx,

                    oi

                )

            )

        except Exception as e:

            print(

                "Error analizando",

                fx["fixture"]["id"],

                e

            )

    # ========================================================

    # 5. SELECCIONAR MEJORES

    # ========================================================

    results.sort(

        key=lambda x: (

            x["scenarios"],

            x["confidence"],

            x["edge"],

            x["probability"]

        ),

        reverse=True

    )

    selected = []

    used = set()

    for r in results:

        if r["fixture_id"] in used:

            continue

        selected.append(r)

        used.add(

            r["fixture_id"]

        )

        if len(selected) >= MAX_ALERTS:

            break

    print(

        "Oportunidades encontradas:",

        len(results)

    )

    print(

        "Alertas seleccionadas:",

        len(selected)

    )

    # ========================================================

    # 6. SIN ALERTAS

    # ========================================================

    if not selected:

        telegram(

            f"🤖 <b>ESCANEO COMPLETADO</b>\n\n"

            f"📅 {today}\n\n"

            "❌ No encontré una oportunidad "

            "que supere todos los filtros.\n\n"

            f"💰 Cuota mínima: "

            f"{MIN_ODD:.2f}\n"

            f"📊 Probabilidad mínima: "

            f"{MIN_PROBABILITY:.0%}\n"

            f"📈 Edge mínimo: "

            f"+{MIN_EDGE:.0%}\n\n"

            "No se fuerza ningún pick."

        )

        return

    # ========================================================

    # 7. ENVIAR ALERTAS

    # ========================================================

    for i, item in enumerate(selected):

        telegram(

            alert(

                item,

                main=(i == 0)

            )

        )

# ============================================================

# EJECUCIÓN

# ============================================================

if __name__ == "__main__":

    main()
