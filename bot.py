import os
import time
import uuid
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

RUN_ID = str(uuid.uuid4())[:8]

# تنظیمات سرمایه و مدیریت ریسک
INITIAL_TON_BALANCE = float(os.environ.get("INITIAL_TON_BALANCE", "100.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.25"))
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "2.5"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-1.5"))
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "1.0"))

# توکن‌های شبکه TON
TOKENS = {
    "NOT": {
        "coingecko_id": "notcoin",
        "fallback_ton": 0.00145,
        "history": [],
        "source": "sim",
    },
    "DOGS": {
        "coingecko_id": "dogs-2",
        "fallback_ton": 0.00018,
        "history": [],
        "source": "sim",
    },
    "STON": {
        "coingecko_id": "ston",
        "fallback_ton": 0.55,
        "history": [],
        "source": "sim",
    },
}

ton_price_usd = None
ton_change_24h = 0.0
last_update_id = 0
trade_counter = 0

state_lock = threading.Lock()
trading_enabled = True  # با /trade_on و /trade_off کنترل می‌شود
balance_ton = INITIAL_TON_BALANCE
open_position = None
closed_trades = []


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"TON Advanced Trader ({RUN_ID}) active".encode("utf-8"))

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
    global ton_price_usd, ton_change_24h
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
        print("CoinGecko TON failed, trying CoinCap:", e)
        url2 = "https://api.coincap.io/v2/assets/toncoin"
        res2 = requests.get(url2, timeout=15)
        res2.raise_for_status()
        d2 = res2.json()["data"]
        price = float(d2["priceUsd"])
        ch24 = float(d2.get("changePercent24Hr", 0.0))

    ton_price_usd = price
    ton_change_24h = ch24
    return price, ch24


def fetch_token_prices_in_ton():
    cg_ids = [info["coingecko_id"] for info in TOKENS.values() if info.get("coingecko_id")]
    usd_map = {}

    if cg_ids and ton_price_usd and ton_price_usd > 0:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": ",".join(set(cg_ids)), "vs_currencies": "usd"}
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                payload = res.json()
                for k, v in payload.items():
                    if "usd" in v and v["usd"]:
                        usd_map[k] = float(v["usd"])
        except Exception as e:
            print("Token CoinGecko fetch warning:", e)

    for sym, info in TOKENS.items():
        hist = info["history"]
        cg_id = info.get("coingecko_id")

        if cg_id and cg_id in usd_map and ton_price_usd:
            real_ton_price = usd_map[cg_id] / ton_price_usd
            if hist and abs(real_ton_price - hist[-1]) < 1e-12:
                drift = random.uniform(-0.008, 0.010)
                new_p = max(real_ton_price * (1.0 + drift), 1e-8)
            else:
                new_p = max(real_ton_price, 1e-8)
            info["source"] = "live/cg"
        else:
            last_p = hist[-1] if hist else info["fallback_ton"]
            drift = random.uniform(-0.018, 0.022)
            new_p = max(last_p * (1.0 + drift), 1e-8)
            info["source"] = "sim"

        hist.append(new_p)
        if len(hist) > 60:
            hist.pop(0)


def score_token(sym: str):
    hist = TOKENS[sym]["history"]
    if len(hist) < 2:
        return 0, 0.0
    prev_p, curr_p = hist[-2], hist[-1]
    delta_pct = ((curr_p - prev_p) / prev_p) * 100.0
    ma3 = sum(hist[-3:]) / min(len(hist), 3)

    score = 0
    if delta_pct >= 0.8:
        score += 2
    elif delta_pct >= 0.25:
        score += 1
    elif delta_pct <= -0.8:
        score -= 2
    elif delta_pct <= -0.25:
        score -= 1

    if curr_p > ma3:
        score += 1
    elif curr_p < ma3:
        score -= 1

    if ton_change_24h > 1.5:
        score += 1
    elif ton_change_24h < -2.5:
        score -= 1

    return score, delta_pct


def process_trading_step():
    global balance_ton, open_position, trade_counter

    with state_lock:
        if not trading_enabled:
            return

    fetch_token_prices_in_ton()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with state_lock:
        if not trading_enabled:
            return

        # ۱. مدیریت پوزیشن باز (حد سود، حد ضرر و تریلینگ استاپ)
        if open_position is not None:
            sym = open_position["symbol"]
            entry_price = open_position["entry_price_ton"]
            token_qty = open_position["token_qty"]
            curr_price = TOKENS[sym]["history"][-1]

            pnl_pct = ((curr_price - entry_price) / entry_price) * 100.0
            peak_pnl_pct = max(open_position.get("peak_pnl_pct", pnl_pct), pnl_pct)
            open_position["peak_pnl_pct"] = peak_pnl_pct

            effective_sl = STOP_LOSS_PCT
            if peak_pnl_pct >= 1.2:
                trailing_sl = peak_pnl_pct - TRAILING_STOP_PCT
                effective_sl = max(effective_sl, trailing_sl)

            exit_reason = None
            if pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"رسیدن به حد سود ({TAKE_PROFIT_PCT:+.1f}%)"
            elif pnl_pct <= effective_sl:
                if peak_pnl_pct >= 1.2:
                    exit_reason = f"حد سود متحرک / Trailing ({effective_sl:+.1f}%)"
                else:
                    exit_reason = f"فعال شدن حد ضرر ({STOP_LOSS_PCT:+.1f}%)"

            if exit_reason:
                gross_ton = token_qty * curr_price
                fee_ton = gross_ton * 0.002
                net_ton = gross_ton - fee_ton
                profit_ton = net_ton - open_position["spent_ton"]
                net_pnl_pct = (profit_ton / open_position["spent_ton"]) * 100.0

                balance_ton += net_ton
                record = {
                    "id": open_position["id"],
                    "symbol": sym,
                    "entry_price": entry_price,
                    "exit_price": curr_price,
                    "profit_ton": profit_ton,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(record)
                open_position = None

                msg = (
                    f"📤 <b>گزارش خروج از معامله #{record['id']}</b> [<code>{RUN_ID}</code>]\n"
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

        # ۲. بررسی سیگنال ورود روی توکن‌ها
        best_sym = None
        best_score = 1
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
                "peak_pnl_pct": 0.0,
                "opened_at": now_str,
            }

            tp_price = entry_price * (1.0 + TAKE_PROFIT_PCT / 100.0)
            sl_price = entry_price * (1.0 + STOP_LOSS_PCT / 100.0)
            src_label = TOKENS[best_sym]["source"]

            msg = (
                f"📥 <b>گزارش ورود به معامله #{trade_counter}</b> [<code>{RUN_ID}</code>]\n"
                f"🪙 توکن: <b>{best_sym}</b> (TON → {best_sym}) [<code>{src_label}</code>]\n"
                f"💵 مبلغ اختصاص‌یافته: <b>{spend_ton:.4f} TON</b>\n"
                f"🎯 قیمت ورود: <code>{entry_price:.6f} TON</code>\n"
                f"📈 تغییر لحظه‌ای: <code>{best_delta:+.2f}%</code> | امتیاز: <b>{best_score}</b>\n"
                f"🟢 حد سود هدف: <code>{tp_price:.6f} TON</code> ({TAKE_PROFIT_PCT:+.1f}%)\n"
                f"🔴 حد ضرر اولیه: <code>{sl_price:.6f} TON</code> ({STOP_LOSS_PCT:+.1f}%)\n"
                f"💼 موجودی باقی‌مانده: <b>{balance_ton:.4f} TON</b>\n"
                f"🕒 زمان: <code>{now_str}</code>"
            )
            send_telegram_message(msg)


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_ton"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_ton"] > 0)
        total_closed = len(closed_trades)
        price_str = f"${ton_price_usd:.4f}" if ton_price_usd is not None else "نامشخص"
        mode_str = "🟢 روشن (در حال ترید)" if trading_enabled else "🔴 متوقف (قطع‌شده)"

        return (
            f"📊 <b>وضعیت جامع ربات تریدر TON</b> [<code>{RUN_ID}</code>]\n"
            f"⚙️ وضعیت ترید: <b>{mode_str}</b>\n"
            f"💵 قیمت TON: <b>{price_str}</b> (۲۴h: <code>{ton_change_24h:+.2f}%</code>)\n"
            f"💼 موجودی نقد TON: <b>{balance_ton:.4f} TON</b>\n"
            f"📈 سود/ضرر کل معاملات بسته: <code>{total_pnl:+.4f} TON</code>\n"
            f"🏆 معاملات موفق: <b>{wins}/{total_closed}</b>\n"
            f"🔓 پوزیشن باز: <b>{'بله (' + open_position['symbol'] + ')' if open_position else 'خیر'}</b>"
        )


def build_positions_text():
    with state_lock:
        if not open_position:
            return f"🔒 در حال حاضر پوزیشن بازی وجود ندارد. [<code>{RUN_ID}</code>]"
        sym = open_position["symbol"]
        curr_p = TOKENS[sym]["history"][-1]
        entry_p = open_position["entry_price_ton"]
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
        peak_pct = open_position.get("peak_pnl_pct", pnl_pct)
        return (
            f"🔓 <b>جزئیات پوزیشن باز #{open_position['id']}</b> [<code>{RUN_ID}</code>]\n"
            f"• توکن: <b>{sym}</b>\n"
            f"• سرمایه درگیر: <b>{open_position['spent_ton']:.4f} TON</b>\n"
            f"• قیمت ورود: <code>{entry_p:.6f} TON</code>\n"
            f"• قیمت فعلی: <code>{curr_p:.6f} TON</code>\n"
            f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code> (اوج: <code>{peak_pct:+.2f}%</code>)"
        )


def build_history_text():
    with state_lock:
        if not closed_trades:
            return f"📭 هنوز هیچ معامله بسته‌شده‌ای ثبت نشده است. [<code>{RUN_ID}</code>]"
        recent = closed_trades[-5:]
        lines = [f"📜 <b>آخرین {len(recent)} معامله بسته‌شده</b> [<code>{RUN_ID}</code>]"]
        for t in reversed(recent):
            lines.append(
                f"• #{t['id']} <b>{t['symbol']}</b> | ورود: <code>{t['entry_price']:.6f}</code> → "
                f"خروج: <code>{t['exit_price']:.6f}</code> | "
                f"سود/ضرر: <code>{t['profit_ton']:+.4f} TON</code> (<code>{t['pnl_pct']:+.2f}%</code>)"
            )
        return "\n".join(lines)


def reset_simulation():
    global balance_ton, open_position, closed_trades, trade_counter
    with state_lock:
        balance_ton = INITIAL_TON_BALANCE
        open_position = None
        closed_trades = []
        trade_counter = 0


def poll_telegram_updates():
    global last_update_id, trading_enabled
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 20}
            res = requests.get(url, params=params, timeout=30)
            if res.status_code == 200:
                for upd in res.json().get("result", []):
                    last_update_id = upd["update_id"]
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not chat_id:
                        continue

                    cmd = text.split()[0].lower() if text else ""

                    if cmd in ("/start", "/help"):
                        send_telegram_message(
                            f"🤖 <b>ربات تریدر جامع TON</b> [<code>{RUN_ID}</code>]\n"
                            "دستورات اصلی کنترل ترید:\n"
                            "• /trade_on (یا /run) — شروع و فعال‌سازی ترید\n"
                            "• /trade_off (یا /stop) — قطع فوری و توقف ترید\n\n"
                            "دستورات گزارش و مدیریت:\n"
                            "• /status — وضعیت کلی و موجودی\n"
                            "• /positions — جزئیات پوزیشن باز\n"
                            "• /history — آخرین معاملات بسته\n"
                            "• /reset — ریست شبیه‌سازی",
                            chat_id=chat_id,
                        )
                    elif cmd in ("/trade_on", "/run"):
                        with state_lock:
                            trading_enabled = True
                        send_telegram_message(
                            f"🟢 <b>عملیات ترید شروع شد!</b> [<code>{RUN_ID}</code>]\n"
                            "ربات اکنون بازار را تحلیل می‌کند و در صورت وجود سیگنال معتبر، ترید انجام می‌دهد.",
                            chat_id=chat_id,
                        )
                    elif cmd in ("/trade_off", "/stop"):
                        with state_lock:
                            trading_enabled = False
                        send_telegram_message(
                            f"🛑 <b>عملیات ترید متوقف شد!</b> [<code>{RUN_ID}</code>]\n"
                            "تمام عملیات ترید و ورود/خروج خودکار قطع شد تا دستور /trade_on ارسال شود.",
                            chat_id=chat_id,
                        )
                    elif cmd == "/status":
                        try:
                            fetch_ton_price()
                        except Exception as e:
                            print("Price fetch in /status failed:", e)
                        send_telegram_message(build_status_text(), chat_id=chat_id)
                    elif cmd == "/positions":
                        send_telegram_message(build_positions_text(), chat_id=chat_id)
                    elif cmd == "/history":
                        send_telegram_message(build_history_text(), chat_id=chat_id)
                    elif cmd == "/reset":
                        reset_simulation()
                        send_telegram_message(
                            f"🔄 شبیه‌سازی ریست شد. موجودی جدید: <b>{INITIAL_TON_BALANCE:.2f} TON</b> [<code>{RUN_ID}</code>]",
                            chat_id=chat_id,
                        )
        except Exception as e:
            print("Polling error:", e)
        time.sleep(2)


def trading_loop():
    for sym, info in TOKENS.items():
        if not info["history"]:
            info["history"].append(info["fallback_ton"])

    send_telegram_message(
        f"🚀 <b>ربات تریدر جامع TON آماده شد!</b> [<code>{RUN_ID}</code>]\n"
        f"• شروع ترید: <code>/trade_on</code>\n"
        f"• قطع و توقف ترید: <code>/trade_off</code>\n"
        f"• وضعیت لحظه‌ای: <code>/status</code>"
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
