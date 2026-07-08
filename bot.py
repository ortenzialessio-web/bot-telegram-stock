import os
import logging
import yfinance as ticker_data
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Configurazione del Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def calculate_indicators(df):
    """Calcola gli indicatori tecnici su dati storici in modo sicuro"""
    if df is None or len(df) < 200: 
        return None
        
    df = df.copy()
    
    # Medie Mobili
    df['MA50'] = df['Close'].rolling(window=50).mean()
    df['MA200'] = df['Close'].rolling(window=200).mean()
    
    # RSI (14 periodi)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # MACD (12, 26, 9)
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    return df.iloc[-1]

def get_put_call_ratio_safe(asset):
    """Tenta il recupero del Put-Call Ratio; restituisce N/A se Yahoo blocca la richiesta"""
    try:
        options_dates = asset.options
        if not options_dates: 
            return "N/A"
        near_expiry = options_dates[0]
        opt_chain = asset.option_chain(near_expiry)
        calls_vol = opt_chain.calls['volume'].sum()
        puts_vol = opt_chain.puts['volume'].sum()
        if calls_vol > 0:
            return f"{puts_vol / calls_vol:.2f} (Scad: {near_expiry})"
        return "N/A"
    except Exception:
        return "N/A (Limitazione API)"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Inviami un ticker (es. AAPL, TSLA, NVDA) per ricevere il report finanziario.")

async def generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker_symbol = update.message.text.upper().strip()
    await update.message.reply_text(f"🔍 Analisi in corso per {ticker_symbol}...")
    
    try:
        asset = ticker_data.Ticker(ticker_symbol)
        
        # Recupero dati storici (metodo nativo yfinance molto più stabile di .info)
        hist = asset.history(period="2y")
        if hist.empty:
            await update.message.reply_text("❌ Ticker non trovato o dati storici non disponibili su Yahoo Finance.")
            return

        # Calcolo indicatori tecnici
        last_row = calculate_indicators(hist)
        
        # Gestione difensiva del dizionario .info (se fallisce o è vuoto non blocca il codice)
        try:
            info = asset.info
            if not info or not isinstance(info, dict):
                info = {}
        except Exception:
            info = {}

        # 1. Estrazione Prezzo (Fallback se .info è vuoto)
        current_price = info.get('currentPrice') or info.get('regularMarketPrice') or hist['Close'].iloc[-1]
        
        # 2. Estrazione Volumi (Fallback su dati storici se .info è bloccato)
        volume = info.get('volume') or hist['Volume'].iloc[-1]
        
        # Calcolo volume medio 30 giorni direttamente dal dataframe storico (Rimozione dipendenza da .info)
        try:
            hist_30d = hist.tail(30)
            vol_30d = hist_30d['Volume'].mean()
        except Exception:
            vol_30d = info.get('averageVolume') or np.nan
        
        # 3. Estrazione Spread Bid/Ask con gestione di sicurezza
        bid = info.get('bid', 0.0)
        ask = info.get('ask', 0.0)
        if bid and ask and bid > 0 and ask > 0:
            spread_str = f"${ask - bid:.2f} (Bid: {bid} / Ask: {ask})"
        else:
            spread_str = "N/A (Dati real-time protetti/Mercato chiuso)"
            
        # 4. Calcolo Put-Call Ratio protetto
        pcr_str = get_put_call_ratio_safe(asset)
        
        # 5. Formattazione Indicatori Tecnici e Trend MACD
        if last_row is not None:
            rsi_val = f"{last_row['RSI']:.2f}"
            ma50_val = f"${last_row['MA50']:.2f}"
            ma200_val = f"${last_row['MA200']:.2f}"
            
            # Trend MACD Crossover richiesto
            if last_row['MACD'] > last_row['Signal']:
                macd_str = f"{last_row['MACD']:.2f} (Signal: {last_row['Signal']:.2f}) 🟢 Rialzista"
            else:
                macd_str = f"{last_row['MACD']:.2f} (Signal: {last_row['Signal']:.2f}) 🔴 Ribassista"
        else:
            rsi_val = macd_str = ma50_val = ma200_val = "N/A (Dati storici insufficienti)"

        # Formattazione finale del Report Utente
        report = (
            f"📊 **REPORT FINANZIARIO: {ticker_symbol}**\n\n"
            f"💵 **Prezzo Attuale:** ${current_price:.2f}\n"
            f"↔️ **Spread Bid/Ask:** {spread_str}\n"
            f"📈 **RSI (14):** {rsi_val}\n"
            f"📉 **MACD Trend:** {macd_str}\n"
            f"📅 **MA50:** {ma50_val}\n"
            f"📆 **MA200:** {ma200_val}\n"
            f"📊 **Volume Odierno:** {int(volume):,}\n"
            f"⏱ **Volume Medio (30gg):** {f'{int(vol_30d):,}' if not np.isnan(vol_30d) else 'N/A'}\n"
            f"🎲 **Put-Call Ratio (Vol):** {pcr_str}"
        )
        
        await update.message.reply_text(report, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Errore critico durante l'analisi del ticker {ticker_symbol}: {e}")
        await update.message.reply_text("⚠️ Impossibile completare l'analisi. Riprova tra qualche istante.")

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    URL = os.getenv("RENDER_EXTERNAL_URL")
    PORT = int(os.getenv("PORT", 10000))

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_report))
    
    logger.info(f"Avvio Webhook su {URL}/{TOKEN} porta {PORT}")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}"
    )

if __name__ == '__main__':
    main()
