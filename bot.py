import os
import time
import uuid
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

DEFAULT_TOKEN = "9080285155:AAHuZFiALLtk9Sstk1jTFFZN0PwPLWFhgWM"
DEFAULT_CHAT_ID = "1951133060"
DEFAULT_WALLET = "UQAtgSONTV8ee_tciJBCPsr-LJEAlpeYFfAJ3y6JucBhZXLS"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
PORT = int(os.environ.get("PORT", "10000"))

TONKEEPER_WALLET = os.environ.get("TONKEEPER_WALLET", DEFAULT_WALLET).strip()

# برای حفظ امنیت: تراکنش واقعی غیرفعال است؛ ترید با موجودی فرضی انجام می‌شود
LIVE_TRADING = False

# تایم‌فریم 5 دقیقه‌ای (300s) با نمونه‌برداری سریع‌تر (20s) برای رصد دقیق‌تر قیمت واقعی
TIMEFRAME_SECONDS = int(os.environ.get("TIMEFRAME_SECONDS", "300"))
SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "20"))

RUN_ID = str(uuid.uuid4())[:8]

# تنظیمات سرمایه فرضی و ترید کوتاه روی ارز NOT
INITIAL_TON_BALANCE = float(os.environ.get("INITIAL_TON_BALANCE", "1000.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.10"))
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))

TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "1.2"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-0.85"))
BREAKEVEN_TRIGGER_PCT = float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "0.6"))
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "0.4"))
COOLDOWN_CANDLES = int(os.environ.get("COOLDOWN_CANDLES", "2"))

NOT_CONFIG = {
    "symbol": "NOT",
    "coingecko_id": "notcoin",
    "jetton_address": "EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT",
}

ton_price_usd = None
ton_change_24h = 0.0
last_ton_fetch_time = None
last_not_usd = None
last_not_ton = None
last_not_fetch_time = None
last_price_source = None

wallet_real_ton = None
wallet_real_not = None
wallet_last_sync = None

candles_5m = []
current_candle = None

last_update_id = 0
trade_counter = 0

state_lock = threading.Lock()
trading_enabled = True
balance_ton = INITIAL_TON_BALANCE
open_position = None
closed_trades = []
cooldown_remaining = 0
polling_thread = None

http_session = requests.Session()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"NOT 5m Fast-Feed ({RUN_ID}) active".encode("utf-8"))

    def log_message(self, format, *args):
        return


def start_web_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        server.serve_forever()
    except Exception as e:
        print("Web server error:", e)


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
        res = http_session.post(url, json=payload, timeout=8)
        if res.status_code != 200:
            print("Telegram error:", res.status_code, res.text[:200])
        return res.status_code == 200
    except Exception as e:
        print("Telegram exception:", e)
        return False


def is_valid_ton_address(addr: str) -> bool:
    if not addr:
        return False
    addr = addr.strip()
    return len(addr) >= 40 and addr[:2] in ("EQ", "UQ", "kQ", "0Q")


def sync_tonkeeper_wallet():
    """دریافت موجودی واقعی TON و Jetton NOT کیف پول Tonkeeper از TonAPI (فقط نمایشی)"""
    global wallet_real_ton, wallet_real_not, wallet_last_sync

    with state_lock:
        addr = TONKEEPER_WALLET

    if not is_valid_ton_address(addr):
        return False

    try:
        acc_url = f"https://tonapi.io/v2/accounts/{addr}"
        acc_res = http_session.get(acc_url, timeout=8)
        ton_bal = None
        if acc_res.status_code == 200:
            nano_bal = acc_res.json().get("balance")
            if nano_bal is not None:
                ton_bal = float(nano_bal) / 1e9

        jetton_addr = NOT_CONFIG["jetton_address"]
        j_url = f"https://tonapi.io/v2/accounts/{addr}/jettons/{jetton_addr}"
        j_res = http_session.get(j_url, timeout=8)
        not_bal = None
        if j_res.status_code == 200:
            j_data = j_res.json()
            raw_bal = j_data.get("balance")
            decimals = int(j_data.get("jetton", {}).get("decimals", 9))
            if raw_bal is not None:
                not_bal = float(raw_bal) / (10 ** decimals)

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        with state_lock:
            if ton_bal is not None:
                wallet_real_ton = ton_bal
            if not_bal is not None:
                wallet_real_not = not_bal
            wallet_last_sync = now_str
        return True
    except Exception as e:
        print("Tonkeeper sync error:", e)
        return False


def fetch_real_ton_price():
    global ton_price_usd, ton_change_24h, last_ton_fetch_time

    try:
        url_gt = "https://api.geckoterminal.com/api/v2/networks/ton/tokens/EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
        res_gt = http_session.get(url_gt, headers={"Accept": "application/json"}, timeout=8)
        if res_gt.status_code == 200:
            attrs = res_gt.json().get("data", {}).get("attributes", {})
            if attrs.get("price_usd"):
                ton_price_usd = float(attrs["price_usd"])
                last_ton_fetch_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                return ton_price_usd
    except Exception as e:
        print("GeckoTerminal TON error:", e)

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "the-open-network",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        res = http_session.get(url, params=params, timeout=8)
        if res.status_code == 200:
            data = res.json().get("the-open-network")
            if data and data.get("usd"):
                ton_price_usd = float(data["usd"])
                ton_change_24h = float(data.get("usd_24h_change") or 0.0)
                last_ton_fetch_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                return ton_price_usd
    except Exception as e:
        print("CoinGecko TON error:", e)

    return None


def fetch_real_not_usd_price():
    global last_price_source

    try:
        addr = NOT_CONFIG["jetton_address"].lower()
        url_gt = f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{addr}"
        res_gt = http_session.get(url_gt, headers={"Accept": "application/json"}, timeout=8)
        if res_gt.status_code == 200:
            p_usd = res_gt.json().get("data", {}).get("attributes", {}).get("price_usd")
            if p_usd is not None and float(p_usd) > 0:
                last_price_source = "GeckoTerminal (TON)"
                return float(p_usd)
    except Exception as e:
        print("GeckoTerminal NOT error:", e)

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": NOT_CONFIG["coingecko_id"], "vs_currencies": "usd"}
        res = http_session.get(url, params=params, timeout=8)
        if res.status_code == 200:
            val = res.json().get(NOT_CONFIG["coingecko_id"], {}).get("usd")
            if val is not None and float(val) > 0:
                last_price_source = "CoinGecko"
                return float(val)
    except Exception as e:
        print("CoinGecko NOT error:", e)

    return None


def update_5m_candles():
    global last_not_usd, last_not_ton, last_not_fetch_time, current_candle, candles_5m

    ton_usd = fetch_real_ton_price()
    if not ton_usd or ton_usd <= 0:
        return False, False

    not_usd = fetch_real_not_usd_price()
    if not not_usd or not_usd <= 0:
        return False, False

    not_in_ton = not_usd / ton_usd
    now_ts = int(time.time())
    bucket_ts = (now_ts // TIMEFRAME_SECONDS) * TIMEFRAME_SECONDS
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    candle_closed = False

    with state_lock:
        last_not_usd = not_usd
        last_not_ton = not_in_ton
        last_not_fetch_time = now_str

        if current_candle is None:
            current_candle = {
                "bucket_ts": bucket_ts,
                "open": not_in_ton,
                "high": not_in_ton,
                "low": not_in_ton,
                "close": not_in_ton,
            }
        elif current_candle["bucket_ts"] == bucket_ts:
            current_candle["high"] = max(current_candle["high"], not_in_ton)
            current_candle["low"] = min(current_candle["low"], not_in_ton)
            current_candle["close"] = not_in_ton
        else:
            candles_5m.append(dict(current_candle))
            if len(candles_5m) > 120:
                candles_5m.pop(0)
            candle_closed = True

            current_candle = {
                "bucket_ts": bucket_ts,
                "open": not_in_ton,
                "high": not_in_ton,
                "low": not_in_ton,
                "close": not_in_ton,
            }

    return True, candle_closed


def calc_sma(closes, period):
    if not closes:
        return 0.0
    sub = closes[-period:] if len(closes) >= period else closes
    return sum(sub) / len(sub)


def analyze_not_5m():
    with state_lock:
        closes = [c["close"] for c in candles_5m]
        last_c = candles_5m[-1] if candles_5m else None

    if len(closes) < 3 or last_c is None:
        return 0, 0.0, "در انتظار تشکیل حداقل ۳ کندل ۵ دقیقه‌ای"

    prev_close = closes[-2]
    curr_close = closes[-1]
    if prev_close <= 0 or last_c["open"] <= 0:
        return 0, 0.0, "داده کندل نامعتبر"

    candle_body_pct = ((last_c["close"] - last_c["open"]) / last_c["open"]) * 100.0
    step_pct = ((curr_close - prev_close) / prev_close) * 100.0

    sma3 = calc_sma(closes, 3)
    sma6 = calc_sma(closes, 6)

    score = 0
    reasons = []

    if 0.15 <= candle_body_pct <= 1.5:
        score += 1
        reasons.append(f"کندل 5m سبز ({candle_body_pct:+.2f}%)")
    elif candle_body_pct > 1.5:
        score -= 1
        reasons.append("کندل کشیده (احتمال اصلاح)")
    elif candle_body_pct < -0.4:
        score -= 2
        reasons.append(f"کندل قرمز ({candle_body_pct:+.2f}%)")

    if curr_close > sma3:
        score += 1
        reasons.append("بالای SMA3(5m)")
    else:
        score -= 1

    if sma3 > sma6:
        score += 1
        reasons.append("روند کوتاه صعودی")

    if ton_change_24h < -4.0:
        score -= 1
        reasons.append("TON تحت فشار")

    return score, step_pct, "، ".join(reasons) if reasons else "خنثی"


def get_current_not_ton_price():
    with state_lock:
        if current_candle and current_candle.get("close"):
            return current_candle["close"]
        if candles_5m:
            return candles_5m[-1]["close"]
        return last_not_ton


def process_short_term_step():
    global balance_ton, open_position, trade_counter, cooldown_remaining

    with state_lock:
        if not trading_enabled:
            return

    has_price, candle_closed = update_5m_candles()
    if not has_price:
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    curr_price = get_current_not_ton_price()
    if not curr_price or curr_price <= 0:
        return

    with state_lock:
        if not trading_enabled:
            return

        if open_position is not None:
            entry_price = open_position["entry_price_ton"]
            token_qty = open_position["token_qty"]

            pnl_pct = ((curr_price - entry_price) / entry_price) * 100.0
            peak_pnl_pct = max(open_position.get("peak_pnl_pct", pnl_pct), pnl_pct)
            open_position["peak_pnl_pct"] = peak_pnl_pct

            effective_sl = STOP_LOSS_PCT
            exit_mode = "حد ضرر کوتاه"

            if peak_pnl_pct >= BREAKEVEN_TRIGGER_PCT:
                be_floor = (FEE_RATE * 2 * 100.0) + 0.05
                effective_sl = max(effective_sl, be_floor)
                exit_mode = "نقطه سر‌به‌سر (Break-even)"

            if peak_pnl_pct >= (BREAKEVEN_TRIGGER_PCT + TRAILING_STOP_PCT):
                trailing_sl = peak_pnl_pct - TRAILING_STOP_PCT
                effective_sl = max(effective_sl, trailing_sl)
                exit_mode = "حد سود متحرک کوتاه"

            exit_reason = None
            if pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"هدف سود کوتاه ({TAKE_PROFIT_PCT:+.2f}%)"
            elif pnl_pct <= effective_sl:
                exit_reason = f"{exit_mode} ({effective_sl:+.2f}%)"

            if exit_reason:
                gross_ton = token_qty * curr_price
                exit_fee_ton = gross_ton * FEE_RATE
                net_ton = gross_ton - exit_fee_ton
                profit_ton = net_ton - open_position["spent_ton"]
                net_pnl_pct = (profit_ton / open_position["spent_ton"]) * 100.0

                balance_ton += net_ton
                record = {
                    "id": open_position["id"],
                    "symbol": "NOT",
                    "trade_type": open_position.get("trade_type", "چنج / خرید صعودی (LONG)"),
                    "entry_price": entry_price,
                    "exit_price": curr_price,
                    "profit_ton": profit_ton,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(record)
                open_position = None

                if profit_ton < 0:
                    cooldown_remaining = COOLDOWN_CANDLES

                msg = (
                    f"📤 <b>خروج کوتاه معامله #{record['id']} (NOT - 5m | فرضی)</b> [<code>{RUN_ID}</code>]\n"
                    f"🔄 نوع معامله: <b>چنج برگشتی (NOT → TON)</b> | جهت اصلی: <b>لانگ / صعودی (LONG)</b>\n"
                    f"📥 ورود: <code>{entry_price:.8f} TON</code>\n"
                    f"📤 خروج: <code>{curr_price:.8f} TON</code>\n"
                    f"📌 علت: <b>{exit_reason}</b>\n"
                    f"💰 سود/ضرر خالص: <code>{profit_ton:+.4f} TON</code> (<code>{net_pnl_pct:+.2f}%</code>)\n"
                    f"💼 موجودی فرضی جدید: <b>{balance_ton:.4f} TON</b>\n"
                    f"🕒 زمان: <code>{now_str}</code>"
                )
                send_telegram_message(msg)
            return

        if not candle_closed:
            return

        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            return

    sc, step_pct, reason_txt = analyze_not_5m()

    with state_lock:
        if not trading_enabled or open_position is not None:
            return

        if sc >= 2 and balance_ton > 5.0:
            spend_ton = round(balance_ton * TRADE_ALLOCATION_PCT, 4)
            if spend_ton <= 0:
                return
            entry_fee_ton = spend_ton * FEE_RATE
            effective_ton = spend_ton - entry_fee_ton
            token_qty = effective_ton / curr_price

            balance_ton -= spend_ton
            trade_counter += 1
            trade_type_label = "چنج / خرید صعودی (LONG: TON → NOT)"
            open_position = {
                "id": trade_counter,
                "symbol": "NOT",
                "trade_type": trade_type_label,
                "entry_price_ton": curr_price,
                "token_qty": token_qty,
                "spent_ton": spend_ton,
                "peak_pnl_pct": 0.0,
                "opened_at": now_str,
            }

            tp_price = curr_price * (1.0 + TAKE_PROFIT_PCT / 100.0)
            sl_price = curr_price * (1.0 + STOP_LOSS_PCT / 100.0)

            msg = (
                f"📥 <b>ورود کوتاه معامله #{trade_counter} (NOT - 5m | فرضی)</b> [<code>{RUN_ID}</code>]\n"
                f"🔄 نوع معامله: <b>چنج / خرید صعودی — LONG (TON → NOT)</b>\n"
                f"💵 سرمایه فرضی تخصیص‌یافته: <b>{spend_ton:.4f} TON</b>\n"
                f"🎯 قیمت ورود: <code>{curr_price:.8f} TON</code>\n"
                f"📊 امتیاز کندل 5m: <b>{sc}</b> | تغییر کندل: <code>{step_pct:+.2f}%</code>\n"
                f"🧠 سیگنال: <i>{reason_txt}</i>\n"
                f"🟢 هدف سود کوتاه: <code>{tp_price:.8f} TON</code> ({TAKE_PROFIT_PCT:+.2f}%)\n"
                f"🔴 حد ضرر کوتاه: <code>{sl_price:.8f} TON</code> ({STOP_LOSS_PCT:+.2f}%)\n"
                f"💼 موجودی فرضی باقی‌مانده: <b>{balance_ton:.4f} TON</b>\n"
                f"🕒 زمان: <code>{now_str}</code>"
            )
            send_telegram_message(msg)


def get_total_equity_ton():
    equity = balance_ton
    if open_position is not None:
        curr_p = get_current_not_ton_price()
        if curr_p and curr_p > 0:
            equity += open_position["token_qty"] * curr_p * (1.0 - FEE_RATE)
        else:
            equity += open_position["spent_ton"]
    return equity


def build_wallet_text():
    with state_lock:
        addr = TONKEEPER_WALLET
        if not addr:
            return (
                f"👛 <b>کیف پول Tonkeeper متصل نیست</b> [<code>{RUN_ID}</code>]\n"
                "برای اتصال آدرس عمومی کیف پول Tonkeeper:\n"
                "<code>/wallet آدرس_کیف_پول_شما</code>"
            )

        ton_b = f"{wallet_real_ton:.4f} TON" if wallet_real_ton is not None else "در حال همگام‌سازی..."
        not_b = f"{wallet_real_not:,.2f} NOT" if wallet_real_not is not None else "در حال همگام‌سازی..."
        ton_p = f"${ton_price_usd:.4f}" if ton_price_usd else "نامشخص"
        not_p_usd = f"${last_not_usd:.6f}" if last_not_usd else "نامشخص"
        not_p_ton = f"{last_not_ton:.8f} TON" if last_not_ton else "نامشخص"
        src_str = last_price_source or "نامشخص"
        sync_t = wallet_last_sync or "هنوز همگام نشده"

        return (
            f"👛 <b>وضعیت کیف پول متصل Tonkeeper (نمایشی)</b> [<code>{RUN_ID}</code>]\n"
            f"• آدرس متصل: <code>{addr}</code>\n"
            f"• موجودی واقعی TON: <b>{ton_b}</b>\n"
            f"• موجودی واقعی NOT: <b>{not_b}</b>\n"
            f"• قیمت واقعی TON: <b>{ton_p}</b>\n"
            f"• قیمت واقعی NOT: <code>{not_p_ton}</code> (<code>{not_p_usd}</code>)\n"
            f"• منبع قیمت: <b>{src_str}</b> (هر {SAMPLE_SECONDS}s)\n"
            f"• حالت ترید: <b>موجودی فرضی ({INITIAL_TON_BALANCE:.0f} TON)</b>\n"
            f"• آخرین همگام‌سازی: <code>{sync_t}</code>"
        )


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_ton"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_ton"] > 0)
        total_closed = len(closed_trades)
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        total_equity = get_total_equity_ton()
        total_return_pct = ((total_equity - INITIAL_TON_BALANCE) / INITIAL_TON_BALANCE) * 100.0

        mode_str = "🟢 روشن (ترید فرضی 5m روی NOT)" if trading_enabled else "🔴 متوقف (قطع‌شده)"
        ton_str = f"${ton_price_usd:.4f}" if ton_price_usd else "در انتظار"
        not_str = f"{last_not_ton:.8f} TON" if last_not_ton else "در انتظار"
        not_usd_str = f"${last_not_usd:.6f}" if last_not_usd else "—"
        wallet_str = f"متصل (<code>{TONKEEPER_WALLET[:8]}...</code>)" if TONKEEPER_WALLET else "متصل نشده"
        c_count = len(candles_5m)
        cd_str = f"{cooldown_remaining} کندل" if cooldown_remaining > 0 else "خیر"
        pos_str = f"بله (NOT | LONG/چنج)" if open_position else "خیر"

        return (
            f"📊 <b>وضعیت ربات تریدر NOT (5m)</b> [<code>{RUN_ID}</code>]\n"
            f"⚙️ وضعیت ترید: <b>{mode_str}</b>\n"
            f"🔄 سبک معاملات: <b>چنج / خرید صعودی (LONG)</b>\n"
            f"👛 کیف پول Tonkeeper: {wallet_str}\n"
            f"🪙 کندل‌های 5m بسته‌شده: <b>{c_count}</b> | بازه رصد: <b>{SAMPLE_SECONDS}s</b>\n"
            f"💎 قیمت NOT: <code>{not_str}</code> (<code>{not_usd_str}</code>)\n"
            f"💵 قیمت TON: <b>{ton_str}</b> (۲۴h: <code>{ton_change_24h:+.2f}%</code>)\n"
            f"💼 موجودی فرضی نقد: <b>{balance_ton:.4f} TON</b>\n"
            f"💎 ارزش کل دارایی فرضی: <b>{total_equity:.4f} TON</b> (<code>{total_return_pct:+.2f}%</code>)\n"
            f"📈 سود/ضرر خالص بسته: <code>{total_pnl:+.4f} TON</code>\n"
            f"🏆 نرخ برد: <b>{wins}/{total_closed}</b> (<code>{win_rate:.1f}%</code>)\n"
            f"⏳ استراحت پس از ضرر: <b>{cd_str}</b>\n"
            f"🔓 پوزیشن باز: <b>{pos_str}</b>"
        )


def build_stats_text():
    with state_lock:
        total_closed = len(closed_trades)
        wins = [t for t in closed_trades if t["profit_ton"] > 0]
        losses = [t for t in closed_trades if t["profit_ton"] < 0]
        net_pnl = sum(t["profit_ton"] for t in closed_trades)
        avg_pnl = (net_pnl / total_closed) if total_closed > 0 else 0.0
        avg_win = (sum(t["profit_ton"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t["profit_ton"] for t in losses) / len(losses)) if losses else 0.0
        win_rate = (len(wins) / total_closed * 100.0) if total_closed > 0 else 0.0

        return (
            f"📈 <b>آمار دقیق معاملات فرضی NOT (5m | LONG/چنج)</b> [<code>{RUN_ID}</code>]\n"
            f"• سرمایه فرضی اولیه: <b>{INITIAL_TON_BALANCE:.2f} TON</b>\n"
            f"• کل معاملات بسته‌شده: <b>{total_closed}</b>\n"
            f"• سودده: <b>{len(wins)}</b> | زیان‌ده: <b>{len(losses)}</b>\n"
            f"• نرخ برد: <b>{win_rate:.1f}%</b>\n"
            f"• سود/ضرر خالص کل: <code>{net_pnl:+.4f} TON</code>\n"
            f"• میانگین هر معامله: <code>{avg_pnl:+.4f} TON</code>\n"
            f"• میانگین سودده: <code>{avg_win:+.4f} TON</code> | میانگین زیان‌ده: <code>{avg_loss:+.4f} TON</code>"
        )


def build_positions_text():
    with state_lock:
        if not open_position:
            return f"🔒 در حال حاضر پوزیشن بازی روی NOT وجود ندارد. [<code>{RUN_ID}</code>]"
        curr_p = get_current_not_ton_price() or open_position["entry_price_ton"]
        entry_p = open_position["entry_price_ton"]
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
        peak_pct = open_position.get("peak_pnl_pct", pnl_pct)
        t_type = open_position.get("trade_type", "چنج / خرید صعودی (LONG: TON → NOT)")
        return (
            f"🔓 <b>پوزیشن باز کوتاه #{open_position['id']} (NOT | فرضی)</b> [<code>{RUN_ID}</code>]\n"
            f"• نوع معامله: <b>{t_type}</b>\n"
            f"• سرمایه فرضی درگیر: <b>{open_position['spent_ton']:.4f} TON</b>\n"
            f"• قیمت ورود: <code>{entry_p:.8f} TON</code>\n"
            f"• قیمت فعلی: <code>{curr_p:.8f} TON</code>\n"
            f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code> (اوج: <code>{peak_pct:+.2f}%</code>)"
        )


def build_history_text():
    with state_lock:
        if not closed_trades:
            return f"📭 هنوز هیچ معامله بسته‌شده‌ای روی NOT ثبت نشده است. [<code>{RUN_ID}</code>]"
        recent = closed_trades[-6:]
        lines = [f"📜 <b>آخرین {len(recent)} معامله کوتاه NOT (5m | LONG/چنج)</b> [<code>{RUN_ID}</code>]"]
        for t in reversed(recent):
            lines.append(
                f"• #{t['id']} [LONG/چنج] ورود: <code>{t['entry_price']:.8f}</code> → "
                f"خروج: <code>{t['exit_price']:.8f}</code> | "
                f"خالص: <code>{t['profit_ton']:+.4f} TON</code> (<code>{t['pnl_pct']:+.2f}%</code>)"
            )
        return "\n".join(lines)


def reset_simulation():
    global balance_ton, open_position, closed_trades, trade_counter, cooldown_remaining
    with state_lock:
        balance_ton = INITIAL_TON_BALANCE
        open_position = None
        closed_trades = []
        trade_counter = 0
        cooldown_remaining = 0


def handle_command(raw_text: str, chat_id: str):
    global trading_enabled, TONKEEPER_WALLET

    parts = raw_text.strip().split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()

    if cmd in ("/start", "/help"):
        send_telegram_message(
            f"🤖 <b>ربات تریدر کوتاه NOT (5m) + Tonkeeper</b> [<code>{RUN_ID}</code>]\n"
            "• /trade_on — شروع ترید فرضی روی NOT\n"
            "• /trade_off — قطع فوری ترید\n"
            "• /wallet — نمایش موجودی و قیمت‌های کیف پول متصل\n"
            "• /status — وضعیت کلی و قیمت‌ها\n"
            "• /stats — آمار دقیق سود/ضرر و نرخ برد\n"
            "• /positions — وضعیت پوزیشن باز\n"
            "• /history — آخرین معاملات بسته\n"
            "• /reset — ریست موجودی فرضی به 1000 TON",
            chat_id=chat_id,
        )
    elif cmd in ("/trade_on", "/run"):
        with state_lock:
            trading_enabled = True
        send_telegram_message(
            f"🟢 <b>ترید فرضی روی NOT (5m) شروع شد!</b> [<code>{RUN_ID}</code>]",
            chat_id=chat_id,
        )
    elif cmd in ("/trade_off", "/stop"):
        with state_lock:
            trading_enabled = False
        send_telegram_message(
            f"🛑 <b>ترید روی NOT متوقف شد!</b> [<code>{RUN_ID}</code>]",
            chat_id=chat_id,
        )
    elif cmd == "/wallet":
        if len(parts) >= 2:
            candidate = parts[1].strip()
            if not is_valid_ton_address(candidate):
                send_telegram_message(
                    f"⚠️ آدرس کیف پول TON نامعتبر است. [<code>{RUN_ID}</code>]",
                    chat_id=chat_id,
                )
                return
            with state_lock:
                TONKEEPER_WALLET = candidate
        sync_tonkeeper_wallet()
        send_telegram_message(build_wallet_text(), chat_id=chat_id)
    elif cmd == "/status":
        sync_tonkeeper_wallet()
        send_telegram_message(build_status_text(), chat_id=chat_id)
    elif cmd == "/stats":
        send_telegram_message(build_stats_text(), chat_id=chat_id)
    elif cmd == "/positions":
        send_telegram_message(build_positions_text(), chat_id=chat_id)
    elif cmd == "/history":
        send_telegram_message(build_history_text(), chat_id=chat_id)
    elif cmd == "/reset":
        reset_simulation()
        send_telegram_message(
            f"🔄 موجودی فرضی ریست شد: <b>{INITIAL_TON_BALANCE:.2f} TON</b> [<code>{RUN_ID}</code>]",
            chat_id=chat_id,
        )


def poll_telegram_updates():
    global last_update_id
    session = requests.Session()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 15}
            res = session.get(url, params=params, timeout=25)
            if res.status_code == 200:
                data = res.json()
                for upd in data.get("result", []):
                    upd_id = upd.get("update_id")
                    if upd_id is not None and upd_id >= last_update_id:
                        last_update_id = upd_id
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not chat_id or not text:
                        continue
                    if chat_id != TELEGRAM_CHAT_ID:
                        continue
                    handle_command(text, chat_id)
            else:
                print("getUpdates status:", res.status_code, res.text[:200])
                time.sleep(3)
        except Exception as e:
            print("Polling loop warning:", e)
            time.sleep(3)
        time.sleep(1)


def ensure_polling_alive():
    global polling_thread
    if polling_thread is None or not polling_thread.is_alive():
        polling_thread = threading.Thread(target=poll_telegram_updates, daemon=True)
        polling_thread.start()


def trading_loop():
    sync_tonkeeper_wallet()
    send_telegram_message(
        f"🚀 <b>ربات تریدر کوتاه NOT (5m) + Tonkeeper آماده شد!</b> [<code>{RUN_ID}</code>]\n"
        f"• کیف پول متصل: <code>{TONKEEPER_WALLET[:8]}...{TONKEEPER_WALLET[-6:]}</code>\n"
        f"• سبک معاملات: <b>چنج / خرید صعودی (LONG)</b> | حالت: <b>فرضی ({INITIAL_TON_BALANCE:.0f} TON)</b>\n"
        f"• شروع ترید: <code>/trade_on</code> | قطع فوری: <code>/trade_off</code>"
    )

    while True:
        ensure_polling_alive()
        try:
            process_short_term_step()
        except Exception as e:
            print("Trading step error:", e)

        time.sleep(SAMPLE_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    ensure_polling_alive()
    trading_loop()
