import os
import logging
import yfinance as ticker_data
import pandas as pd
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Configurazione del Logging ottimizzato per la dashboard di Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def calculate_indicators(df):
    """Calcola gli indicatori tecnici storici su un DataFrame (MA50, MA200, RSI, MACD)"""
    if len(df) < 200: 
        return None
        
    df = df.copy()
    
    # Moving Averages (Medie Mobili)
    df['MA50'] = df['Close'].rolling(window=50).mean()
    df['MA200'] = df['Close'].rolling(window=200).mean()
    
    # Relative Strength Index (RSI a 14 periodi)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Moving Average Convergence Divergence (MACD 12, 26, 9)
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    return df.iloc[-1]

def get_put_call_ratio(asset):
    """Estrae in modo lean il Put-Call Ratio basato sui volumi della scadenza opzioni più vicina"""
    try:
        options_dates = asset.options
        if not options_dates: 
            return "N/A (Nessuna opzione)"
            
        near_expiry = options_dates[0]
        opt_chain = asset.option_chain(near_expiry)
        
        calls_vol = opt_chain.calls['volume'].sum()
        puts_vol = opt_chain.puts['volume'].sum()
        
        if calls_vol > 0:
            pcr = puts_vol / calls_vol
            return f"{pcr:.2f} (Scad: {near_expiry})"
        return "N/A (Volume Call zero)"
    except Exception:
        return "N/A"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Messaggio di benvenuto all'avvio del bot"""
    await update.message.reply_text(
        "📊 **Benvenuto nel Bot Finanziario Lean!**\n\n"
        "Inviami il ticker di un'azione (es. `AAPL`, `TSLA`, `NVDA`) "
        "per ricevere un report tecnico e quantitativo istantaneo.",
        parse_mode="Markdown"
    )

async def generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce la richiesta del ticker, elabora i dati e risponde con il report"""
    ticker_symbol = update.message.text.upper().strip()
    await update.message.reply_text(f"🔍 Analisi in corso per {ticker_symbol}...")
    
    try:
        asset = ticker_data.Ticker(ticker_symbol)
        hist = asset.history(period="2y")
        
        if hist.empty:
            await update.message.reply_text("❌ Ticker non trovato o dati storici non disponibili.")
            return

        last_row = calculate_indicators(hist)
        info = asset.info
        
        # Estrazione prezzi e volumi (con fallback intelligenti se i dati mancano nell'info dict)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice') or hist['Close'].iloc[-1]
        volume = info.get('volume') or hist['Volume'].iloc[-1]
        vol_30d = info.get('averageVolume') or info.get('averageVolume10Days') or np.nan
        
        # Calcolo Spread Bid/Ask nativo yfinance
        bid = info.get('bid', 0.0)
        ask = info.get('ask', 0.0)
        if bid and ask and bid > 0 and ask > 0:
            spread_str = f"${ask - bid:.2f} (Bid: {bid} / Ask: {ask})"
        else:
            spread_str = "N/A (Mercato chiuso o dati assenti)"
            
        # Calcolo PCR
        pcr_str = get_put_call_ratio(asset)
        
        # Formattazione indicatori ed estrazione del Trend MACD richiesto
        if last_row is not None:
            rsi = f"{last_row['RSI']:.2f}"
            ma50 = f"${last_row['MA50']:.2f}"
            ma200 = f"${last_row['MA200']:.2f}"
            
            # Logica Trend Crossover MACD vs Signal
            macd_val = last_row['MACD']
            signal_val = last_row['Signal']
            if macd_val > signal_val:
                macd_str = f"{macd_val:.2f} (Signal: {signal_val:.2f}) 🟢 Rialzista"
            else:
                macd_str = f"{macd_val:.2f} (Signal: {signal_val:.2f}) 🔴 Ribassista"
        else:
            rsi = macd_str = ma50 = ma200 = "Dati storici insufficienti (<200gg)"

        # Formattazione e invio del report finale utente
        report = (
            f"📊 **REPORT FINANZIARIO: {ticker_symbol}**\n\n"
            f"💵 **Prezzo Attuale:** ${current_price:.2f}\n"
            f"↔️ **Spread Bid/Ask:** {spread_str}\n"
            f"📈 **RSI (14):** {rsi}\n"
            f"📉 **MACD Trend:** {macd_str}\n"
            f"📅 **MA50:** {ma50}\n"
            f"📆 **MA200:** {ma200}\n"
            f"📊 **Volume Odierno:** {int(volume):,}\n"
            f"⏱ **Volume Medio (30gg):** {f'{int(vol_30d):,}' if not np.isnan(vol_30d) else 'N/A'}\n"
            f"🎲 **Put-Call Ratio (Volume):** {pcr_str}"
        )
        
        await update.message.reply_text(report, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Errore durante l'elaborazione del ticker {ticker_symbol}: {e}")
        await update.message.reply_text("⚠️ Si è verificato un errore imprevisto nel recupero dei dati.")

# Server di Health Check fittizio richiesto da Render per mantenere attivo il Web Service
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        return  # Silenzia i log HTTP standard per pulizia della console

def run_health_server(port):
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    URL = os.getenv("RENDER_EXTERNAL_URL")  # Iniettata automaticamente da Render
    PORT = int(os.getenv("PORT", 10000))     # Iniettata automaticamente da Render

    if not TOKEN:
        logger.critical("Variabile d'ambiente TELEGRAM_TOKEN mancante!")
        return

    # Esegue il server HTTP di controllo in un thread indipendente
    threading.Thread(target=run_health_server, args=(PORT,), daemon=True).start()

    # Costruzione dell'applicazione del bot
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_report))
    
    # Avvio in modalità Webhook (obbligatoria per il piano Free di Render)
    # Usiamo una porta interna fittizia (PORT + 1) per svincolare l'Health Check dal webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT + 1,
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}"
    )

if __name__ == '__main__':
    main()
