import os
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_URL = "https://v3.football.api-sports.io"

MIN_ODD = float(os.getenv("MIN_ODD", "1.50"))
MIN_PROBABILITY = float(os.getenv("MIN_PROBABILITY", "0.65"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))
MAX_PREDICTIONS = int(os.getenv("MAX_PREDICTIONS", "20"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.25"))

session = requests.Session()
session.headers.update({"x-apisports-key": API_KEY or ""})
cache = {}


def api_get(endpoint, params=None, retries=3):
    key = (endpoint, tuple(sorted((params or {}).items())))
    if key in cache:
        return cache[key]
    for attempt in range(retries):
        try:
            r = session.get(BASE_URL + "/" + endpoint, params=params or {}, timeout=30)
            if r.status_code == 429:
                wait = 2 + attempt * 3
                print(f"429 API-Football. Esperando {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                print("API error:", data["errors"])
                return []
            result = data.get("response", [])
            cache[key] = result
            time.sleep(REQUEST_DELAY)
            return result
        except Exception as e:
            if attempt == retries - 1:
                print("API error", endpoint, params, e)
                return []
            time.sleep(1 + attempt)
    return []


def telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram {r.status_code}: {r.text[:500]}")


def f(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


def implied(odd):
    return 1 / odd if odd and odd > 1 else 0


def pct(v):
    x = f(v)
    if x is None:
        return None
    if x > 1:
        x /= 100
    return max(0, min(1, x))


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


def odds_markets(odds_item, home, away):
    out = []
    for bookmaker in odds_item.get("bookmakers", []):
        bname = bookmaker.get("name", "Bookmaker")
        for bet in bookmaker.get("bets", []):
            mk = norm_market(bet.get("name"))
            if mk not in ("winner", "double_chance", "dnb"):
                continue
            for val in bet.get("values", []):
                odd = f(val.get("odd"))
                if odd is None or odd < MIN_ODD:
                    continue
                sel = norm_selection(val.get("value"))
                if sel:
                    out.append({
                        "market": mk, "market_name": bet.get("name", mk),
                        "selection": sel, "odd": odd, "bookmaker": bname,
                        "home": home, "away": away,
                    })
    return out


def get_prediction(fixture_id):
    data = api_get("predictions", {"fixture": fixture_id})
    if not data:
        return {}
    return data[0].get("predictions", {}) or {}


def winner_probs(pred):
    p = pred.get("percent", {}) if pred else {}
    return pct(p.get("home")), pct(p.get("draw")), pct(p.get("away"))


def market_consensus(markets, market, selection):
    """Normaliza el margen de cada bookmaker y promedia la selecciÃ³n."""
    vals = []
    for b in sorted({m["bookmaker"] for m in markets}):
        same = [m for m in markets if m["bookmaker"] == b and m["market"] == market]
        if not same:
            continue
        target = next((m for m in same if m["selection"] == selection), None)
        odds = [m["odd"] for m in same if m["odd"] > 1]
        if not target or len(odds) < 2:
            continue
        inv = sum(1 / o for o in odds)
        if inv > 0:
            vals.append((1 / target["odd"]) / inv)
    if vals:
        return sum(vals) / len(vals), len(vals)
    target = next((m for m in markets if m["market"] == market and m["selection"] == selection), None)
    return (implied(target["odd"]), 1) if target else (0, 0)


def api_market_probability(market, pred):
    h, d, a = winner_probs(pred)
    if h is None or d is None or a is None:
        return None
    sel = market["selection"].lower().strip()
    home = market["home"].lower().strip()
    away = market["away"].lower().strip()
    if market["market"] == "winner":
        if sel == home or sel == "home": return h
        if sel == away or sel == "away": return a
        if sel in ("draw", "tie"): return d
    if market["market"] == "double_chance":
        s = market["selection"].upper().replace(" ", "")
        if s == "1X": return h + d
        if s == "X2": return d + a
        if s == "12": return h + a
    if market["market"] == "dnb":
        if sel == home or sel == "home": return h / max(h + a, 1e-9)
        if sel == away or sel == "away": return a / max(h + a, 1e-9)
    return None


def select_best_odds(markets):
    best = {}
    for m in markets:
        key = (m["market"], m["selection"])
        if key not in best or m["odd"] > best[key]["odd"]:
            best[key] = m
    return list(best.values())


def analyze(fixture, odds_item):
    fid = fixture["fixture"]["id"]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    markets = odds_markets(odds_item, home, away)
    if not markets:
        return []
    pred = get_prediction(fid)
    h, d, a = winner_probs(pred)
    results = []

    for market in select_best_odds(markets):
        p_market, books = market_consensus(markets, market["market"], market["selection"])
        p_api = api_market_probability(market, pred)
        # Para doble oportunidad/DNB, la probabilidad principal viene del modelo API.
        # Para mercados sin modelo, usamos consenso.
        if p_api is not None:
            probability = 0.65 * p_api + 0.35 * p_market
        else:
            probability = p_market
        imp = implied(market["odd"])
        edge = probability - imp
        ev = probability * market["odd"] - 1

        if probability < MIN_PROBABILITY or edge < MIN_EDGE:
            continue

        # Penaliza si API y mercado difieren mucho.
        conflict = abs(p_api - p_market) if p_api is not None else 0
        confidence = probability * 100 + edge * 100 * 0.60
        if books >= 5: confidence += 3
        elif books >= 3: confidence += 2
        if p_api is not None and p_api >= p_market + 0.05: confidence += 4
        if conflict >= 0.12: confidence -= 8
        confidence = max(0, min(100, confidence))
        if confidence < 70:
            continue

        scenarios = 2 if market["market"] in ("double_chance", "dnb") else 1
        results.append({
            "fixture_id": fid, "home": home, "away": away,
            "market": market, "probability": probability,
            "implied": imp, "edge": edge, "ev": ev,
            "api_probability": p_api, "market_probability": p_market,
            "bookmakers": books, "confidence": confidence,
            "scenarios": scenarios, "date": fixture["fixture"]["date"],
        })
    return results


def level(x):
    if x >= 85: return "ð¥ MUY FUERTE"
    if x >= 78: return "ð¢ FUERTE"
    return "ð¡ BUENA"


def alert(item, main=True):
    m = item["market"]
    sel = m["selection"]
    if m["market"] == "double_chance" and sel.upper() == "1X":
        scenarios = "ð¢ Local gana â GANA\nð¢ Empate â GANA\nð´ Visitante gana â PIERDE"
    elif m["market"] == "double_chance" and sel.upper() == "X2":
        scenarios = "ð´ Local gana â PIERDE\nð¢ Empate â GANA\nð¢ Visitante gana â GANA"
    elif m["market"] == "dnb":
        scenarios = "ð¢ Victoria del seleccionado â GANA\nð¡ Empate â DEVOLUCIÃN\nð´ Derrota â PIERDE"
    else:
        scenarios = "ð¯ Se necesita acertar el resultado seleccionado."

    dt = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
    honduras = dt.astimezone(timezone(timedelta(hours=-6)))
    title = "ð¥ ALERTA DE VALOR" if main else "ð OTRA OPORTUNIDAD"
    return (
        f"<b>{title}</b>\n\n"
        f"â½ <b>{item['home']} vs {item['away']}</b>\n"
        f"ð¯ <b>{m['market_name']}: {sel}</b>\n"
        f"ð° Cuota: <b>{m['odd']:.2f}</b>\n\n"
        f"ð Prob. estimada: <b>{item['probability']:.1%}</b>\n"
        f"ð Prob. implÃ­cita: {item['implied']:.1%}\n"
        f"ð Ventaja: <b>+{item['edge']:.1%}</b>\n"
        f"ðµ EV estimado: +{item['ev']:.1%}\n"
        f"â­ Confianza: <b>{item['confidence']:.0f}/100</b> {level(item['confidence'])}\n\n"
        f"{scenarios}\n\n"
        f"ð¦ Cuota de: {m['bookmaker']}\n"
        f"ð Bookmakers comparados: {item['bookmakers']}\n"
        f"ð Honduras: {honduras.strftime('%H:%M')}\n\n"
        "â ï¸ El bot informa una oportunidad; tÃº decides si haces el pick."
    )


def main():
    if not API_KEY or not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("Falta uno de los 3 Secrets: API_FOOTBALL_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")

    honduras = timezone(timedelta(hours=-6))
    today = datetime.now(honduras).strftime("%Y-%m-%d")
    print("Fecha Honduras:", today)

    fixtures = api_get("fixtures", {"date": today})
    fixture_map = {}
    for fx in fixtures:
        if fx.get("fixture", {}).get("status", {}).get("short") in ("NS", "TBD"):
            fixture_map[fx["fixture"]["id"]] = fx
    print("Partidos pendientes:", len(fixture_map))

    odds = api_get("odds", {"date": today})
    print("Registros de cuotas:", len(odds))
    if not odds:
        telegram(f"ð¤ <b>ESCANEO COMPLETADO</b>\n\nð {today}\nâ ï¸ API-Football no devolviÃ³ cuotas para hoy.")
        return

    candidates = []
    for oi in odds:
        fid = oi.get("fixture", {}).get("id")
        if fid not in fixture_map:
            continue
        fx = fixture_map[fid]
        markets = odds_markets(oi, fx["teams"]["home"]["name"], fx["teams"]["away"]["name"])
        if markets:
            # Priorizamos partidos con doble oportunidad disponible; luego cuotas >=1.50.
            has_dc = any(m["market"] == "double_chance" for m in markets)
            candidates.append((0 if has_dc else 1, -max(m["odd"] for m in markets), fx, oi))
    candidates.sort(key=lambda x: (x[0], x[1]))
    candidates = candidates[:MAX_PREDICTIONS]
    print("Partidos a analizar con predictions:", len(candidates))

    results = []
    for _, _, fx, oi in candidates:
        try:
            results.extend(analyze(fx, oi))
        except Exception as e:
            print("Error analizando", fx["fixture"]["id"], e)

    # Un solo pick por partido: el mejor valor/riesgo.
    results.sort(key=lambda x: (x["scenarios"], x["confidence"], x["edge"], x["probability"]), reverse=True)
    selected = []
    used = set()
    for r in results:
        if r["fixture_id"] in used:
            continue
        selected.append(r)
        used.add(r["fixture_id"])
        if len(selected) >= MAX_ALERTS:
            break

    print("Oportunidades encontradas:", len(results))
    print("Alertas seleccionadas:", len(selected))

    if not selected:
        telegram(
            f"ð¤ <b>ESCANEO COMPLETADO</b>\n\n"
            f"ð {today}\n\n"
            "â No encontrÃ© una oportunidad que supere todos los filtros.\n\n"
            f"ð° Cuota mÃ­nima: {MIN_ODD:.2f}\n"
            f"ð Probabilidad mÃ­nima: {MIN_PROBABILITY:.0%}\n"
            f"ð Edge mÃ­nimo: +{MIN_EDGE:.0%}\n\n"
            "No se fuerza ningÃºn pick."
        )
        return

    for i, item in enumerate(selected):
        telegram(alert(item, main=(i == 0)))


if __name__ == "__main__":
    main()
