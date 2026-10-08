import os
import time
import math
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

# ============================================================
# BOT FUTBOL ALERTAS - SCANNER DE PICKS DE ALTA CONFIANZA
# Mercados: 1X2, Over 2.5, BTTS Sí
# Fuente: API-Football
# ============================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"

# ---------------- CONFIGURACION ----------------
MIN_ODD = 1.50
MIN_PROBABILITY = 0.65       # 65% minimo
MIN_EDGE = 0.05              # 5 puntos porcentuales
MIN_SAMPLE = 5
MAX_SAMPLE = 10
MAX_CANDIDATES = 25          # limita consumo de API
MAX_ALERTS = 5
REQUEST_DELAY = 0.25
TIMEOUT = 20
MAX_RETRIES = 3

# Para ser selectivo: la confianza final debe superar este nivel.
MIN_FINAL_SCORE = 70

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or "",
    "Accept": "application/json",
})

cache = {}
requests_made = 0


# ============================================================
# UTILIDADES
# ============================================================

def log(msg):
    print(msg, flush=True)


def api_get(endpoint, params=None):
    global requests_made

    key = (endpoint, tuple(sorted((params or {}).items())))
    if key in cache:
        return cache[key]

    if not API_KEY:
        raise RuntimeError("Falta API_FOOTBALL_KEY")

    url = BASE_URL + endpoint

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            requests_made += 1
            r = session.get(url, params=params or {}, timeout=TIMEOUT)

            if r.status_code == 429:
                log("⚠️ API: límite/429. Se detiene el consumo.")
                raise RuntimeError("API 429 / límite de solicitudes")

            r.raise_for_status()
            data = r.json()

            errors = data.get("errors")
            if errors:
                # API-Football puede devolver errores como dict o lista.
                raise RuntimeError(f"API error: {errors}")

            cache[key] = data
            time.sleep(REQUEST_DELAY)
            return data

        except RuntimeError:
            raise
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Error API {endpoint}: {e}")
            time.sleep(1.0 * attempt)

    raise RuntimeError("Error desconocido de API")


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("⚠️ Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if not r.ok:
            log(f"⚠️ Telegram error: {r.text[:300]}")
            return False
        return True
    except Exception as e:
        log(f"⚠️ Telegram exception: {e}")
        return False


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def pct(value):
    return f"{value * 100:.0f}%"


def avg(values):
    return sum(values) / len(values) if values else None


# ============================================================
# FECHA HONDURAS (UTC-6)
# ============================================================

def honduras_now():
    return datetime.now(timezone.utc) - timedelta(hours=6)


# ============================================================
# PARTIDOS DEL DIA
# ============================================================

def get_today_fixtures(date_str):
    data = api_get("/fixtures", {"date": date_str})
    return data.get("response", [])


# ============================================================
# CUOTAS
# ============================================================

def extract_odds(odds_response):
    """
    Convierte la respuesta /odds en:
    fixture_id -> lista de mercados normalizados.

    Solo toma:
      1X2
      Over 2.5
      BTTS Sí
    """
    result = defaultdict(dict)

    for item in odds_response:
        fixture = item.get("fixture") or {}
        fixture_id = fixture.get("id")
        if not fixture_id:
            continue

        bookmakers = item.get("bookmakers") or []

        for bookmaker in bookmakers:
            bets = bookmaker.get("bets") or []

            for bet in bets:
                name = (bet.get("name") or "").strip().lower()
                values = bet.get("values") or []

                # 1X2
                if name in ("match winner", "1x2"):
                    for v in values:
                        label = (v.get("value") or "").strip().lower()
                        odd = safe_float(v.get("odd"))
                        if odd and odd >= MIN_ODD:
                            if label in ("home", "1"):
                                result[fixture_id]["home"] = max(
                                    result[fixture_id].get("home", 0), odd
                                )
                            elif label in ("draw", "x"):
                                result[fixture_id]["draw"] = max(
                                    result[fixture_id].get("draw", 0), odd
                                )
                            elif label in ("away", "2"):
                                result[fixture_id]["away"] = max(
                                    result[fixture_id].get("away", 0), odd
                                )

                # Over/Under
                elif name in ("goals over/under", "total goals"):
                    for v in values:
                        label = (v.get("value") or "").strip().lower()
                        odd = safe_float(v.get("odd"))
                        if odd and odd >= MIN_ODD:
                            # Algunas casas devuelven "Over 2.5",
                            # otras "Over" + handicap separado.
                            if "over 2.5" in label:
                                result[fixture_id]["over25"] = max(
                                    result[fixture_id].get("over25", 0), odd
                                )

                # BTTS
                elif name in ("both teams score", "btts"):
                    for v in values:
                        label = (v.get("value") or "").strip().lower()
                        odd = safe_float(v.get("odd"))
                        if odd and odd >= MIN_ODD and label in ("yes", "sí", "si"):
                            result[fixture_id]["btts"] = max(
                                result[fixture_id].get("btts", 0), odd
                            )

    return result


def get_today_odds(date_str):
    data = api_get("/odds", {"date": date_str})
    return extract_odds(data.get("response", []))


# ============================================================
# HISTORIAL LOCAL / VISITANTE
# ============================================================

def get_team_recent(team_id):
    data = api_get("/fixtures", {
        "team": team_id,
        "last": MAX_SAMPLE,
    })
    return data.get("response", [])


def fixture_finished(fx):
    status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
    return status in {
        "FT", "AET", "PEN"
    }


def team_match_result(fx, team_id):
    teams = fx.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    goals = fx.get("goals") or {}

    hg = goals.get("home")
    ag = goals.get("away")

    if hg is None or ag is None:
        return None

    if home.get("id") == team_id:
        gf, ga = hg, ag
        venue = "home"
    elif away.get("id") == team_id:
        gf, ga = ag, hg
        venue = "away"
    else:
        return None

    return {
        "gf": float(gf),
        "ga": float(ga),
        "venue": venue,
        "win": 1 if gf > ga else 0,
        "draw": 1 if gf == ga else 0,
        "loss": 1 if gf < ga else 0,
        "over25": 1 if (gf + ga) >= 3 else 0,
        "btts": 1 if gf > 0 and ga > 0 else 0,
    }


def split_home_away(fixtures, team_id):
    home = []
    away = []

    for fx in fixtures:
        if not fixture_finished(fx):
            continue

        item = team_match_result(fx, team_id)
        if not item:
            continue

        if item["venue"] == "home":
            home.append(item)
        else:
            away.append(item)

    return home, away


def summarize(matches):
    if len(matches) < MIN_SAMPLE:
        return None

    return {
        "n": len(matches),
        "gf": avg([x["gf"] for x in matches]),
        "ga": avg([x["ga"] for x in matches]),
        "win": avg([x["win"] for x in matches]),
        "draw": avg([x["draw"] for x in matches]),
        "loss": avg([x["loss"] for x in matches]),
        "over25": avg([x["over25"] for x in matches]),
        "btts": avg([x["btts"] for x in matches]),
    }


def get_context(team_id, team_name):
    fixtures = get_team_recent(team_id)
    home, away = split_home_away(fixtures, team_id)

    return {
        "team": team_name,
        "home": summarize(home[:MAX_SAMPLE]),
        "away": summarize(away[:MAX_SAMPLE]),
    }


# ============================================================
# MODELO DE PROBABILIDADES
# ============================================================

def poisson_prob_over25(lam):
    """
    P(total >= 3) para Poisson con lambda de goles.
    """
    if lam is None or lam <= 0:
        return None

    p0 = math.exp(-lam)
    p1 = p0 * lam
    p2 = p1 * lam / 2
    return max(0.0, min(1.0, 1 - (p0 + p1 + p2)))


def poisson_prob_btts(lam_home, lam_away):
    if lam_home is None or lam_away is None:
        return None

    p_home_zero = math.exp(-lam_home)
    p_away_zero = math.exp(-lam_away)
    p_both = 1 - p_home_zero - p_away_zero + math.exp(-(lam_home + lam_away))

    return max(0.0, min(1.0, p_both))


def match_probabilities(home_ctx, away_ctx):
    h = home_ctx["home"]
    a = away_ctx["away"]

    if not h or not a:
        return None

    # Goles esperados basados en ataque propio + defensa rival.
    lambda_home = (h["gf"] + a["ga"]) / 2
    lambda_away = (a["gf"] + h["ga"]) / 2

    # Evita modelos extremos con muestras pequeñas.
    lambda_home = max(0.15, min(4.0, lambda_home))
    lambda_away = max(0.15, min(4.0, lambda_away))

    total = lambda_home + lambda_away

    # Poisson 1X2
    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    # Distribución hasta 8 goles por equipo.
    def pois(k, lam):
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    for hg in range(0, 9):
        for ag in range(0, 9):
            p = pois(hg, lambda_home) * pois(ag, lambda_away)
            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

    over25 = poisson_prob_over25(total)
    btts = poisson_prob_btts(lambda_home, lambda_away)

    # Ajuste conservador usando frecuencia real de los últimos partidos.
    over25_adj = 0.65 * over25 + 0.35 * avg([h["over25"], a["over25"]])
    btts_adj = 0.65 * btts + 0.35 * avg([h["btts"], a["btts"]])

    # Ajuste 1X2 hacia la evidencia de resultados recientes.
    recent_home_strength = h["win"]
    recent_away_strength = a["win"]

    # Pequeño ajuste, nunca domina al modelo de goles.
    home_win = 0.85 * home_win + 0.15 * recent_home_strength
    away_win = 0.85 * away_win + 0.15 * recent_away_strength

    total_1x2 = home_win + draw + away_win
    home_win /= total_1x2
    draw /= total_1x2
    away_win /= total_1x2

    return {
        "home": home_win,
        "draw": draw,
        "away": away_win,
        "over25": max(0, min(1, over25_adj)),
        "btts": max(0, min(1, btts_adj)),
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "total_goals": total,
    }


# ============================================================
# GENERACION Y RANKING DE PICKS
# ============================================================

def make_pick(match, probs, odds):
    home_name = ((match.get("teams") or {}).get("home") or {}).get("name", "Local")
    away_name = ((match.get("teams") or {}).get("away") or {}).get("name", "Visitante")

    candidates = [
        ("1X2", "Local", probs["home"], odds.get("home")),
        ("1X2", "Empate", probs["draw"], odds.get("draw")),
        ("1X2", "Visitante", probs["away"], odds.get("away")),
        ("Over 2.5", "Over 2.5 goles", probs["over25"], odds.get("over25")),
        ("BTTS", "BTTS — Sí", probs["btts"], odds.get("btts")),
    ]

    valid = []

    for market, pick, probability, odd in candidates:
        if not odd or odd < MIN_ODD or probability < MIN_PROBABILITY:
            continue

        implied = 1 / odd
        edge = probability - implied

        if edge < MIN_EDGE:
            continue

        # Score 0-100:
        # probabilidad pesa más; edge y cuota aportan valor.
        score = (
            probability * 100 * 0.65
            + min(edge * 100, 20) * 1.5
            + min(max(odd - 1.50, 0), 1.0) * 5
        )

        valid.append({
            "market": market,
            "pick": pick,
            "probability": probability,
            "odd": odd,
            "implied": implied,
            "edge": edge,
            "score": score,
            "home": home_name,
            "away": away_name,
            "lambda_home": probs["lambda_home"],
            "lambda_away": probs["lambda_away"],
        })

    if not valid:
        return None

    # Solo el mercado más lógico del partido.
    valid.sort(key=lambda x: (x["score"], x["edge"], x["probability"]), reverse=True)
    best = valid[0]

    if best["score"] < MIN_FINAL_SCORE:
        return None

    return best


def reason_for_pick(pick, home_ctx, away_ctx):
    h = home_ctx["home"]
    a = away_ctx["away"]

    if pick["market"] == "1X2":
        if pick["pick"] == "Local":
            return (
                f"Localía + promedio GF local {h['gf']:.2f} + "
                f"GA visitante {a['ga']:.2f}; victoria local en {pct(h['win'])} "
                f"de su muestra local."
            )
        if pick["pick"] == "Visitante":
            return (
                f"Rendimiento visitante + promedio GF fuera {a['gf']:.2f} + "
                f"GA local {h['ga']:.2f}; victoria visitante en {pct(a['win'])} "
                f"de su muestra fuera."
            )
        return (
            f"Los datos de local/visitante muestran una alta frecuencia de empate; "
            f"el modelo estima {pct(pick['probability'])}."
        )

    if pick["market"] == "Over 2.5":
        return (
            f"Proyección de {pick['lambda_home'] + pick['lambda_away']:.2f} goles; "
            f"Over 2.5 frecuente: local {pct(h['over25'])}, visitante {pct(a['over25'])}."
        )

    return (
        f"Ambos marcan frecuente: local {pct(h['btts'])}, visitante {pct(a['btts'])}; "
        f"proyección de goles {pick['lambda_home']:.2f}–{pick['lambda_away']:.2f}."
    )


# ============================================================
# MENSAJE TELEGRAM
# ============================================================

def build_message(picks, date_str):
    if not picks:
        return (
            f"🔎 <b>ESCÁNER DE FÚTBOL — {date_str}</b>\n\n"
            "🚫 No se encontraron picks que superaran todos los filtros.\n"
            "No se fuerza ninguna apuesta."
        )

    lines = [
        f"🎯 <b>MEJORES PICKS — {date_str}</b>",
        "",
        "Filtros: cuota ≥ 1.50 | prob. ≥ 65% | edge ≥ 5%",
        ""
    ]

    for i, p in enumerate(picks, 1):
        lines.extend([
            f"🔥 <b>PICK #{i}</b>",
            f"⚽ {p['home']} vs {p['away']}",
            f"🎯 <b>{p['pick']}</b>",
            f"💰 Cuota: <b>{p['odd']:.2f}</b>",
            f"📊 Confianza modelo: <b>{pct(p['probability'])}</b>",
            f"📈 Edge: <b>{pct(p['edge'])}</b>",
            f"🧠 {p['reason']}",
            ""
        ])

    lines.append("⚠️ El bot no fuerza picks cuando los datos no son suficientes.")
    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    if not API_KEY:
        raise SystemExit("❌ Falta API_FOOTBALL_KEY")

    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise SystemExit("❌ Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")

    now = honduras_now()
    date_str = now.strftime("%Y-%m-%d")

    print("=" * 68)
    print(f"ESCÁNER DE FÚTBOL — {date_str}")
    print("Mercados: 1X2 | Over 2.5 | BTTS")
    print("=" * 68)

    try:
        fixtures = get_today_fixtures(date_str)
        log(f"Partidos encontrados: {len(fixtures)}")

        if not fixtures:
            send_telegram(build_message([], date_str))
            return

        odds_map = get_today_odds(date_str)

        # Primero dejamos solo partidos con al menos una cuota >= 1.50
        candidates = []
        for fx in fixtures:
            if not fx.get("fixture", {}).get("id"):
                continue

            fid = fx["fixture"]["id"]
            odds = odds_map.get(fid, {})

            if not any(v >= MIN_ODD for v in odds.values()):
                continue

            candidates.append((fx, odds))

        # Para no gastar la cuota de API innecesariamente:
        # prioriza partidos con más mercados disponibles.
        candidates.sort(
            key=lambda x: len(x[1]),
            reverse=True
        )
        candidates = candidates[:MAX_CANDIDATES]

        log(f"Candidatos con cuotas: {len(candidates)}")

        picks = []
        team_cache = {}

        for idx, (fx, odds) in enumerate(candidates, 1):
            teams = fx.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}

            home_id = home.get("id")
            away_id = away.get("id")

            if not home_id or not away_id:
                continue

            log(f"[{idx}/{len(candidates)}] {home.get('name')} vs {away.get('name')}")

            try:
                if home_id not in team_cache:
                    team_cache[home_id] = get_context(home_id, home.get("name", "Local"))

                if away_id not in team_cache:
                    team_cache[away_id] = get_context(away_id, away.get("name", "Visitante"))

                home_ctx = team_cache[home_id]
                away_ctx = team_cache[away_id]

                # Exigimos 5 partidos como local y 5 como visitante.
                if not home_ctx["home"] or not away_ctx["away"]:
                    log("  ↳ Sin muestra suficiente local/visitante.")
                    continue

                probs = match_probabilities(home_ctx, away_ctx)
                if not probs:
                    continue

                pick = make_pick(fx, probs, odds)
                if not pick:
                    continue

                pick["reason"] = reason_for_pick(pick, home_ctx, away_ctx)
                picks.append(pick)

            except RuntimeError as e:
                log(f"  ↳ Detenido por API: {e}")
                break
            except Exception as e:
                log(f"  ↳ Error partido: {e}")

        # Ranking final: confianza, edge y score.
        picks.sort(
            key=lambda x: (x["score"], x["probability"], x["edge"]),
            reverse=True
        )

        # Evitar duplicados y saturación.
        final_picks = []
        seen = set()

        for p in picks:
            key = (p["home"], p["away"])
            if key in seen:
                continue
            seen.add(key)
            final_picks.append(p)

            if len(final_picks) >= MAX_ALERTS:
                break

        log("")
        log(f"Picks finales: {len(final_picks)}")
        log(f"Solicitudes API aproximadas: {requests_made}")

        message = build_message(final_picks, date_str)
        send_telegram(message)

    except RuntimeError as e:
        log(f"🛑 BOT DETENIDO: {e}")
        send_telegram(
            f"🛑 <b>BOT DETENIDO</b>\n\n"
            f"{e}\n\n"
            "No se enviaron picks inventados."
        )
    except Exception as e:
        log(f"🛑 ERROR GENERAL: {e}")
        send_telegram(
            f"🛑 <b>ERROR DEL BOT</b>\n\n"
            f"{str(e)[:500]}"
        )


if __name__ == "__main__":
    main()
