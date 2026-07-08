import os
import logging
import yfinance as ticker_data
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging per Render
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def calculate_indicators(df):
    if len(df) < 200: return None
    df = df.copy()
    df['MA50'] = df['Close'].rolling(window=50).mean()
    df['MA200'] = df['Close'].rolling(window=200).mean()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df.iloc[-1]

def get_put_call_ratio(asset):
    try:
        options_dates = asset.options
        if not options_dates: return "N/A"
        near_expiry = options_dates[0]
        opt_chain = asset.option_chain(near_expiry)
        calls_vol = opt_chain.calls['volume'].sum()
        puts_vol = opt_chain.puts['volume'].sum()
        if calls_vol > 0:
            return f"{puts_vol / calls_vol:.2f} (Scad: {near_expiry})"
        return "N/A"
    except Exception:
        return "N/A"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Inviami un ticker (es. AAPL, TSLA) per il report finanziario.")

async def generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker_symbol = update.message.text.upper().strip()
    await update.message.reply_text(f"🔍 Analisi in corso per {ticker_symbol}...")
    try:
        asset = ticker_data.Ticker(ticker_symbol)
        hist = asset.history(period="2y")
        if hist.empty:
            await update.message.reply_text("❌ Ticker non trovato o dati non disponibili.")
            return

        last_row = calculate_indicators(hist)
        info = asset.info
        current_price = info.get('currentPrice') or info.get('regularMarketPrice') or hist['Close'].iloc[-1]
        volume = info.get('volume') or hist['Volume'].iloc[-1]
        vol_30d = info.get('averageVolume') or np.nan
        
        bid, ask = info.get('bid', 0.0), info.get('ask', 0.0)
        spread_str = f"${ask - bid:.2f} (Bid: {bid} / Ask: {ask})" if bid and ask else "N/A (Mercato chiuso)"
        pcr_str = get_put_call_ratio(asset)
        
        if last_row is not None:
            rsi = f"{last_row['RSI']:.2f}"
            ma50 = f"${last_row['MA50']:.2f}"
            ma200 = f"${last_row['MA200']:.2f}"
            if last_row['MACD'] > last_row['Signal']:
                macd_str = f"{last_row['MACD']:.2f} 🟢 Rialzista"
            else:
                macd_str = f"{last_row['MACD']:.2f} 🔴 Ribassista"
        else:
            rsi = macd_str = ma50 = ma200 = "N/A"

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
            f"🎲 **Put-Call Ratio:** {pcr_str}"
        )
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Errore: {e}")
        await update.message.reply_text("⚠️ Si è verificato un errore nel recupero dati.")

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    URL = os.getenv("RENDER_EXTERNAL_URL")
    PORT = int(os.getenv("PORT", 10000))

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_report))
    
    logger.info(f"Avvio Webhook su {URL}/{TOKEN} sulla porta {PORT}")
    
    # Avvio del webhook centralizzato sulla porta principale di Render
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{URL}/{TOKEN}"
    )

if __name__ == '__main__':
    main()
