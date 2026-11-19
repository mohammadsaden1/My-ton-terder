import os
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

DEFAULT_TOKEN = "9080285155:AAHuZFiALLtk9Sstk1jTFFZN0PwPLWFhgWM"
DEFAULT_CHAT_ID = "1951133060"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "120"))  # هر 120 ثانیه
PORT = int(os.environ.get("PORT", "10000"))

price_history = []


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TON Advanced Bot is running")

    def log_message(self, format, *args):
        return


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("توکن یا Chat ID تنظیم نشده است.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200:
            print("خطا در ارسال پیام تلگرام:", res.text)
    except Exception as e:
        print("استثنا در ارسال پیام:", e)


def fetch_ton_price():
    # دریافت قیمت لحظه‌ای TON به USD از CoinGecko
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "the-open-network", "vs_currencies": "usd", "include_24hr_change": "true"}
    res = requests.get(url, params=params, timeout=15)
    res.raise_for_status()
    data = res.json()["the-open-network"]
    return float(data["usd"]), float(data.get("usd_24h_change", 0.0))


def analyze_market(current_price: float, change_24h: float):
    price_history.append(current_price)
    if len(price_history) > 30:
        price_history.pop(0)

    if len(price_history) < 2:
        return "در حال جمع‌آوری داده اولیه", 0.0

    prev_price = price_history[-2]
    short_delta_pct = ((current_price - prev_price) / prev_price) * 100

    # امتیازدهی ساده اولیه برای نسخه 1
    score = 0
    if short_delta_pct > 0.3:
        score += 1
    elif short_delta_pct < -0.3:
        score -= 1

    if change_24h > 2.0:
        score += 1
    elif change_24h < -2.0:
        score -= 1

    if score >= 2:
        signal = "سیگنال صعودی قوی (آماده بررسی ورود)"
    elif score == 1:
        signal = "روند صعودی ملایم"
    elif score <= -2:
        signal = "سیگنال نزولی / احتیاط (حد ضرر)"
    else:
        signal = "خنثی / نوسان محدود"

    return signal, short_delta_pct


def trading_loop():
    send_telegram_message(
        "🚀 <b>ربات هوشمند TON روی Render فعال شد!</b>\n"
        "نسخه ۱: مانیتورینگ زنده و تحلیل اولیه فعال است."
    )

    while True:
        try:
            price_usd, change_24h = fetch_ton_price()
            signal, short_delta = analyze_market(price_usd, change_24h)
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

            report = (
                f"📊 <b>گزارش تحلیلی TON</b>\n"
                f"🕒 زمان: <code>{now_str}</code>\n"
                f"💵 قیمت لحظه‌ای: <b>${price_usd:.4f}</b>\n"
                f"📈 تغییر لحظه‌ای: <code>{short_delta:+.2f}%</code>\n"
                f"🌐 تغییر ۲۴ ساعته: <code>{change_24h:+.2f}%</code>\n"
                f"🎯 وضعیت تحلیل: <b>{signal}</b>"
            )
            send_telegram_message(report)
        except Exception as e:
            print("خطا در حلقه تحلیل:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    trading_loop()
