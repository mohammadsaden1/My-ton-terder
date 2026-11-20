import os
import time
import uuid
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

try:
    from tabdeal.spot import Spot
    from tabdeal.enums import OrderSides, OrderTypes
except Exception:
    Spot = None
    OrderSides = None
    OrderTypes = None

DEFAULT_TOKEN = "9080285155:AAHuZFiALLtk9Sstk1jTFFZN0PwPLWFhgWM"
DEFAULT_CHAT_ID = "1951133060"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
PORT = int(os.environ.get("PORT", "10000"))

TABDEAL_API_KEY = os.environ.get("TABDEAL_API_KEY", "").strip()
TABDEAL_API_SECRET = os.environ.get("TABDEAL_API_SECRET", "").strip()

# برای امنیت: تا زمانی که خودت فعال نکردی سفارش واقعی ارسال نمی‌شود
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

# تایم‌فریم 5 دقیقه‌ای (300s) با نمونه‌برداری هر 20s
TIMEFRAME_SECONDS = int(os.environ.get("TIMEFRAME_SECONDS", "300"))
SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "20"))

# سرمایه فرضی اولیه: 10,000,000 تومان
INITIAL_IRT_BALANCE = float(os.environ.get("INITIAL_IRT_BALANCE", "10000000.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.10"))
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))

TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "1.2"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-0.85"))
BREAKEVEN_TRIGGER_PCT = float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "0.6"))
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "0.4"))
COOLDOWN_CANDLES = int(os.environ.get("COOLDOWN_CANDLES", "2"))

RUN_ID = str(uuid.uuid4())[:8]

IRT_SYMBOLS = [
    "BTC_IRT",
    "ETH_IRT",
    "TON_IRT",
    "NOT_IRT",
    "DOGS_IRT",
    "SOL_IRT",
    "TRX_IRT",
]

market_state = {
    sym: {
        "last_price": None,
        "last_updated": None,
        "candles_5m": [],
        "current_candle": None,
    }
    for sym in IRT_SYMBOLS
}

spot_client = (
    Spot(TABDEAL_API_KEY, TABDEAL_API_SECRET)
    if (Spot is not None and TABDEAL_API_KEY and TABDEAL_API_SECRET)
    else None
)
http_session = requests.Session()

last_update_id = 0
trade_counter = 0
cooldown_remaining = 0
trading_enabled = True
balance_irt = INITIAL_IRT_BALANCE
open_position = None
closed_trades = []
polling_thread = None
state_lock = threading.Lock()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"Tabdeal IRT 5m Trader ({RUN_ID}) active".encode("utf-8"))

    def log_message(self, format, *args):
        return


def start_web_server():
    try:
        HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()
    except Exception as e:
        print("Web server error:", e)


def send_telegram_message(text: str, chat_id: str = None):
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        return False
    try:
        res = http_session.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": target_chat, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        return res.status_code == 200
    except Exception as e:
        print("Telegram send error:", e)
        return False


def fetch_irt_prices_map():
    prices = {}

    if spot_client is not None:
        try:
            if hasattr(spot_client, "get_ticker_price"):
                for sym in IRT_SYMBOLS:
                    data = spot_client.get_ticker_price(symbol=sym)
                    p = data.get("price") if isinstance(data, dict) else None
                    if p is not None and float(p) > 0:
                        prices[sym] = float(p)
        except Exception as e:
            print("Tabdeal SDK price warning:", e)

    missing = [s for s in IRT_SYMBOLS if s not in prices]
    if missing:
        try:
            res = http_session.get("https://api1.tabdeal.org/r/api/v1/ticker/24hr", timeout=8)
            if res.status_code == 200:
                payload = res.json()
                items = payload if isinstance(payload, list) else payload.get("data", [])
                wanted = set(missing)
                for row in items:
                    sym = str(row.get("symbol") or "").upper()
                    if sym in wanted:
                        p = row.get("lastPrice") or row.get("price") or row.get("close")
                        if p is not None and float(p) > 0:
                            prices[sym] = float(p)
        except Exception as e:
            print("Tabdeal public ticker error:", e)

    return prices


def execute_tabdeal_order(symbol: str, side: str, quantity: float):
    if not LIVE_TRADING:
        return {"simulated": True, "symbol": symbol, "side": side, "quantity": quantity}
    if spot_client is None or OrderSides is None or OrderTypes is None:
        raise RuntimeError("کلاینت تبدیل یا کلیدهای API آماده نیستند.")

    order_side = OrderSides.BUY if side.upper() == "BUY" else OrderSides.SELL
    return spot_client.new_order(
        symbol=symbol,
        side=order_side,
        type=OrderTypes.MARKET,
        quantity=str(quantity),
    )


def update_5m_candles_irt():
    prices_map = fetch_irt_prices_map()
    if not prices_map:
        return False, False

    now_ts = int(time.time())
    bucket_ts = (now_ts // TIMEFRAME_SECONDS) * TIMEFRAME_SECONDS
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    any_closed = False

    with state_lock:
        for sym, price in prices_map.items():
            st = market_state[sym]
            st["last_price"] = price
            st["last_updated"] = now_str
            cur = st["current_candle"]

            if cur is None:
                st["current_candle"] = {
                    "bucket_ts": bucket_ts,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
            elif cur["bucket_ts"] == bucket_ts:
                cur["high"] = max(cur["high"], price)
                cur["low"] = min(cur["low"], price)
                cur["close"] = price
            else:
                st["candles_5m"].append(dict(cur))
                if len(st["candles_5m"]) > 120:
                    st["candles_5m"].pop(0)
                any_closed = True
                st["current_candle"] = {
                    "bucket_ts": bucket_ts,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }

    return True, any_closed


def calc_sma(values, period):
    if not values:
        return 0.0
    sub = values[-period:] if len(values) >= period else values
    return sum(sub) / len(sub)


def analyze_symbol_5m(sym: str):
    with state_lock:
        st = market_state[sym]
        closes = [c["close"] for c in st["candles_5m"]]
        last_c = st["candles_5m"][-1] if st["candles_5m"] else None

    if len(closes) < 3 or last_c is None or last_c["open"] <= 0:
        return 0, 0.0, "در انتظار تشکیل کندل‌های 5m"

    body_pct = ((last_c["close"] - last_c["open"]) / last_c["open"]) * 100.0
    sma3 = calc_sma(closes, 3)
    sma6 = calc_sma(closes, 6)

    score = 0
    reasons = []

    if 0.15 <= body_pct <= 1.5:
        score += 1
        reasons.append(f"کندل 5m سبز ({body_pct:+.2f}%)")
    elif body_pct > 1.5:
        score -= 1
        reasons.append("کندل کشیده")
    elif body_pct < -0.4:
        score -= 2
        reasons.append(f"کندل قرمز ({body_pct:+.2f}%)")

    if closes[-1] > sma3:
        score += 1
        reasons.append("بالای SMA3")
    else:
        score -= 1

    if sma3 > sma6:
        score += 1
        reasons.append("SMA3 > SMA6")

    return score, body_pct, "، ".join(reasons) if reasons else "خنثی"


def get_current_symbol_price(sym: str):
    with state_lock:
        st = market_state.get(sym)
        if not st:
            return None
        if st["current_candle"] and st["current_candle"].get("close"):
            return st["current_candle"]["close"]
        if st["candles_5m"]:
            return st["candles_5m"][-1]["close"]
        return st["last_price"]


def get_total_equity_irt():
    eq = balance_irt
    if open_position is not None:
        curr_p = get_current_symbol_price(open_position["symbol"])
        if curr_p and curr_p > 0:
            eq += open_position["qty"] * curr_p * (1.0 - FEE_RATE)
        else:
            eq += open_position["spent_irt"]
    return eq


def process_trading_step():
    global balance_irt, open_position, trade_counter, cooldown_remaining

    with state_lock:
        if not trading_enabled:
            return

    ok, any_closed = update_5m_candles_irt()
    if not ok:
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with state_lock:
        if not trading_enabled:
            return

        if open_position is not None:
            sym = open_position["symbol"]
            curr_price = get_current_symbol_price(sym)
            if not curr_price or curr_price <= 0:
                return

            entry_p = open_position["entry_price"]
            qty = open_position["qty"]
            pnl_pct = ((curr_price - entry_p) / entry_p) * 100.0
            peak_pct = max(open_position.get("peak_pct", pnl_pct), pnl_pct)
            open_position["peak_pct"] = peak_pct

            eff_sl = STOP_LOSS_PCT
            mode_txt = "حد ضرر کوتاه"

            if peak_pct >= BREAKEVEN_TRIGGER_PCT:
                eff_sl = max(eff_sl, (FEE_RATE * 200.0) + 0.05)
                mode_txt = "نقطه سر‌به‌سر"

            if peak_pct >= (BREAKEVEN_TRIGGER_PCT + TRAILING_STOP_PCT):
                eff_sl = max(eff_sl, peak_pct - TRAILING_STOP_PCT)
                mode_txt = "حد سود متحرک"

            exit_reason = None
            if pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"هدف سود کوتاه ({TAKE_PROFIT_PCT:+.2f}%)"
            elif pnl_pct <= eff_sl:
                exit_reason = f"{mode_txt} ({eff_sl:+.2f}%)"

            if exit_reason:
                try:
                    execute_tabdeal_order(sym, "SELL", qty)
                except Exception as e:
                    send_telegram_message(f"⚠️ خطا در خروج {sym}: <code>{e}</code>")
                    return

                gross_irt = qty * curr_price
                net_irt = gross_irt * (1.0 - FEE_RATE)
                profit_irt = net_irt - open_position["spent_irt"]
                net_pnl_pct = (profit_irt / open_position["spent_irt"]) * 100.0

                balance_irt += net_irt
                rec = {
                    "id": open_position["id"],
                    "symbol": sym,
                    "trade_type": open_position.get("trade_type", "چنج / خرید صعودی (LONG)"),
                    "entry_price": entry_p,
                    "exit_price": curr_price,
                    "profit_irt": profit_irt,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(rec)
                open_position = None

                if profit_irt < 0:
                    cooldown_remaining = COOLDOWN_CANDLES

                send_telegram_message(
                    f"📤 <b>خروج (چنج/فروش — SELL) #{rec['id']} | {sym}</b> [<code>{RUN_ID}</code>]\n"
                    f"• نوع معامله: <b>{rec['trade_type']}</b>\n"
                    f"• ورود: <code>{entry_p:,.0f} IRT</code> → خروج: <code>{curr_price:,.0f} IRT</code>\n"
                    f"• علت: <b>{exit_reason}</b>\n"
                    f"• سود/ضرر خالص: <code>{profit_irt:+,.0f} IRT</code> (<code>{net_pnl_pct:+.2f}%</code>)\n"
                    f"• موجودی فرضی جدید: <b>{balance_irt:,.0f} IRT</b>"
                )
            return

        if not any_closed:
            return

        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            return

    best_sym = None
    best_score = 1
    best_body = 0.0
    best_reason = ""

    for sym in IRT_SYMBOLS:
        sc, body_p, rsn = analyze_symbol_5m(sym)
        if sc >= 2 and sc > best_score:
            best_score = sc
            best_sym = sym
            best_body = body_p
            best_reason = rsn

    with state_lock:
        if not trading_enabled or open_position is not None or not best_sym:
            return

        curr_p = get_current_symbol_price(best_sym)
        if not curr_p or curr_p <= 0 or balance_irt < 50000:
            return

        spend_irt = round(balance_irt * TRADE_ALLOCATION_PCT, 0)
        if spend_irt <= 0:
            return

        eff_irt = spend_irt * (1.0 - FEE_RATE)
        qty = round(eff_irt / curr_p, 4)
        if qty <= 0:
            return

        try:
            execute_tabdeal_order(best_sym, "BUY", qty)
        except Exception as e:
            send_telegram_message(f"⚠️ خطا در ورود {best_sym}: <code>{e}</code>")
            return

        balance_irt -= spend_irt
        trade_counter += 1
        trade_type_label = f"چنج / خرید صعودی (LONG: IRT → {best_sym.replace('_IRT', '')})"
        open_position = {
            "id": trade_counter,
            "symbol": best_sym,
            "trade_type": trade_type_label,
            "entry_price": curr_p,
            "qty": qty,
            "spent_irt": spend_irt,
            "peak_pct": 0.0,
            "opened_at": now_str,
        }

        tp_p = curr_p * (1.0 + TAKE_PROFIT_PCT / 100.0)
        sl_p = curr_p * (1.0 + STOP_LOSS_PCT / 100.0)

        send_telegram_message(
            f"📥 <b>ورود کوتاه #{trade_counter} | {best_sym} (5m)</b> [<code>{RUN_ID}</code>]\n"
            f"• نوع معامله: <b>{trade_type_label}</b>\n"
            f"• سرمایه فرضی تخصیص‌یافته: <b>{spend_irt:,.0f} IRT</b>\n"
            f"• مقدار خرید: <code>{qty}</code> | قیمت ورود: <code>{curr_p:,.0f} IRT</code>\n"
            f"• امتیاز کندل 5m: <b>{best_score}</b> | بدنه کندل: <code>{best_body:+.2f}%</code>\n"
            f"• سیگنال: <i>{best_reason}</i>\n"
            f"• هدف سود کوتاه: <code>{tp_p:,.0f} IRT</code> ({TAKE_PROFIT_PCT:+.2f}%) | "
            f"حد ضرر: <code>{sl_p:,.0f} IRT</code> ({STOP_LOSS_PCT:+.2f}%)\n"
            f"• موجودی فرضی باقی‌مانده: <b>{balance_irt:,.0f} IRT</b>"
        )


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_irt"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_irt"] > 0)
        total_closed = len(closed_trades)
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        eq = get_total_equity_irt()
        ret_pct = ((eq - INITIAL_IRT_BALANCE) / INITIAL_IRT_BALANCE) * 100.0
        mode_s = "🟢 روشن (ترید کوتاه 5m تومانی)" if trading_enabled else "🔴 متوقف"
        live_s = "فعال (واقعی)" if LIVE_TRADING else "تست ایمن (موجودی فرضی)"
        pos_s = f"بله ({open_position['symbol']} | LONG/چنج)" if open_position else "خیر"
        cd_s = f"{cooldown_remaining} کندل" if cooldown_remaining > 0 else "خیر"

        return (
            f"📊 <b>وضعیت ربات تریدر تبدیل (تومانی | 5m)</b> [<code>{RUN_ID}</code>]\n"
            f"• وضعیت ترید: <b>{mode_s}</b> | حالت سفارش: <b>{live_s}</b>\n"
            f"• جفت‌ارزهای تومانی تحت نظر: <b>{len(IRT_SYMBOLS)} بازار IRT</b>\n"
            f"• موجودی فرضی نقد: <b>{balance_irt:,.0f} IRT</b>\n"
            f"• ارزش کل دارایی فرضی: <b>{eq:,.0f} IRT</b> (<code>{ret_pct:+.2f}%</code>)\n"
            f"• سود/ضرر خالص بسته: <code>{total_pnl:+,.0f} IRT</code>\n"
            f"• نرخ برد: <b>{wins}/{total_closed}</b> (<code>{win_rate:.1f}%</code>)\n"
            f"• استراحت پس از ضرر: <b>{cd_s}</b> | پوزیشن باز: <b>{pos_s}</b>"
        )


def build_stats_text():
    with state_lock:
        tot = len(closed_trades)
        wins = [t for t in closed_trades if t["profit_irt"] > 0]
        losses = [t for t in closed_trades if t["profit_irt"] < 0]
        net_pnl = sum(t["profit_irt"] for t in closed_trades)
        avg_pnl = (net_pnl / tot) if tot > 0 else 0.0
        win_rate = (len(wins) / tot * 100.0) if tot > 0 else 0.0
        return (
            f"📈 <b>آمار دقیق معاملات کوتاه تومانی (5m)</b> [<code>{RUN_ID}</code>]\n"
            f"• سرمایه فرضی اولیه: <b>{INITIAL_IRT_BALANCE:,.0f} IRT</b>\n"
            f"• کل معاملات بسته: <b>{tot}</b> | سودده: <b>{len(wins)}</b> | زیان‌ده: <b>{len(losses)}</b>\n"
            f"• نرخ برد: <b>{win_rate:.1f}%</b>\n"
            f"• سود/ضرر خالص کل: <code>{net_pnl:+,.0f} IRT</code>\n"
            f"• میانگین سود/ضرر هر معامله: <code>{avg_pnl:+,.0f} IRT</code>"
        )


def build_positions_text():
    with state_lock:
        if not open_position:
            return f"🔒 در حال حاضر پوزیشن بازی روی بازارهای تومانی وجود ندارد. [<code>{RUN_ID}</code>]"
        sym = open_position["symbol"]
        curr_p = get_current_symbol_price(sym) or open_position["entry_price"]
        entry_p = open_position["entry_price"]
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
        return (
            f"🔓 <b>پوزیشن باز #{open_position['id']} | {sym}</b> [<code>{RUN_ID}</code>]\n"
            f"• نوع معامله: <b>{open_position.get('trade_type', 'چنج / خرید صعودی (LONG)')}</b>\n"
            f"• سرمایه فرضی درگیر: <b>{open_position['spent_irt']:,.0f} IRT</b>\n"
            f"• قیمت ورود: <code>{entry_p:,.0f} IRT</code> | فعلی: <code>{curr_p:,.0f} IRT</code>\n"
            f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code>"
        )


def build_history_text():
    with state_lock:
        if not closed_trades:
            return f"📭 هنوز معامله بسته‌شده‌ای ثبت نشده است. [<code>{RUN_ID}</code>]"
        recent = closed_trades[-6:]
        lines = [f"📜 <b>آخرین {len(recent)} معامله کوتاه تومانی (5m)</b> [<code>{RUN_ID}</code>]"]
        for t in reversed(recent):
            lines.append(
                f"• #{t['id']} <b>{t['symbol']}</b> [LONG/چنج] | "
                f"خالص: <code>{t['profit_irt']:+,.0f} IRT</code> (<code>{t['pnl_pct']:+.2f}%</code>)"
            )
        return "\n".join(lines)


def reset_simulation():
    global balance_irt, open_position, closed_trades, trade_counter, cooldown_remaining
    with state_lock:
        balance_irt = INITIAL_IRT_BALANCE
        open_position = None
        closed_trades = []
        trade_counter = 0
        cooldown_remaining = 0


def handle_command(raw_text: str, chat_id: str):
    global trading_enabled
    cmd = raw_text.strip().split()[0].split("@")[0].lower()

    if cmd in ("/start", "/help"):
        send_telegram_message(
            f"🤖 <b>ربات تریدر کوتاه تبدیل (تومانی | 5m)</b> [<code>{RUN_ID}</code>]\n"
            "• /trade_on — شروع ترید کوتاه تومانی\n"
            "• /trade_off — قطع فوری ترید\n"
            "• /status — وضعیت کلی و موجودی فرضی\n"
            "• /stats — آمار دقیق سود/ضرر و نرخ برد\n"
            "• /positions — پوزیشن باز فعلی\n"
            "• /history — آخرین معاملات بسته\n"
            "• /reset — ریست موجودی فرضی به 10,000,000 IRT",
            chat_id,
        )
    elif cmd in ("/trade_on", "/run"):
        with state_lock:
            trading_enabled = True
        send_telegram_message(f"🟢 ترید کوتاه تومانی (5m) فعال شد. [<code>{RUN_ID}</code>]", chat_id)
    elif cmd in ("/trade_off", "/stop"):
        with state_lock:
            trading_enabled = False
        send_telegram_message(f"🛑 ترید کوتاه تومانی متوقف شد. [<code>{RUN_ID}</code>]", chat_id)
    elif cmd == "/status":
        send_telegram_message(build_status_text(), chat_id)
    elif cmd == "/stats":
        send_telegram_message(build_stats_text(), chat_id)
    elif cmd == "/positions":
        send_telegram_message(build_positions_text(), chat_id)
    elif cmd == "/history":
        send_telegram_message(build_history_text(), chat_id)
    elif cmd == "/reset":
        reset_simulation()
        send_telegram_message(
            f"🔄 موجودی فرضی ریست شد: <b>{INITIAL_IRT_BALANCE:,.0f} IRT</b> [<code>{RUN_ID}</code>]",
            chat_id,
        )


def poll_telegram_updates():
    global last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    while True:
        try:
            res = http_session.get(url, params={"offset": last_update_id + 1, "timeout": 15}, timeout=25)
            if res.status_code == 200:
                for upd in res.json().get("result", []):
                    uid = upd.get("update_id")
                    if uid is not None and uid >= last_update_id:
                        last_update_id = uid
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    text = (msg.get("text") or "").strip()
                    cid = str(msg.get("chat", {}).get("id", ""))
                    if cid and text and cid == TELEGRAM_CHAT_ID:
                        handle_command(text, cid)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(3)
        time.sleep(1)


def ensure_polling_alive():
    global polling_thread
    if polling_thread is None or not polling_thread.is_alive():
        polling_thread = threading.Thread(target=poll_telegram_updates, daemon=True)
        polling_thread.start()


def main_loop():
    send_telegram_message(
        f"🚀 <b>ربات تریدر کوتاه تبدیل (تومانی | 5m) آماده شد!</b> [<code>{RUN_ID}</code>]\n"
        f"• سرمایه فرضی: <b>{INITIAL_IRT_BALANCE:,.0f} IRT</b> (10 میلیون تومان)\n"
        f"• تایم‌فریم: <b>5 دقیقه‌ای</b> | هدف سود کوتاه: <code>{TAKE_PROFIT_PCT:+.2f}%</code> | حد ضرر: <code>{STOP_LOSS_PCT:+.2f}%</code>\n"
        f"• حالت سفارش: <b>{'واقعی (تبدیل)' if LIVE_TRADING else 'تست ایمن (LIVE_TRADING=False)'}</b>\n"
        f"• شروع ترید: <code>/trade_on</code> | قطع فوری: <code>/trade_off</code>"
    )
    while True:
        ensure_polling_alive()
        try:
            process_trading_step()
        except Exception as e:
            print("Main loop error:", e)
        time.sleep(SAMPLE_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    ensure_polling_alive()
    main_loop()
