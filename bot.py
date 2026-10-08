import os
import requests
import pandas as pd

# 1. PARÁMETROS DE CONFIGURACIÓN (Inyectados de forma segura desde GitHub Secrets)
API_KEY = os.getenv("ODDS_API_KEY", "TU_API_KEY_AQUI")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Configuración del bot para buscar las ligas más estables
SPORT = "soccer"      # Usar prefijo general o ligas específicas como soccer_epl, soccer_spain_la_liga
REGIONS = "eu"        # Casas de apuestas de Europa (puedes cambiar a 'us' o 'au' si lo requieres)
MARKETS = "h2h"       # Mercado 1X2 (Local, Empate, Visitante)
ODDS_FORMAT = "decimal"

def enviar_alerta_telegram(mensaje):
    """Envía las apuestas filtradas de alta probabilidad directamente a tu Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: No se han configurado las credenciales de Telegram.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Alerta enviada correctamente a Telegram.")
        else:
            print(f"Error al enviar a Telegram: {response.text}")
    except Exception as e:
        print(f"Excepción al enviar notificación: {e}")

def obtener_cuotas():
    """Obtiene las cuotas en tiempo real de múltiples ligas de fútbol"""
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT
    }
    
    response = requests.get(url, params=params)
    if response.status_code != 200:
        print(f"Error al consultar la API de Cuotas: {response.status_code}")
        return []
    return response.json()

def simular_probabilidad_real(home_team, away_team):
    """
    Simulación de tu modelo estadístico.
    Para que el bot sea altamente efectivo y busque la ALTA PROBABILIDAD,
    asumiremos que calcula cuotas implícitas basándose en rachas u xG.
    Reemplaza este bloque con tu distribución de Poisson real.
    """
    return {
        "home": 0.45,
        "draw": 0.25,
        "away": 0.30
    }

def analizar_y_filtrar_apuestas():
    eventos = obtener_cuotas()
    if not eventos:
        print("No se encontraron eventos o la API Key de cuotas es inválida.")
        return

    oportunidades = []

    for evento in eventos:
        home_team = evento["home_team"]
        away_team = evento["away_team"]
        prob_reales = simular_probabilidad_real(home_team, away_team)
        
        for bookmaker in evento.get("bookmakers", []):
            bookmaker_name = bookmaker["title"]
            
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    cuotas = {outcome["name"]: outcome["price"] for outcome in market["outcomes"]}
                    
                    cuota_home = cuotas.get(home_team)
                    cuota_away = cuotas.get(away_team)
                    
                    analisis = [
                        {"tipo": f"Ganador: {home_team} (Local)", "cuota": cuota_home, "prob": prob_reales["home"]},
                        {"tipo": f"Ganador: {away_team} (Visitante)", "cuota": cuota_away, "prob": prob_reales["away"]}
                    ]
                    
                    for opc in analisis:
                        if opc["cuota"]:
                            ev = (opc["prob"] * opc["cuota"]) - 1
                            
                            # FILTROS DE EXCELENCIA: Alta probabilidad y Valor Esperado Positivo alto (> 5%)
                            # Esto asegura recibir picks muy selectos y optimizados para el usuario
                            if ev > 0.05 and opc["prob"] >= 0.40:
                                oportunidades.append({
                                    "partido": f"{home_team} vs {away_team}",
                                    "bookie": bookmaker_name,
                                    "seleccion": opc["tipo"],
                                    "cuota": opc["cuota"],
                                    "probabilidad": opc["prob"] * 100,
                                    "ev": ev * 100
                                })

    # Agrupar y enviar el reporte diario automatizado
    if oportunidades:
        mensaje_bloque = "🤖 *BOT DE APUESTAS: PICKS DIARIOS DE ALTA PROBABILIDAD* 🤖\n\n"
        for op in oportunidades:
            mensaje_bloque += (
                f"⚽ *Partido:* {op['partido']}\n"
                f"🎯 *Pick Recomendado:* {op['seleccion']}\n"
                f"📊 *Probabilidad de Éxito:* {op['probabilidad']:.1f}%\n"
                f"📈 *Cuota Encontrada:* {op['cuota']}\n"
                f"🏛️ *Casa de Apuestas:* {op['bookie']}\n"
                f"🔥 *Valor Esperado (EV):* +{op['ev']:.2f}%\n"
                f"-----------------------------------------\n"
            )
        enviar_alerta_telegram(mensaje_bloque)
    else:
        print("Análisis del día finalizado. No se detectaron apuestas que superaran el estricto umbral de alta probabilidad.")

if __name__ == "__main__":
    analizar_y_filtrar_apuestas()
