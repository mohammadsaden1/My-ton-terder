import os
import time
import uuid
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

INITIAL_TON_BALANCE = float(os.environ.get("INITIAL_TON_BALANCE", "1000.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.10"))
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "2.5"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-1.5"))
BREAKEVEN_TRIGGER_PCT = float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "1.0"))
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "0.8"))
COOLDOWN_CYCLES = int(os.environ.get("COOLDOWN_CYCLES", "3"))

TOKENS = {
    "NOT": {
        "coingecko_id": "notcoin",
        "jetton_address": "EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT",
        "history_ton": [],
        "last_usd": None,
        "last_updated": None,
    },
    "DOGS": {
        "coingecko_id": "dogs-2",
        "jetton_address": "EQCvxJy4eG8hyHBFsZ7yRpDRrs1N7CJ9g64BOoHVFz2hSgB9",
        "history_ton": [],
        "last_usd": None,
        "last_updated": None,
    },
    "STON": {
        "coingecko_id": "ston",
        "jetton_address": "EQA2kCVNwVsil2EM2mB0SkXytxCqQjS4mttjDpnX7GUM56OF",
        "history_ton": [],
        "last_usd": None,
        "last_updated": None,
    },
}

ton_price_usd = None
ton_change_24h = 0.0
last_ton_fetch_time = None
last_update_id = 0
trade_counter = 0

state_lock = threading.Lock()
trading_enabled = True
balance_ton = INITIAL_TON_BALANCE
open_position = None
closed_trades = []
cooldown_remaining = 0
polling_thread = None


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"TON Real-Data Trader ({RUN_ID}) active".encode("utf-8"))

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
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print("Telegram error:", res.status_code, res.text[:200])
        return res.status_code == 200
    except Exception as e:
        print("Telegram exception:", e)
        return False


def fetch_real_ton_price():
    global ton_price_usd, ton_change_24h, last_ton_fetch_time

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "the-open-network",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json().get("the-open-network")
        if data and data.get("usd"):
            ton_price_usd = float(data["usd"])
            ton_change_24h = float(data.get("usd_24h_change") or 0.0)
            last_ton_fetch_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            return ton_price_usd, ton_change_24h
    except Exception as e:
        print("CoinGecko TON error:", e)

    try:
        url_gt = "https://api.geckoterminal.com/api/v2/networks/ton/tokens/EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
        res_gt = requests.get(url_gt, headers={"Accept": "application/json"}, timeout=10)
        res_gt.raise_for_status()
        attrs = res_gt.json().get("data", {}).get("attributes", {})
        if attrs.get("price_usd"):
            ton_price_usd = float(attrs["price_usd"])
            last_ton_fetch_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            return ton_price_usd, ton_change_24h
    except Exception as e:
        print("GeckoTerminal TON error:", e)

    return None, ton_change_24h


def fetch_real_token_usd_prices():
    usd_prices = {}

    cg_map = {
        info["coingecko_id"]: sym
        for sym, info in TOKENS.items()
        if info.get("coingecko_id")
    }
    if cg_map:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": ",".join(cg_map.keys()), "vs_currencies": "usd"}
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                payload = res.json()
                for cg_id, sym in cg_map.items():
                    val = payload.get(cg_id, {}).get("usd")
                    if val is not None and float(val) > 0:
                        usd_prices[sym] = float(val)
        except Exception as e:
            print("CoinGecko tokens error:", e)

    missing_syms = [s for s in TOKENS if s not in usd_prices and TOKENS[s].get("jetton_address")]
    if missing_syms:
        addrs = ",".join(TOKENS[s]["jetton_address"].lower() for s in missing_syms)
        addr_to_sym = {TOKENS[s]["jetton_address"].lower(): s for s in missing_syms}
        try:
            url_gt = f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/multi/{addrs}"
            res_gt = requests.get(url_gt, headers={"Accept": "application/json"}, timeout=10)
            if res_gt.status_code == 200:
                items = res_gt.json().get("data", [])
                for item in items:
                    attrs = item.get("attributes", {})
                    addr = (attrs.get("address") or "").lower()
                    p_usd = attrs.get("price_usd")
                    if addr in addr_to_sym and p_usd is not None and float(p_usd) > 0:
                        usd_prices[addr_to_sym[addr]] = float(p_usd)
        except Exception as e:
            print("GeckoTerminal tokens error:", e)

    return usd_prices


def update_real_market_data():
    ton_usd, _ = fetch_real_ton_price()
    if not ton_usd or ton_usd <= 0:
        return False

    token_usd_map = fetch_real_token_usd_prices()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    updated_any = False
    for sym, tok_usd in token_usd_map.items():
        price_in_ton = tok_usd / ton_usd
        if price_in_ton > 0:
            info = TOKENS[sym]
            info["last_usd"] = tok_usd
            info["last_updated"] = now_str
            info["history_ton"].append(price_in_ton)
            if len(info["history_ton"]) > 120:
                info["history_ton"].pop(0)
            updated_any = True

    return updated_any


def calc_sma(values, period):
    if not values:
        return 0.0
    sub = values[-period:] if len(values) >= period else values
    return sum(sub) / len(sub)


def analyze_token_real(sym: str):
    hist = TOKENS[sym]["history_ton"]
    if len(hist) < 3:
        return 0, 0.0, "در انتظار جمع‌آوری کندل‌های واقعی بیشتر"

    prev_p, curr_p = hist[-2], hist[-1]
    if prev_p <= 0:
        return 0, 0.0, "داده نامعتبر"

    delta_pct = ((curr_p - prev_p) / prev_p) * 100.0
    sma_fast = calc_sma(hist, 3)
    sma_slow = calc_sma(hist, 6)

    score = 0
    reasons = []

    if delta_pct > 0.25:
        score += 1
        reasons.append(f"رشد واقعی ({delta_pct:+.2f}%)")
    elif delta_pct < -0.5:
        score -= 2
        reasons.append(f"افت واقعی ({delta_pct:+.2f}%)")

    if curr_p > sma_fast:
        score += 1
        reasons.append("بالای SMA3")
    else:
        score -= 1

    if sma_fast > sma_slow:
        score += 1
        reasons.append("SMA3 > SMA6")
    elif sma_fast < sma_slow:
        score -= 1

    if ton_change_24h > 0.0:
        score += 1
        reasons.append("TON مثبت")
    elif ton_change_24h < -3.0:
        score -= 2
        reasons.append("TON نزولی")

    return score, delta_pct, "، ".join(reasons) if reasons else "خنثی"


def process_trading_step():
    global balance_ton, open_position, trade_counter, cooldown_remaining

    with state_lock:
        if not trading_enabled:
            return

    ok = update_real_market_data()
    if not ok:
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with state_lock:
        if not trading_enabled:
            return

        if open_position is not None:
            sym = open_position["symbol"]
            hist = TOKENS[sym]["history_ton"]
            if not hist:
                return

            curr_price = hist[-1]
            entry_price = open_position["entry_price_ton"]
            token_qty = open_position["token_qty"]

            pnl_pct = ((curr_price - entry_price) / entry_price) * 100.0
            peak_pnl_pct = max(open_position.get("peak_pnl_pct", pnl_pct), pnl_pct)
            open_position["peak_pnl_pct"] = peak_pnl_pct

            effective_sl = STOP_LOSS_PCT
            mode_tag = "حد ضرر اولیه"

            if peak_pnl_pct >= BREAKEVEN_TRIGGER_PCT:
                be_floor = (FEE_RATE * 2 * 100.0) + 0.1
                effective_sl = max(effective_sl, be_floor)
                mode_tag = "نقطه سر‌به‌سر (Break-even)"

            if peak_pnl_pct >= (BREAKEVEN_TRIGGER_PCT + TRAILING_STOP_PCT):
                trailing_sl = peak_pnl_pct - TRAILING_STOP_PCT
                effective_sl = max(effective_sl, trailing_sl)
                mode_tag = "حد سود متحرک (Trailing)"

            exit_reason = None
            if pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"رسیدن به حد سود ({TAKE_PROFIT_PCT:+.1f}%)"
            elif pnl_pct <= effective_sl:
                exit_reason = f"{mode_tag} ({effective_sl:+.2f}%)"

            if exit_reason:
                gross_ton = token_qty * curr_price
                exit_fee_ton = gross_ton * FEE_RATE
                net_ton = gross_ton - exit_fee_ton
                profit_ton = net_ton - open_position["spent_ton"]
                net_pnl_pct = (profit_ton / open_position["spent_ton"]) * 100.0

                balance_ton += net_ton
                record = {
                    "id": open_position["id"],
                    "symbol": sym,
                    "entry_price": entry_price,
                    "exit_price": curr_price,
                    "spent_ton": open_position["spent_ton"],
                    "net_ton": net_ton,
                    "profit_ton": profit_ton,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(record)
                open_position = None

                if profit_ton < 0:
                    cooldown_remaining = COOLDOWN_CYCLES

                msg = (
                    f"📤 <b>گزارش خروج واقعی معامله #{record['id']}</b> [<code>{RUN_ID}</code>]\n"
                    f"🪙 توکن: <b>{sym}</b> (چنج به TON)\n"
                    f"📥 قیمت ورود: <code>{entry_price:.8f} TON</code>\n"
                    f"📤 قیمت خروج: <code>{curr_price:.8f} TON</code>\n"
                    f"📌 علت خروج: <b>{exit_reason}</b>\n"
                    f"💰 سود/ضرر خالص: <code>{profit_ton:+.4f} TON</code> (<code>{net_pnl_pct:+.2f}%</code>)\n"
                    f"💼 موجودی جدید: <b>{balance_ton:.4f} TON</b>\n"
                    f"🕒 زمان: <code>{now_str}</code>"
                )
                send_telegram_message(msg)
            return

        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            return

        best_sym = None
        best_score = 2
        best_delta = 0.0
        best_reason = ""

        for sym in TOKENS:
            if not TOKENS[sym]["history_ton"]:
                continue
            sc, dlt, rsn = analyze_token_real(sym)
            if sc >= 3 and sc > best_score:
                best_score = sc
                best_sym = sym
                best_delta = dlt
                best_reason = rsn

        if best_sym and balance_ton > 5.0:
            spend_ton = round(balance_ton * TRADE_ALLOCATION_PCT, 4)
            if spend_ton <= 0:
                return
            entry_price = TOKENS[best_sym]["history_ton"][-1]
            entry_fee_ton = spend_ton * FEE_RATE
            effective_ton = spend_ton - entry_fee_ton
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

            msg = (
                f"📥 <b>گزارش ورود واقعی معامله #{trade_counter}</b> [<code>{RUN_ID}</code>]\n"
                f"🪙 توکن: <b>{best_sym}</b> (TON → {best_sym})\n"
                f"💵 سرمایه اختصاص‌یافته: <b>{spend_ton:.4f} TON</b>\n"
                f"🎯 قیمت ورود واقعی: <code>{entry_price:.8f} TON</code>\n"
                f"📈 تغییر اخیر: <code>{best_delta:+.2f}%</code> | امتیاز: <b>{best_score}</b>\n"
                f"🧠 دلایل: <i>{best_reason}</i>\n"
                f"🟢 حد سود هدف: <code>{tp_price:.8f} TON</code> ({TAKE_PROFIT_PCT:+.1f}%)\n"
                f"🔴 حد ضرر اولیه: <code>{sl_price:.8f} TON</code> ({STOP_LOSS_PCT:+.1f}%)\n"
                f"💼 موجودی نقد باقی‌مانده: <b>{balance_ton:.4f} TON</b>\n"
                f"🕒 زمان: <code>{now_str}</code>"
            )
            send_telegram_message(msg)


def get_total_equity_ton():
    equity = balance_ton
    if open_position is not None:
        sym = open_position["symbol"]
        hist = TOKENS[sym]["history_ton"]
        if hist:
            curr_val = open_position["token_qty"] * hist[-1] * (1.0 - FEE_RATE)
            equity += curr_val
        else:
            equity += open_position["spent_ton"]
    return equity


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_ton"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_ton"] > 0)
        total_closed = len(closed_trades)
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        total_equity = get_total_equity_ton()
        total_return_pct = ((total_equity - INITIAL_TON_BALANCE) / INITIAL_TON_BALANCE) * 100.0

        price_str = f"${ton_price_usd:.4f}" if ton_price_usd is not None else "در انتظار داده واقعی"
        mode_str = "🟢 روشن (در حال ترید)" if trading_enabled else "🔴 متوقف (قطع‌شده)"
        cd_str = f"{cooldown_remaining} چرخه" if cooldown_remaining > 0 else "خیر"
        upd_str = last_ton_fetch_time or "نامشخص"

        return (
            f"📊 <b>وضعیت ربات تریدر TON (داده واقعی)</b> [<code>{RUN_ID}</code>]\n"
            f"⚙️ وضعیت ترید: <b>{mode_str}</b>\n"
            f"💵 قیمت واقعی TON: <b>{price_str}</b> (۲۴h: <code>{ton_change_24h:+.2f}%</code>)\n"
            f"🕒 آخرین بروزرسانی قیمت: <code>{upd_str}</code>\n"
            f"💼 موجودی نقد: <b>{balance_ton:.4f} TON</b>\n"
            f"💎 ارزش کل دارایی (Equity): <b>{total_equity:.4f} TON</b> (<code>{total_return_pct:+.2f}%</code>)\n"
            f"📈 سود/ضرر خالص معاملات بسته: <code>{total_pnl:+.4f} TON</code>\n"
            f"🏆 نرخ برد: <b>{wins}/{total_closed}</b> (<code>{win_rate:.1f}%</code>)\n"
            f"⏳ دوره استراحت پس از ضرر: <b>{cd_str}</b>\n"
            f"🔓 پوزیشن باز: <b>{'بله (' + open_position['symbol'] + ')' if open_position else 'خیر'}</b>"
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
            f"📈 <b>آمار دقیق عملکرد معاملاتی</b> [<code>{RUN_ID}</code>]\n"
            f"• سرمایه اولیه: <b>{INITIAL_TON_BALANCE:.2f} TON</b>\n"
            f"• کل معاملات بسته‌شده: <b>{total_closed}</b>\n"
            f"• تعداد معاملات سودده: <b>{len(wins)}</b> | زیان‌ده: <b>{len(losses)}</b>\n"
            f"• نرخ برد (Win Rate): <b>{win_rate:.1f}%</b>\n"
            f"• سود/ضرر خالص کل: <code>{net_pnl:+.4f} TON</code>\n"
            f"• میانگین سود/ضرر هر معامله: <code>{avg_pnl:+.4f} TON</code>\n"
            f"• میانگین معامله سودده: <code>{avg_win:+.4f} TON</code>\n"
            f"• میانگین معامله زیان‌ده: <code>{avg_loss:+.4f} TON</code>"
        )


def build_positions_text():
    with state_lock:
        if not open_position:
            return f"🔒 در حال حاضر پوزیشن بازی وجود ندارد. [<code>{RUN_ID}</code>]"
        sym = open_position["symbol"]
        hist = TOKENS[sym]["history_ton"]
        curr_p = hist[-1] if hist else open_position["entry_price_ton"]
        entry_p = open_position["entry_price_ton"]
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
        peak_pct = open_position.get("peak_pnl_pct", pnl_pct)
        return (
            f"🔓 <b>جزئیات پوزیشن باز #{open_position['id']}</b> [<code>{RUN_ID}</code>]\n"
            f"• توکن: <b>{sym}</b>\n"
            f"• سرمایه درگیر: <b>{open_position['spent_ton']:.4f} TON</b>\n"
            f"• قیمت ورود: <code>{entry_p:.8f} TON</code>\n"
            f"• قیمت فعلی واقعی: <code>{curr_p:.8f} TON</code>\n"
            f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code> (اوج: <code>{peak_pct:+.2f}%</code>)"
        )


def build_history_text():
    with state_lock:
        if not closed_trades:
            return f"📭 هنوز هیچ معامله بسته‌شده‌ای ثبت نشده است. [<code>{RUN_ID}</code>]"
        recent = closed_trades[-6:]
        lines = [f"📜 <b>آخرین {len(recent)} معامله بسته‌شده</b> [<code>{RUN_ID}</code>]"]
        for t in reversed(recent):
            lines.append(
                f"• #{t['id']} <b>{t['symbol']}</b> | ورود: <code>{t['entry_price']:.8f}</code> → "
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


def handle_command(cmd: str, chat_id: str):
    global trading_enabled

    if cmd in ("/start", "/help"):
        send_telegram_message(
            f"🤖 <b>ربات تریدر TON (داده واقعی و آمار دقیق)</b> [<code>{RUN_ID}</code>]\n"
            "دستورات اصلی کنترل ترید:\n"
            "• /trade_on (یا /run) — شروع و فعال‌سازی ترید\n"
            "• /trade_off (یا /stop) — قطع فوری و توقف ترید\n\n"
            "دستورات گزارش و آمار:\n"
            "• /status — وضعیت کلی، موجودی و ارزش کل دارایی\n"
            "• /stats — آمار دقیق سود/ضرر، نرخ برد و میانگین‌ها\n"
            "• /positions — وضعیت پوزیشن باز\n"
            "• /history — آخرین معاملات بسته\n"
            "• /reset — ریست شبیه‌سازی به 1000 TON",
            chat_id=chat_id,
        )
    elif cmd in ("/trade_on", "/run"):
        with state_lock:
            trading_enabled = True
        send_telegram_message(
            f"🟢 <b>عملیات ترید شروع شد!</b> [<code>{RUN_ID}</code>]\n"
            "ربات فقط بر اساس قیمت‌های واقعی بازار تحلیل و معامله می‌کند.",
            chat_id=chat_id,
        )
    elif cmd in ("/trade_off", "/stop"):
        with state_lock:
            trading_enabled = False
        send_telegram_message(
            f"🛑 <b>عملیات ترید متوقف شد!</b> [<code>{RUN_ID}</code>]\n"
            "تمام عملیات ترید قطع شد تا دستور /trade_on ارسال شود.",
            chat_id=chat_id,
        )
    elif cmd == "/status":
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
            f"🔄 آمار و موجودی ریست شد. موجودی اولیه: <b>{INITIAL_TON_BALANCE:.2f} TON</b> [<code>{RUN_ID}</code>]",
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
                    cmd = text.split()[0].split("@")[0].lower()
                    handle_command(cmd, chat_id)
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
    send_telegram_message(
        f"🚀 <b>ربات تریدر TON (حالت داده واقعی) آماده شد!</b> [<code>{RUN_ID}</code>]\n"
        f"سرمایه تست: <b>{INITIAL_TON_BALANCE:.2f} TON</b> | بدون قیمت ساختگی یا نویز مصنوعی\n"
        f"• شروع ترید: <code>/trade_on</code>\n"
        f"• قطع فوری ترید: <code>/trade_off</code>\n"
        f"• آمار دقیق: <code>/stats</code>"
    )

    while True:
        ensure_polling_alive()
        try:
            process_trading_step()
        except Exception as e:
            print("Trading step error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    ensure_polling_alive()
    trading_loop()
