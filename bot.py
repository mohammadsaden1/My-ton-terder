import os
import time
import random
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

DEFAULT_TOKEN = "9080285155:AAHuZFiALLtk9Sstk1jTFFZN0PwPLWFhgWM"
DEFAULT_CHAT_ID = "1951133060"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
PORT = int(os.environ.get("PORT", "10000"))

# تنظیمات استراتژی و مدیریت ریسک (نسخه ۲ - شبیه‌سازی)
INITIAL_TON_BALANCE = float(os.environ.get("INITIAL_TON_BALANCE", "100.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.25"))  # 25% سرمایه در هر معامله
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "2.5"))  # حد سود +2.5%
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-1.5"))  # حد ضرر -1.5%

TOKENS = {
    "NOT": {"base_price_ton": 0.00145, "history": []},
    "DOGS": {"base_price_ton": 0.00018, "history": []},
}

ton_price_usd = None
ton_change_24h = 0.0
ton_history = []
last_update_id = 0
trade_counter = 0

state_lock = threading.Lock()
balance_ton = INITIAL_TON_BALANCE
open_position = None  # دیکشنری پوزیشن باز یا None
closed_trades = []


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TON Advanced Trader v2 is running")

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
        if res.status_code != 200:
            print("Telegram error:", res.status_code, res.text[:200])
        return res.status_code == 200
    except Exception as e:
        print("Telegram exception:", e)
        return False


def fetch_ton_price():
    global ton_price_usd, ton_change_24h, ton_history
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
        price = float(data["usd"])
        ch24 = float(data.get("usd_24h_change", 0.0))
    except Exception as e:
        print("CoinGecko failed, trying CoinCap:", e)
        url2 = "https://api.coincap.io/v2/assets/toncoin"
        res2 = requests.get(url2, timeout=15)
        res2.raise_for_status()
        d2 = res2.json()["data"]
        price = float(d2["priceUsd"])
        ch24 = float(d2.get("changePercent24Hr", 0.0))

    ton_price_usd = price
    ton_change_24h = ch24
    ton_history.append(price)
    if len(ton_history) > 50:
        ton_history.pop(0)
    return price, ch24


def update_token_prices():
    # شبیه‌سازی قیمت توکن‌ها به TON با نوسان کنترل‌شده برای تست استراتژی
    for sym, info in TOKENS.items():
        hist = info["history"]
        last_p = hist[-1] if hist else info["base_price_ton"]
        drift = random.uniform(-0.018, 0.022)  # نوسان -1.8% تا +2.2%
        new_p = max(last_p * (1.0 + drift), 1e-8)
        hist.append(new_p)
        if len(hist) > 50:
            hist.pop(0)


def score_token(sym: str):
    hist = TOKENS[sym]["history"]
    if len(hist) < 2:
        return 0, 0.0
    prev_p, curr_p = hist[-2], hist[-1]
    delta_pct = ((curr_p - prev_p) / prev_p) * 100.0

    score = 0
    if delta_pct >= 0.8:
        score += 2
    elif delta_pct >= 0.25:
        score += 1
    elif delta_pct <= -0.8:
        score -= 2
    elif delta_pct <= -0.25:
        score -= 1

    if ton_change_24h > 1.5:
        score += 1
    elif ton_change_24h < -2.5:
        score -= 1

    return score, delta_pct


def process_trading_step():
    global balance_ton, open_position, trade_counter

    update_token_prices()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with state_lock:
        # ۱. اگر پوزیشن باز داریم، حد سود و حد ضرر را بررسی کنیم
        if open_position is not None:
            sym = open_position["symbol"]
            entry_price = open_position["entry_price_ton"]
            token_qty = open_position["token_qty"]
            curr_price = TOKENS[sym]["history"][-1]

            pnl_pct = ((curr_price - entry_price) / entry_price) * 100.0
            exit_reason = None

            if pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"رسیدن به حد سود ({TAKE_PROFIT_PCT:+.1f}%)"
            elif pnl_pct <= STOP_LOSS_PCT:
                exit_reason = f"فعال شدن حد ضرر ({STOP_LOSS_PCT:+.1f}%)"

            if exit_reason:
                gross_ton = token_qty * curr_price
                fee_ton = gross_ton * 0.002  # کارمزد فرضی 0.2%
                net_ton = gross_ton - fee_ton
                profit_ton = net_ton - open_position["spent_ton"]
                net_pnl_pct = (profit_ton / open_position["spent_ton"]) * 100.0

                balance_ton += net_ton
                trade_record = {
                    "id": open_position["id"],
                    "symbol": sym,
                    "entry_price": entry_price,
                    "exit_price": curr_price,
                    "profit_ton": profit_ton,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(trade_record)
                open_position = None

                msg = (
                    f"📤 <b>گزارش خروج از معامله #{trade_record['id']}</b>\n"
                    f"🪙 توکن: <b>{sym}</b> (چنج به TON)\n"
                    f"📥 قیمت ورود: <code>{entry_price:.6f} TON</code>\n"
                    f"📤 قیمت خروج: <code>{curr_price:.6f} TON</code>\n"
                    f"📌 علت خروج: <b>{exit_reason}</b>\n"
                    f"💰 سود/ضرر خالص: <code>{profit_ton:+.4f} TON</code> (<code>{net_pnl_pct:+.2f}%</code>)\n"
                    f"💼 موجودی جدید: <b>{balance_ton:.4f} TON</b>\n"
                    f"🕒 زمان: <code>{now_str}</code>"
                )
                send_telegram_message(msg)
            return

        # ۲. اگر پوزیشن باز نداریم، بهترین توکن را برای ورود بررسی کنیم
        best_sym = None
        best_score = 0
        best_delta = 0.0

        for sym in TOKENS:
            sc, dlt = score_token(sym)
            if sc >= 2 and sc > best_score:
                best_score = sc
                best_sym = sym
                best_delta = dlt

        if best_sym and balance_ton > 1.0:
            spend_ton = round(balance_ton * TRADE_ALLOCATION_PCT, 4)
            if spend_ton <= 0:
                return
            entry_price = TOKENS[best_sym]["history"][-1]
            fee_ton = spend_ton * 0.002
            effective_ton = spend_ton - fee_ton
            token_qty = effective_ton / entry_price

            balance_ton -= spend_ton
            trade_counter += 1
            open_position = {
                "id": trade_counter,
                "symbol": best_sym,
                "entry_price_ton": entry_price,
                "token_qty": token_qty,
                "spent_ton": spend_ton,
                "opened_at": now_str,
            }

            tp_price = entry_price * (1.0 + TAKE_PROFIT_PCT / 100.0)
            sl_price = entry_price * (1.0 + STOP_LOSS_PCT / 100.0)

            msg = (
                f"📥 <b>گزارش ورود به معامله #{trade_counter}</b>\n"
                f"🪙 توکن: <b>{best_sym}</b> (TON → {best_sym})\n"
                f"💵 مبلغ اختصاص‌یافته: <b>{spend_ton:.4f} TON</b>\n"
                f"🎯 قیمت ورود: <code>{entry_price:.6f} TON</code>\n"
                f"📈 تغییر لحظه‌ای: <code>{best_delta:+.2f}%</code> | امتیاز: <b>{best_score}</b>\n"
                f"🟢 حد سود هدف: <code>{tp_price:.6f} TON</code> ({TAKE_PROFIT_PCT:+.1f}%)\n"
                f"🔴 حد ضرر: <code>{sl_price:.6f} TON</code> ({STOP_LOSS_PCT:+.1f}%)\n"
                f"💼 موجودی باقی‌مانده: <b>{balance_ton:.4f} TON</b>\n"
                f"🕒 زمان: <code>{now_str}</code>"
            )
            send_telegram_message(msg)


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_ton"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_ton"] > 0)
        total_closed = len(closed_trades)

        price_str = f"${ton_price_usd:.4f}" if ton_price_usd is not None else "در حال دریافت..."
        lines = [
            "📊 <b>وضعیت ربات تریدر TON (نسخه ۲ - شبیه‌سازی)</b>",
            f"💵 قیمت TON: <b>{price_str}</b> (۲۴h: <code>{ton_change_24h:+.2f}%</code>)",
            f"💼 موجودی نقد TON: <b>{balance_ton:.4f} TON</b>",
            f"📈 سود/ضرر کل معاملات بسته: <code>{total_pnl:+.4f} TON</code>",
            f"🏆 معاملات موفق: <b>{wins}/{total_closed}</b>",
        ]

        if open_position:
            sym = open_position["symbol"]
            curr_p = TOKENS[sym]["history"][-1]
            entry_p = open_position["entry_price_ton"]
            pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
            lines.append(
                f"\n🔓 <b>پوزیشن باز #{open_position['id']} ({sym}):</b>\n"
                f"• ورود: <code>{entry_p:.6f} TON</code> | فعلی: <code>{curr_p:.6f} TON</code>\n"
                f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code>"
            )
        else:
            lines.append("\n🔒 <b>پوزیشن باز:</b> ندارد (در انتظار سیگنال ورود)")

        return "\n".join(lines)


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
                    if not chat_id:
                        continue

                    if text == "/start":
                        send_telegram_message(
                            "✅ <b>ربات تریدر TON نسخه ۲ فعال است!</b>\n"
                            "دستورات موجود:\n"
                            "• /status — وضعیت موجودی، پوزیشن باز و سود/ضرر",
                            chat_id=chat_id,
                        )
                    elif text == "/status":
                        try:
                            fetch_ton_price()
                        except Exception as e:
                            print("Price fetch in /status failed:", e)
                        send_telegram_message(build_status_text(), chat_id=chat_id)
        except Exception as e:
            print("Polling error:", e)
        time.sleep(2)


def trading_loop():
    # مقداردهی اولیه تاریخچه قیمت توکن‌ها
    for sym, info in TOKENS.items():
        if not info["history"]:
            info["history"].append(info["base_price_ton"])

    send_telegram_message(
        "🤖 <b>ربات تریدر TON نسخه ۲ روشن شد!</b>\n"
        f"سرمایه اولیه شبیه‌سازی: <b>{INITIAL_TON_BALANCE:.2f} TON</b>\n"
        f"حد سود: <code>{TAKE_PROFIT_PCT:+.1f}%</code> | حد ضرر: <code>{STOP_LOSS_PCT:+.1f}%</code>"
    )

    while True:
        try:
            fetch_ton_price()
        except Exception as e:
            print("Price fetch error in loop:", e)

        try:
            process_trading_step()
        except Exception as e:
            print("Trading step error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=poll_telegram_updates, daemon=True).start()
    trading_loop()
