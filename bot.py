import os
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

try:
    from tabdeal.spot import Spot
except Exception:
    Spot = None

DEFAULT_TOKEN = "9080285155:AAHuZFiALLtk9Sstk1jTFFZN0PwPLWFhgWM"
DEFAULT_CHAT_ID = "1951133060"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID).strip()
PORT = int(os.environ.get("PORT", "10000"))

TABDEAL_API_KEY = os.environ.get("TABDEAL_API_KEY", "").strip()
TABDEAL_API_SECRET = os.environ.get("TABDEAL_API_SECRET", "").strip()
TABDEAL_PROXY_URL = os.environ.get("TABDEAL_PROXY_URL", "").strip()

SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "12"))
FAST_TF_SECONDS = int(os.environ.get("FAST_TF_SECONDS", "30"))
TREND_TF_SECONDS = int(os.environ.get("TREND_TF_SECONDS", "180"))

# کارمزد هر سمت (0.2% => رفت‌وبرگشت 0.4%)
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))
ROUND_TRIP_FEE_PCT = FEE_RATE * 200.0

# سود کم و منطقی: تارگت ناخالص 0.85% => سود خالص پس از 0.40% کارمزد حدود +0.45%
MIN_GROSS_TARGET_PCT = ROUND_TRIP_FEE_PCT + 0.45
GROSS_TARGET_PCT = max(float(os.environ.get("GROSS_TARGET_PCT", "0.85")), MIN_GROSS_TARGET_PCT)
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-0.55"))

MIN_SIGNAL_SCORE = int(os.environ.get("MIN_SIGNAL_SCORE", "1"))
PER_SYMBOL_COOLDOWN_SEC = int(os.environ.get("PER_SYMBOL_COOLDOWN_SEC", "90"))

RUN_ID = str(uuid.uuid4())[:8]

SEED_IRT_SYMBOLS = [
    "ETH_IRT", "DOGE_IRT", "DOT_IRT", "PUMP_IRT", "ONDO_IRT", "BTTC_IRT", "DOGS_IRT", "SUI_IRT",
    "FLOKI_IRT", "MOG_IRT", "TURBO_IRT", "STRK_IRT", "HBAR_IRT", "BOME_IRT", "AXS_IRT", "SEI_IRT",
    "ICP_IRT", "GIGA_IRT", "CGPT_IRT", "FET_IRT", "MEME_IRT", "ARKM_IRT", "AXL_IRT", "BONK_IRT",
    "CHZ_IRT", "FLUX_IRT", "COW_IRT", "DASH_IRT", "LINK_IRT", "NOT_IRT", "JASMY_IRT", "WIF_IRT",
    "FLR_IRT", "XCN_IRT", "BABYDOGE_IRT", "DUSK_IRT", "GALA_IRT", "HYPE_IRT", "ADA_IRT", "HAEDAL_IRT",
    "XRP_IRT", "CAT_IRT", "XMR_IRT", "ACH_IRT", "BTC_IRT", "SHIB_IRT", "SOL_IRT", "AVAX_IRT",
    "TON_IRT",
]

API_BASES = [
    "https://api1.tabdeal.org",
    "https://api.tabdeal.org",
]

http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 TabdealSignalBot/3.0",
    "Accept": "application/json",
})
if TABDEAL_PROXY_URL:
    http_session.proxies.update({
        "http": TABDEAL_PROXY_URL,
        "https": TABDEAL_PROXY_URL,
    })

spot_client = (
    Spot(TABDEAL_API_KEY, TABDEAL_API_SECRET)
    if (Spot is not None and TABDEAL_API_KEY and TABDEAL_API_SECRET)
    else None
)

active_symbols = list(SEED_IRT_SYMBOLS)
market_state = {}
last_sent_ts = {}
last_update_id = 0
signal_active = True
last_fetch_info = "در حال اتصال به تبدیل..."
last_fetch_ok = False
polling_thread = None
state_lock = threading.Lock()


def ensure_symbol_state(sym: str):
    if sym not in market_state:
        market_state[sym] = {
            "price": None,
            "price_raw": None,
            "fast_candles": [],
            "fast_cur": None,
            "trend_candles": [],
            "trend_cur": None,
        }


for _s in SEED_IRT_SYMBOLS:
    ensure_symbol_state(_s)


def format_price_irt(value: float, raw_str: str = None) -> str:
    if raw_str is not None and str(raw_str).strip() != "":
        s = str(raw_str).strip().replace(",", "")
        try:
            float(s)
            if "." in s:
                ip, dp = s.split(".", 1)
                dp = dp.rstrip("0")
                ip_fmt = f"{int(float(ip)):,}" if ip not in ("", "-") else ip
                return f"{ip_fmt}.{dp} تومان" if dp else f"{ip_fmt} تومان"
            return f"{int(float(s)):,} تومان"
        except Exception:
            pass

    if value is None:
        return "نامشخص"
    val = float(value)
    if val >= 100 or val.is_integer():
        return f"{round(val):,} تومان"
    s = f"{val:.8f}".rstrip("0").rstrip(".")
    if "." in s:
        ip, dp = s.split(".", 1)
        return f"{int(ip):,}.{dp} تومان"
    return f"{int(s):,} تومان"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"Tabdeal Signal Bot ({RUN_ID}) active".encode("utf-8"))

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
            timeout=10,
        )
        return res.status_code == 200
    except Exception as e:
        print("Telegram send error:", e)
        return False


def _normalize_irt_symbol(raw_sym: str):
    if not raw_sym:
        return None
    s = str(raw_sym).strip().upper().replace("-", "_").replace("/", "_")
    if s.endswith("_IRT") and len(s) > 4:
        return s
    if s.endswith("IRT") and len(s) > 3 and "_" not in s:
        return f"{s[:-3]}_IRT"
    return None


def _extract_price(obj: dict):
    if not isinstance(obj, dict):
        return None, None
    for k in ("lastPrice", "last_price", "price", "close", "mid", "ask", "bid", "markPrice"):
        val = obj.get(k)
        if val is not None and str(val).strip() != "":
            raw_s = str(val).strip()
            try:
                fval = float(raw_s.replace(",", ""))
                if fval > 0:
                    return fval, raw_s
            except Exception:
                continue
    return None, None


def _extract_rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "result", "tickers", "symbols", "markets", "items"):
            val = payload.get(k)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                rows = []
                for sym_k, sym_v in val.items():
                    if isinstance(sym_v, dict):
                        row = dict(sym_v)
                        row.setdefault("symbol", sym_k)
                        rows.append(row)
                    else:
                        rows.append({"symbol": sym_k, "price": sym_v})
                return rows
    return []


def fetch_all_irt_prices_from_tabdeal():
    """دریافت قیمت لحظه‌ای دقیق از تبدیل از طریق SDK یا مسیرهای REST مستند"""
    global last_fetch_info, last_fetch_ok, active_symbols

    prices = {}
    discovered = set(SEED_IRT_SYMBOLS)
    diag_errors = []

    if spot_client is not None:
        for method_name in ("exchange_info", "get_exchange_info", "ticker_24hr", "get_ticker"):
            if hasattr(spot_client, method_name):
                try:
                    res = getattr(spot_client, method_name)()
                    rows = _extract_rows(res)
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        raw_sym = row.get("symbol") or row.get("pair") or row.get("market")
                        norm_sym = _normalize_irt_symbol(raw_sym)
                        if norm_sym:
                            discovered.add(norm_sym)
                            p, raw_s = _extract_price(row)
                            if p is not None and norm_sym not in prices:
                                prices[norm_sym] = (p, raw_s)
                    if prices:
                        break
                except Exception as e:
                    diag_errors.append(f"SDK.{method_name}: {type(e).__name__}")

    if not prices:
        for base in API_BASES:
            for ep in ("/r/api/v1/exchangeInfo", "/api/v1/exchangeInfo", "/r/api/v1/ticker/24hr"):
                url = f"{base}{ep}"
                try:
                    resp = http_session.get(url, timeout=8)
                    if resp.status_code != 200:
                        diag_errors.append(f"{ep}->{resp.status_code}")
                        continue
                    rows = _extract_rows(resp.json())
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        raw_sym = row.get("symbol") or row.get("pair") or row.get("market") or row.get("name")
                        norm_sym = _normalize_irt_symbol(raw_sym)
                        if norm_sym:
                            discovered.add(norm_sym)
                            p, raw_s = _extract_price(row)
                            if p is not None and norm_sym not in prices:
                                prices[norm_sym] = (p, raw_s)
                    if prices:
                        break
                except Exception as e:
                    diag_errors.append(f"{base}:{type(e).__name__}")
            if prices:
                break

    if not prices:
        with state_lock:
            syms_to_check = list(active_symbols)
        for base in API_BASES:
            for ep in ("/r/api/v1/depth", "/r/api/v1/trades"):
                url = f"{base}{ep}"
                try:
                    probe = http_session.get(url, params={"symbol": "BTC_IRT", "limit": 5}, timeout=6)
                    if probe.status_code != 200:
                        continue
                    for sym in syms_to_check:
                        try:
                            r = http_session.get(url, params={"symbol": sym, "limit": 5}, timeout=5)
                            if r.status_code != 200:
                                continue
                            payload = r.json()
                            if ep.endswith("/depth") and isinstance(payload, dict):
                                bids = payload.get("bids") or []
                                asks = payload.get("asks") or []
                                if bids and asks:
                                    b_p = float(str(bids[0][0]).replace(",", ""))
                                    a_p = float(str(asks[0][0]).replace(",", ""))
                                    if b_p > 0 and a_p > 0:
                                        mid_p = (b_p + a_p) / 2.0
                                        prices[sym] = (mid_p, str(mid_p))
                            elif ep.endswith("/trades"):
                                t_list = _extract_rows(payload)
                                if t_list and isinstance(t_list[0], dict):
                                    p, raw_s = _extract_price(t_list[0])
                                    if p is not None:
                                        prices[sym] = (p, raw_s)
                        except Exception:
                            continue
                    if prices:
                        break
                except Exception:
                    continue
            if prices:
                break

    if prices:
        with state_lock:
            active_symbols = sorted(discovered)
            for s in active_symbols:
                ensure_symbol_state(s)
        last_fetch_ok = True
        last_fetch_info = f"متصل به تبدیل ({len(prices)} بازار IRT)"
        return prices

    last_fetch_ok = False
    reason = " | ".join(diag_errors[:3]) if diag_errors else "عدم پاسخ API تبدیل (احتمال محدودیت IP سرور روی Render)"
    last_fetch_info = reason
    return {}


def _update_candle(candles, cur, bucket_ts, price, max_keep=120):
    if cur is None:
        return {"bucket_ts": bucket_ts, "open": price, "high": price, "low": price, "close": price}
    if cur["bucket_ts"] == bucket_ts:
        cur["high"] = max(cur["high"], price)
        cur["low"] = min(cur["low"], price)
        cur["close"] = price
        return cur
    candles.append(dict(cur))
    if len(candles) > max_keep:
        candles.pop(0)
    return {"bucket_ts": bucket_ts, "open": price, "high": price, "low": price, "close": price}


def update_market_candles():
    prices = fetch_all_irt_prices_from_tabdeal()
    if not prices:
        return False, 0

    now_ts = int(time.time())
    fast_ts = (now_ts // FAST_TF_SECONDS) * FAST_TF_SECONDS
    trend_ts = (now_ts // TREND_TF_SECONDS) * TREND_TF_SECONDS

    with state_lock:
        for sym, (price, raw_s) in prices.items():
            ensure_symbol_state(sym)
            st = market_state[sym]
            st["price"] = price
            st["price_raw"] = raw_s
            st["fast_cur"] = _update_candle(st["fast_candles"], st["fast_cur"], fast_ts, price)
            st["trend_cur"] = _update_candle(st["trend_candles"], st["trend_cur"], trend_ts, price)

    return True, len(prices)


def calc_ema(values, period):
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def evaluate_symbol(sym: str):
    with state_lock:
        st = market_state.get(sym)
        if not st or st["price"] is None or st["price"] <= 0:
            return None
        price = st["price"]
        price_raw = st["price_raw"]
        f_candles = list(st["fast_candles"])
        f_cur = dict(st["fast_cur"]) if st["fast_cur"] else None
        t_candles = list(st["trend_candles"])

    series = list(f_candles)
    if f_cur:
        series.append(f_cur)
    closes = [c["close"] for c in series if c and c.get("close")]

    score = 0
    reasons = []

    if len(closes) >= 2 and closes[-2] > 0:
        step_pct = ((closes[-1] - closes[-2]) / closes[-2]) * 100.0
        if step_pct > 0.03:
            score += 2
            reasons.append(f"رشد لحظه‌ای {step_pct:+.2f}%")
        elif step_pct < -0.35:
            score -= 2
            reasons.append(f"افت لحظه‌ای {step_pct:+.2f}%")

    if len(closes) >= 3:
        ema_fast = calc_ema(closes, 2)
        ema_slow = calc_ema(closes, 5)
        if price >= ema_fast >= ema_slow:
            score += 2
            reasons.append("هم‌راستایی EMA کوتاه")

    if f_cur and f_cur.get("open", 0) > 0:
        body_pct = ((f_cur["close"] - f_cur["open"]) / f_cur["open"]) * 100.0
        if body_pct > 0.03:
            score += 1
            reasons.append(f"کندل سبز ({body_pct:+.2f}%)")

    if t_candles and t_candles[-1].get("open", 0) > 0:
        t_pct = ((t_candles[-1]["close"] - t_candles[-1]["open"]) / t_candles[-1]["open"]) * 100.0
        if t_pct >= 0:
            score += 1
            reasons.append("روند کوتاه مثبت")

    entry_price = price
    target_price = entry_price * (1.0 + GROSS_TARGET_PCT / 100.0)
    stop_price = entry_price * (1.0 + STOP_LOSS_PCT / 100.0)
    net_profit_pct = GROSS_TARGET_PCT - ROUND_TRIP_FEE_PCT
    net_profit_per_1m = 1_000_000.0 * (net_profit_pct / 100.0)

    return {
        "symbol": sym,
        "score": score,
        "price": price,
        "price_raw": price_raw,
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "gross_target_pct": GROSS_TARGET_PCT,
        "net_profit_pct": net_profit_pct,
        "fee_round_pct": ROUND_TRIP_FEE_PCT,
        "stop_loss_pct": STOP_LOSS_PCT,
        "net_profit_per_1m": net_profit_per_1m,
        "reasons": "، ".join(reasons) if reasons else "قیمت زنده تبدیل",
    }


def format_signal_message(sig: dict) -> str:
    return (
        f"⚡ <b>سیگنال تبدیل | {sig['symbol']}</b> [<code>{RUN_ID}</code>]\n"
        f"• <b>ارز:</b> <code>{sig['symbol']}</code>\n"
        f"• <b>ورود:</b> <code>{format_price_irt(sig['entry_price'], sig['price_raw'])}</code>\n"
        f"• <b>تارگت خروج:</b> <code>{format_price_irt(sig['target_price'])}</code> (<code>+{sig['gross_target_pct']:.2f}%</code>)\n"
        f"• <b>حد ضرر:</b> <code>{format_price_irt(sig['stop_price'])}</code> (<code>{sig['stop_loss_pct']:+.2f}%</code>)\n"
        f"• <b>سود خالص پس از کارمزد ({sig['fee_round_pct']:.2f}%):</b> "
        f"<b>+{sig['net_profit_pct']:.2f}%</b> (~<code>+{round(sig['net_profit_per_1m']):,} تومان</code> به ازای ۱ میلیون)\n"
        f"• <i>{sig['reasons']}</i>"
    )


def scan_and_dispatch_signals(manual: bool = False):
    ok, priced_count = update_market_candles()
    if not ok or priced_count == 0:
        if manual:
            send_telegram_message(
                f"⚠️ <b>دریافت قیمت زنده از تبدیل ناموفق بود</b> [<code>{RUN_ID}</code>]\n"
                f"• علت: <code>{last_fetch_info}</code>"
            )
        return []

    with state_lock:
        syms = list(active_symbols)

    candidates = []
    for sym in syms:
        ev = evaluate_symbol(sym)
        if ev and ev["score"] >= MIN_SIGNAL_SCORE:
            candidates.append(ev)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    now_ts = time.time()
    to_send = []
    with state_lock:
        for ev in candidates:
            sym = ev["symbol"]
            if manual or (now_ts - last_sent_ts.get(sym, 0.0)) >= PER_SYMBOL_COOLDOWN_SEC:
                last_sent_ts[sym] = now_ts
                to_send.append(ev)

    if manual and not to_send:
        fallback = [ev for sym in syms if (ev := evaluate_symbol(sym))]
        fallback.sort(key=lambda x: x["score"], reverse=True)
        to_send = fallback[:5]

    for ev in to_send[:10]:
        send_telegram_message(format_signal_message(ev))

    return to_send


def build_status_details() -> str:
    with state_lock:
        enabled = signal_active
        priced = sum(1 for s in active_symbols if market_state.get(s, {}).get("price") is not None)
        total_syms = len(active_symbols)
        info = last_fetch_info
        ok = last_fetch_ok

    state_label = "🟢 روشن" if enabled else "🔴 خاموش"
    conn_label = "🟢 متصل" if ok else "🟡 ناموفق"
    net_pct = GROSS_TARGET_PCT - ROUND_TRIP_FEE_PCT

    return (
        f"📊 <b>جزئیات ربات سیگنال تبدیل</b> [<code>{RUN_ID}</code>]\n"
        f"• وضعیت: <b>{state_label}</b> | اتصال قیمت: <b>{conn_label}</b> ({priced}/{total_syms} IRT)\n"
        f"• جزئیات اتصال: <code>{info}</code>\n"
        f"• کارمزد رفت‌وبرگشت: <code>{ROUND_TRIP_FEE_PCT:.2f}%</code>\n"
        f"• تارگت: <code>+{GROSS_TARGET_PCT:.2f}%</code> | <b>سود خالص پس از کارمزد: +{net_pct:.2f}%</b>\n"
        f"• حد ضرر: <code>{STOP_LOSS_PCT:+.2f}%</code>\n"
        f"• دستورها: <code>/start</code> | <code>/stop</code> | <code>/signal</code>"
    )


def handle_command(raw_text: str, chat_id: str):
    global signal_active
    cmd = raw_text.strip().split()[0].split("@")[0].lower()

    if cmd == "/start":
        with state_lock:
            signal_active = True
        send_telegram_message(
            f"🚀 <b>ربات سیگنال تبدیل روشن شد و سیگنال‌گیری آغاز گردید!</b> [<code>{RUN_ID}</code>]\n\n"
            + build_status_details(),
            chat_id,
        )
        scan_and_dispatch_signals(manual=True)

    elif cmd == "/stop":
        with state_lock:
            signal_active = False
        send_telegram_message(
            f"🛑 <b>ربات سیگنال تبدیل خاموش شد.</b> [<code>{RUN_ID}</code>]\n"
            "برای شروع دوباره دستور <code>/start</code> را بفرستید.",
            chat_id,
        )

    elif cmd == "/signal":
        with state_lock:
            is_on = signal_active
        if not is_on:
            send_telegram_message(
                f"🔴 ربات خاموش است. ابتدا <code>/start</code> را بفرستید. [<code>{RUN_ID}</code>]",
                chat_id,
            )
            return
        sent = scan_and_dispatch_signals(manual=True)
        if not sent:
            send_telegram_message(
                f"⚠️ قیمت زنده‌ای از تبدیل دریافت نشد؛ علت: <code>{last_fetch_info}</code> [<code>{RUN_ID}</code>]",
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
        f"✅ <b>ربات سیگنال تبدیل آماده است.</b> [<code>{RUN_ID}</code>]\n"
        "• <code>/start</code> — شروع فوری سیگنال‌گیری و جزئیات کامل\n"
        "• <code>/stop</code> — خاموش کردن ربات\n"
        "• <code>/signal</code> — بررسی بازارهای تومانی و اعلام سود خالص تا تارگت"
    )
    while True:
        ensure_polling_alive()
        try:
            with state_lock:
                active = signal_active
            if active:
                scan_and_dispatch_signals(manual=False)
        except Exception as e:
            print("Main loop error:", e)
        time.sleep(SAMPLE_SECONDS)


if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    ensure_polling_alive()
    main_loop()
