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
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "120"))
PORT = int(os.environ.get("PORT", "10000"))

price_history = []
last_update_id = 0


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


def send_telegram_message(text: str, chat_id: str = None):
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        print("ERROR: Token or Chat ID missing")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        print("Telegram send status:", res.status_code, res.text[:200])
        return res.status_code == 200
    except Exception as e:
        print("ERROR sending telegram message:", e)
        return False


def fetch_ton_price():
    # تلاش اول: CoinGecko
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "the-open-network",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()["the-open-network"]
        return float(data["usd"]), float(data.get("usd_24h_change", 0.0))
    except Exception as e:
        print("CoinGecko failed, trying fallback:", e)

    # تلاش دوم: CoinCap
    url2 = "https://api.coincap.io/v2/assets/toncoin"
    res2 = requests.get(url2, timeout=15)
    res2.raise_for_status()
    d2 = res2.json()["data"]
    return float(d2["priceUsd"]), float(d2.get("changePercent24Hr", 0.0))


def analyze_market(current_price: float, change_24h: float):
    price_history.append(current_price)
    if len(price_history) > 30:
        price_history.pop(0)

    if len(price_history) < 2:
        return "در حال جمع‌آوری داده اولیه", 0.0

    prev_price = price_history[-2]
    short_delta_pct = ((current_price - prev_price) / prev_price) * 100

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


def poll_telegram_updates():
    global last_update_id
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 20}
            res = requests.get(url, params=params, timeout=30)
            if res.status_code == 200:
                data = res.json()
                for upd in data.get("result", []):
                    last_update_id = upd["update_id"]
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if text == "/start" and chat_id:
                        send_telegram_message(
                            "✅ <b>ربات فعال است!</b>\n"
                            "دستور /status را برای گزارش لحظه‌ای بفرستید.",
                            chat_id=chat_id,
                        )
                    elif text == "/status" and chat_id:
                        price_usd, change_24h = fetch_ton_price()
                        signal, short_delta = analyze_market(price_usd, change_24h)
                        send_telegram_message(
                            f"📊 <b>وضعیت لحظه‌ای TON</b>\n"
                            f"💵 قیمت: <b>${price_usd:.4f}</b>\n"
                            f"📈 تغییر لحظه‌ای: <code>{short_delta:+.2f}%</code>\n"
                            f"🌐 تغییر ۲۴h: <code>{change_24h:+.2f}%</code>\n"
                            f"🎯 تحلیل: <b>{signal}</b>",
                            chat_id=chat_id,
                        )
        except Exception as e:
            print("Polling error:", e)
        time.sleep(2)


def trading_loop():
    send_telegram_message(
        "🚀 <b>ربات هوشمند TON روی Render فعال شد!</b>\n"
        "گزارش‌های دوره‌ای و پاسخ به /start و /status فعال است."
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
            print("ERROR in trading_loop:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=poll_telegram_updates, daemon=True).start()
    trading_loop()
