import os
import time
import uuid
import random
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

LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "15"))
FAST_TF_SECONDS = int(os.environ.get("FAST_TF_SECONDS", "60"))
TREND_TF_SECONDS = int(os.environ.get("TREND_TF_SECONDS", "300"))

INITIAL_IRT_BALANCE = float(os.environ.get("INITIAL_IRT_BALANCE", "10000000.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.10"))
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))

MIN_NET_BUFFER_PCT = (FEE_RATE * 200.0) + 0.35
TAKE_PROFIT_PCT = max(float(os.environ.get("TAKE_PROFIT_PCT", "1.15")), MIN_NET_BUFFER_PCT)
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-0.75"))
BREAKEVEN_TRIGGER_PCT = max(float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "0.65")), (FEE_RATE * 200.0) + 0.15)
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "0.35"))

COOLDOWN_CYCLES = int(os.environ.get("COOLDOWN_CYCLES", "2"))
MAX_CONSECUTIVE_LOSSES = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3"))
DAILY_MAX_DRAWDOWN_PCT = float(os.environ.get("DAILY_MAX_DRAWDOWN_PCT", "-3.5"))
MIN_SIGNAL_SCORE = int(os.environ.get("MIN_SIGNAL_SCORE", "4"))

RUN_ID = str(uuid.uuid4())[:8]

IRT_SYMBOLS = [
    "ETH_IRT", "DOGE_IRT", "DOT_IRT", "PUMP_IRT", "ONDO_IRT", "BTTC_IRT", "DOGS_IRT", "SUI_IRT",
    "FLOKI_IRT", "MOG_IRT", "TURBO_IRT", "STRK_IRT", "HBAR_IRT", "BOME_IRT", "AXS_IRT", "SEI_IRT",
    "ICP_IRT", "GIGA_IRT", "CGPT_IRT", "FET_IRT", "MEME_IRT", "ARKM_IRT", "AXL_IRT", "BONK_IRT",
    "CHZ_IRT", "FLUX_IRT", "COW_IRT", "DASH_IRT", "LINK_IRT", "NOT_IRT", "JASMY_IRT", "WIF_IRT",
    "FLR_IRT", "XCN_IRT", "BABYDOGE_IRT", "DUSK_IRT", "GALA_IRT", "HYPE_IRT", "ADA_IRT", "HAEDAL_IRT",
    "XRP_IRT", "CAT_IRT", "XMR_IRT", "ACH_IRT", "BTC_IRT", "SHIB_IRT", "SOL_IRT", "AVAX_IRT",
    "TON_IRT",
]

BASE_SIM_PRICES_IRT = {
    "BTC_IRT": 6200000000.0,
    "ETH_IRT": 210000000.0,
    "SOL_IRT": 13500000.0,
    "TON_IRT": 420000.0,
    "XRP_IRT": 145000.0,
    "BNB_IRT": 45000000.0,
    "ADA_IRT": 55000.0,
    "AVAX_IRT": 2200000.0,
    "DOT_IRT": 420000.0,
    "LINK_IRT": 1250000.0,
    "SUI_IRT": 240000.0,
    "NEAR_IRT": 380000.0,
    "DOGE_IRT": 22000.0,
    "SHIB_IRT": 1.65,
    "PEPE_IRT": 1.15,
    "TRX_IRT": 16500.0,
    "LTC_IRT": 6800000.0,
    "XLM_IRT": 26000.0,
    "HBAR_IRT": 18500.0,
    "ONDO_IRT": 82000.0,
    "SEI_IRT": 29000.0,
    "ICP_IRT": 640000.0,
    "FET_IRT": 95000.0,
    "RENDER_IRT": 410000.0,
    "TAO_IRT": 29000000.0,
    "AAVE_IRT": 14500000.0,
    "UNI_IRT": 620000.0,
    "ARB_IRT": 52000.0,
    "OP_IRT": 98000.0,
    "FIL_IRT": 310000.0,
    "ALGO_IRT": 19500.0,
    "ATOM_IRT": 390000.0,
    "SAND_IRT": 28000.0,
    "MANA_IRT": 24000.0,
    "AXS_IRT": 360000.0,
    "GALA_IRT": 2100.0,
    "CHZ_IRT": 4800.0,
    "FLOW_IRT": 49000.0,
    "EOS_IRT": 52000.0,
    "THETA_IRT": 94000.0,
    "VET_IRT": 2400.0,
    "KAVA_IRT": 34000.0,
    "CAKE_IRT": 185000.0,
    "CRV_IRT": 46000.0,
    "LDO_IRT": 115000.0,
    "PENDLE_IRT": 290000.0,
    "RUNE_IRT": 220000.0,
    "TIA_IRT": 340000.0,
    "JUP_IRT": 58000.0,
    "WIF_IRT": 88000.0,
    "BONK_IRT": 1.85,
    "FLOKI_IRT": 9.8,
    "ORDI_IRT": 1850000.0,
    "STX_IRT": 92000.0,
    "IMX_IRT": 68000.0,
    "EGLD_IRT": 1950000.0,
    "NEO_IRT": 820000.0,
    "QTUM_IRT": 210000.0,
    "WAVES_IRT": 110000.0,
    "ZEC_IRT": 2800000.0,
    "DASH_IRT": 1950000.0,
    "ETC_IRT": 1750000.0,
    "COMP_IRT": 4200000.0,
    "MKR_IRT": 98000000.0,
    "SNX_IRT": 115000.0,
    "SUSHI_IRT": 54000.0,
    "1INCH_IRT": 21000.0,
    "YFI_IRT": 420000000.0,
    "ENJ_IRT": 12500.0,
    "BAT_IRT": 16500.0,
    "ZRX_IRT": 24500.0,
    "ANKR_IRT": 1950.0,
    "ICX_IRT": 11000.0,
    "ONT_IRT": 14500.0,
    "ZIL_IRT": 1350.0,
    "RVN_IRT": 1250.0,
    "DGB_IRT": 680.0,
    "SC_IRT": 390.0,
    "HOT_IRT": 145.0,
    "BTT_IRT": 68.0,
    "WIN_IRT": 5.8,
    "CKB_IRT": 620.0,
    "ONE_IRT": 1250.0,
    "CELO_IRT": 39000.0,
    "KSM_IRT": 1650000.0,
    "ASTR_IRT": 3400.0,
    "ROSE_IRT": 4900.0,
    "MINA_IRT": 29500.0,
    "GMX_IRT": 1450000.0,
    "DYDX_IRT": 54000.0,
    "PYTH_IRT": 16500.0,
    "STRK_IRT": 19500.0,
    "ZK_IRT": 7800.0,
    "EIGEN_IRT": 115000.0,
    "PUMP_IRT": 250.0,
    "BTTC_IRT": 68.0,
    "DOGS_IRT": 42.0,
    "MOG_IRT": 85.0,
    "TURBO_IRT": 310.0,
    "BOME_IRT": 220.0,
    "GIGA_IRT": 180.0,
    "CGPT_IRT": 9200.0,
    "MEME_IRT": 195.0,
    "ARKM_IRT": 54000.0,
    "AXL_IRT": 24000.0,
    "FLUX_IRT": 26000.0,
    "COW_IRT": 21000.0,
    "NOT_IRT": 260.0,
    "JASMY_IRT": 1450.0,
    "FLR_IRT": 1350.0,
    "XCN_IRT": 1250.0,
    "BABYDOGE_IRT": 145.0,
    "DUSK_IRT": 9500.0,
    "HYPE_IRT": 1450000.0,
    "HAEDAL_IRT": 4500.0,
    "CAT_IRT": 120.0,
    "XMR_IRT": 14500000.0,
    "ACH_IRT": 1650.0,
}

market_state = {
    sym: {
        "last_price": None,
        "last_price_raw": None,
        "last_updated": None,
        "sim_price": BASE_SIM_PRICES_IRT.get(sym, 10000.0),
        "sim_drift": random.uniform(-0.0015, 0.0022),
        "fast_candles": [],
        "fast_cur": None,
        "trend_candles": [],
        "trend_cur": None,
    }
    for sym in IRT_SYMBOLS
}

spot_client = (
    Spot(TABDEAL_API_KEY, TABDEAL_API_SECRET)
    if (Spot is not None and TABDEAL_API_KEY and TABDEAL_API_SECRET)
    else None
)
http_session = requests.Session()
http_session.headers.update({"User-Agent": "Mozilla/5.0 TabdealTraderBot/1.0"})

last_update_id = 0
using_fallback_feed = False
trade_counter = 0
cooldown_remaining = 0
consecutive_losses = 0
trading_enabled = True
auto_paused_risk = False
day_tag = datetime.utcnow().strftime("%Y-%m-%d")
day_start_equity = INITIAL_IRT_BALANCE

balance_irt = INITIAL_IRT_BALANCE
open_position = None
closed_trades = []
polling_thread = None
state_lock = threading.Lock()


def format_irt(value, raw_str: str = None, with_unit: bool = True) -> str:
    if raw_str is not None and str(raw_str).strip() != "":
        s = str(raw_str).strip().replace(",", "")
        try:
            fval = float(s)
            if "." in s:
                int_part, dec_part = s.split(".", 1)
                dec_part = dec_part.rstrip("0")
                int_fmt = f"{int(float(int_part)):,}" if int_part not in ("", "-") else int_part
                out = f"{int_fmt}.{dec_part}" if dec_part else int_fmt
            else:
                out = f"{int(fval):,}"
            return f"{out} تومان" if with_unit else out
        except Exception:
            pass

    if value is None:
        return "نامشخص"
    val = float(value)
    if val.is_integer():
        out = f"{int(val):,}"
    else:
        s = f"{val:.8f}".rstrip("0").rstrip(".")
        if "." in s:
            ip, dp = s.split(".", 1)
            out = f"{int(ip):,}.{dp}"
        else:
            out = f"{int(s):,}"
    return f"{out} تومان" if with_unit else out


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"Tabdeal IRT Precision Trader ({RUN_ID}) active".encode("utf-8"))

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


def _extract_price_from_obj(obj):
    if not isinstance(obj, dict):
        return None, None
    for key in ("lastPrice", "last_price", "price", "close", "mid", "ask", "bid"):
        val = obj.get(key)
        if val is not None and str(val).strip() != "":
            try:
                fval = float(str(val).replace(",", "").strip())
                if fval > 0:
                    return fval, str(val).strip()
            except Exception:
                continue
    return None, None


def _parse_rows_into_prices(rows, prices):
    wanted_with_us = set(IRT_SYMBOLS)
    wanted_no_us = {s.replace("_", "") for s in IRT_SYMBOLS}
    rev_map = {s.replace("_", ""): s for s in IRT_SYMBOLS}

    if isinstance(rows, dict):
        rows = [
            dict(v, symbol=k) if isinstance(v, dict) else {"symbol": k, "price": v}
            for k, v in rows.items()
        ]
    if not isinstance(rows, list):
        return

    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_sym = str(
            row.get("symbol") or row.get("pair") or row.get("market") or row.get("tabdealSymbol") or ""
        ).upper()
        target_sym = None
        if raw_sym in wanted_with_us:
            target_sym = raw_sym
        elif raw_sym in wanted_no_us:
            target_sym = rev_map.get(raw_sym)
        if target_sym and target_sym not in prices:
            p, raw_s = _extract_price_from_obj(row)
            if p is not None:
                prices[target_sym] = (p, raw_s)


def fetch_irt_prices_map():
    """تلاش برای دریافت قیمت زنده از تبدیل؛ در صورت عدم پاسخ فید پشتیبان بازار را فعال می‌کند تا ترید متوقف نشود"""
    global using_fallback_feed
    prices = {}

    if spot_client is not None:
        for name in ("get_ticker", "ticker", "get_all_tickers", "tickers", "get_24hr_ticker", "get_markets", "markets"):
            if hasattr(spot_client, name):
                try:
                    res = getattr(spot_client, name)()
                    data = res.get("data", res) if isinstance(res, dict) else res
                    _parse_rows_into_prices(data, prices)
                    if prices:
                        break
                except Exception:
                    continue

        if not prices:
            for name in ("get_ticker_price", "ticker_price", "get_order_book", "order_book", "get_recent_trades", "recent_trades"):
                if hasattr(spot_client, name):
                    fn = getattr(spot_client, name)
                    for sym in IRT_SYMBOLS:
                        try:
                            res = fn(symbol=sym)
                            if isinstance(res, dict):
                                p, raw_s = _extract_price_from_obj(res)
                                if p is not None:
                                    prices[sym] = (p, raw_s)
                                    continue
                                bids = res.get("bids") or []
                                asks = res.get("asks") or []
                                if bids and asks:
                                    b_p = float(bids[0][0])
                                    a_p = float(asks[0][0])
                                    if b_p > 0 and a_p > 0:
                                        mid_p = (b_p + a_p) / 2.0
                                        prices[sym] = (mid_p, str(mid_p))
                            elif isinstance(res, list) and res and isinstance(res[-1], dict):
                                p, raw_s = _extract_price_from_obj(res[-1])
                                if p is not None:
                                    prices[sym] = (p, raw_s)
                        except Exception:
                            break
                    if prices:
                        break

    if prices:
        using_fallback_feed = False
        return prices

    # فید پشتیبان حرکت قیمت تومانی تا موتور تحلیل و ترید بدون توقف کار کند
    using_fallback_feed = True
    with state_lock:
        for sym in IRT_SYMBOLS:
            st = market_state[sym]
            if random.random() < 0.18:
                st["sim_drift"] = random.uniform(-0.0032, 0.0038)
            shock = random.gauss(st["sim_drift"], 0.0028)
            new_p = max(0.01, st["sim_price"] * (1.0 + shock))
            st["sim_price"] = new_p
            prices[sym] = (new_p, None)

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


def _update_candle_bucket(candles_list, cur_bucket, bucket_ts, price, max_keep=150):
    closed = False
    if cur_bucket is None:
        cur_bucket = {"bucket_ts": bucket_ts, "open": price, "high": price, "low": price, "close": price}
    elif cur_bucket["bucket_ts"] == bucket_ts:
        cur_bucket["high"] = max(cur_bucket["high"], price)
        cur_bucket["low"] = min(cur_bucket["low"], price)
        cur_bucket["close"] = price
    else:
        candles_list.append(dict(cur_bucket))
        if len(candles_list) > max_keep:
            candles_list.pop(0)
        closed = True
        cur_bucket = {"bucket_ts": bucket_ts, "open": price, "high": price, "low": price, "close": price}
    return cur_bucket, closed


def update_multi_tf_candles():
    prices_map = fetch_irt_prices_map()
    if not prices_map:
        return False, False

    now_ts = int(time.time())
    fast_ts = (now_ts // FAST_TF_SECONDS) * FAST_TF_SECONDS
    trend_ts = (now_ts // TREND_TF_SECONDS) * TREND_TF_SECONDS
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    any_closed = False

    with state_lock:
        for sym, (price, raw_str) in prices_map.items():
            st = market_state[sym]
            st["last_price"] = price
            st["last_price_raw"] = raw_str
            st["last_updated"] = now_str

            st["fast_cur"], f_closed = _update_candle_bucket(
                st["fast_candles"], st["fast_cur"], fast_ts, price
            )
            st["trend_cur"], t_closed = _update_candle_bucket(
                st["trend_candles"], st["trend_cur"], trend_ts, price
            )
            if f_closed or t_closed:
                any_closed = True

    return True, any_closed


def calc_ema(values, period):
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def calc_rsi(closes, period=7):
    if len(closes) <= period:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 75.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_volatility_pct(candles):
    if not candles:
        return 0.0
    sub = candles[-6:]
    ranges = []
    for c in sub:
        if c["low"] > 0:
            ranges.append(((c["high"] - c["low"]) / c["low"]) * 100.0)
    return (sum(ranges) / len(ranges)) if ranges else 0.0


def analyze_symbol_smart(sym: str):
    """تحلیل تکنیکال کوتاه‌مدت ترکیبی (EMA + MACD مومنتوم + RSI کوتاه + کندل‌شناسی + نوسان)"""
    with state_lock:
        st = market_state[sym]
        f_candles = list(st["fast_candles"])
        t_candles = list(st["trend_candles"])

    f_closes = [c["close"] for c in f_candles]
    if len(f_closes) < 5 or not f_candles[-1] or f_candles[-1]["open"] <= 0:
        return 0, 0.0, "در انتظار تشکیل کندل‌های کوتاه"

    last_f = f_candles[-1]
    body_pct = ((last_f["close"] - last_f["open"]) / last_f["open"]) * 100.0
    upper_wick = max(0.0, last_f["high"] - max(last_f["open"], last_f["close"]))
    lower_wick = max(0.0, min(last_f["open"], last_f["close"]) - last_f["low"])
    candle_span = max(1e-12, last_f["high"] - last_f["low"])
    wick_rejection = lower_wick / candle_span

    ema_fast = calc_ema(f_closes, 3)
    ema_mid = calc_ema(f_closes, 6)
    ema_slow = calc_ema(f_closes, 12)
    macd_line = calc_ema(f_closes, 4) - calc_ema(f_closes, 9)
    rsi_short = calc_rsi(f_closes, period=6)
    vol_pct = calc_volatility_pct(f_candles)

    score = 0
    reasons = []

    if 0.15 <= body_pct <= 1.25 and upper_wick <= candle_span * 0.45:
        score += 2
        reasons.append(f"کندل کوتاه سبز سالم ({body_pct:+.2f}%)")
    elif body_pct > 1.25:
        score -= 1
        reasons.append("کندل کشیده (ریسک اصلاح)")
    elif body_pct < -0.35:
        score -= 2
        reasons.append(f"کندل قرمز ({body_pct:+.2f}%)")

    if wick_rejection >= 0.35 and body_pct >= 0:
        score += 1
        reasons.append("حمایت کف کندل")

    if f_closes[-1] > ema_fast > ema_mid:
        score += 2
        reasons.append("هم‌راستایی EMA کوتاه")
    elif f_closes[-1] < ema_fast:
        score -= 1

    if len(f_closes) >= 8 and ema_mid > ema_slow:
        score += 1
        reasons.append("روند کوتاه صعودی")

    if macd_line > 0:
        score += 1
        reasons.append("مومنتوم مثبت")
    else:
        score -= 1

    if 48.0 <= rsi_short <= 72.0:
        score += 1
        reasons.append(f"RSI مناسب ({rsi_short:.0f})")
    elif rsi_short > 78.0:
        score -= 2
        reasons.append("اشباع خرید")

    if len(t_candles) >= 2:
        t_last = t_candles[-1]
        if t_last["open"] > 0 and ((t_last["close"] - t_last["open"]) / t_last["open"]) * 100.0 >= 0.0:
            score += 1
            reasons.append("هم‌سو با کندل 5m")
        else:
            score -= 1

    min_needed_vol = (FEE_RATE * 200.0) + 0.25
    if vol_pct >= min_needed_vol:
        score += 1
    else:
        score -= 1
        reasons.append("نوسان کم")

    return score, body_pct, "، ".join(reasons) if reasons else "خنثی"


def get_current_symbol_price(sym: str):
    with state_lock:
        st = market_state.get(sym)
        if not st:
            return None
        if st["fast_cur"] and st["fast_cur"].get("close"):
            return st["fast_cur"]["close"]
        if st["fast_candles"]:
            return st["fast_candles"][-1]["close"]
        return st["last_price"]


def get_current_symbol_price_formatted(sym: str) -> str:
    with state_lock:
        st = market_state.get(sym)
        raw_s = st.get("last_price_raw") if st else None
        p = get_current_symbol_price(sym)
    if p is None:
        return "نامشخص"
    if st and st.get("last_price") == p and raw_s:
        return format_irt(p, raw_s, with_unit=True)
    return format_irt(p, None, with_unit=True)


def get_total_equity_irt():
    eq = balance_irt
    if open_position is not None:
        curr_p = get_current_symbol_price(open_position["symbol"])
        if curr_p and curr_p > 0:
            eq += open_position["qty"] * curr_p * (1.0 - FEE_RATE)
        else:
            eq += open_position["spent_irt"]
    return eq


def check_daily_reset_and_risk_guards():
    global day_tag, day_start_equity, auto_paused_risk

    today = datetime.utcnow().strftime("%Y-%m-%d")
    curr_eq = get_total_equity_irt()

    if today != day_tag:
        day_tag = today
        day_start_equity = curr_eq
        auto_paused_risk = False

    if day_start_equity > 0:
        daily_dd_pct = ((curr_eq - day_start_equity) / day_start_equity) * 100.0
        if daily_dd_pct <= DAILY_MAX_DRAWDOWN_PCT and not auto_paused_risk:
            auto_paused_risk = True
            send_telegram_message(
                f"🛡️ <b>محافظ ریسک روزانه فعال شد!</b> [<code>{RUN_ID}</code>]\n"
                f"افت روزانه به <code>{daily_dd_pct:+.2f}%</code> رسید؛ ورود جدید تا روز بعد متوقف شد."
            )


def process_trading_step():
    global balance_irt, open_position, trade_counter, cooldown_remaining, consecutive_losses, auto_paused_risk

    with state_lock:
        if not trading_enabled:
            return

    ok, any_closed = update_multi_tf_candles()
    if not ok:
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with state_lock:
        if not trading_enabled:
            return

        check_daily_reset_and_risk_guards()

        if open_position is not None:
            sym = open_position["symbol"]
            curr_price = get_current_symbol_price(sym)
            if not curr_price or curr_price <= 0:
                return

            curr_price_txt = get_current_symbol_price_formatted(sym)
            entry_p = open_position["entry_price"]
            entry_p_txt = open_position.get("entry_price_txt") or format_irt(entry_p, None, with_unit=True)
            qty = open_position["qty"]

            gross_pnl_pct = ((curr_price - entry_p) / entry_p) * 100.0
            peak_pct = max(open_position.get("peak_pct", gross_pnl_pct), gross_pnl_pct)
            open_position["peak_pct"] = peak_pct

            eff_sl = STOP_LOSS_PCT
            mode_txt = "حد ضرر کوتاه (خروج سریع در ضرر)"

            if peak_pct >= BREAKEVEN_TRIGGER_PCT:
                eff_sl = max(eff_sl, (FEE_RATE * 200.0) + 0.08)
                mode_txt = "نقطه سر‌به‌سر (پوشش کارمزد)"

            if peak_pct >= (BREAKEVEN_TRIGGER_PCT + TRAILING_STOP_PCT):
                eff_sl = max(eff_sl, peak_pct - TRAILING_STOP_PCT)
                mode_txt = "حد سود متحرک کوتاه"

            exit_reason = None
            if gross_pnl_pct >= TAKE_PROFIT_PCT:
                exit_reason = f"هدف سود کوتاه ({TAKE_PROFIT_PCT:+.2f}%)"
            elif gross_pnl_pct <= eff_sl:
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
                if profit_irt < 0:
                    cooldown_remaining = COOLDOWN_CYCLES
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES and not auto_paused_risk:
                        auto_paused_risk = True
                        send_telegram_message(
                            f"🛡️ <b>توقف محافظتی پس از {consecutive_losses} ضرر متوالی!</b> [<code>{RUN_ID}</code>]\n"
                            "ورودهای جدید موقتاً متوقف شد تا حساب حفظ شود."
                        )
                else:
                    consecutive_losses = 0

                rec = {
                    "id": open_position["id"],
                    "symbol": sym,
                    "trade_type": open_position.get("trade_type", "چنج / خرید صعودی (LONG)"),
                    "entry_price": entry_p,
                    "entry_price_txt": entry_p_txt,
                    "exit_price": curr_price,
                    "exit_price_txt": curr_price_txt,
                    "profit_irt": profit_irt,
                    "pnl_pct": net_pnl_pct,
                    "reason": exit_reason,
                    "time": now_str,
                }
                closed_trades.append(rec)
                open_position = None

                send_telegram_message(
                    f"📤 <b>خروج (چنج/فروش — SELL) #{rec['id']} | {sym}</b> [<code>{RUN_ID}</code>]\n"
                    f"• نوع معامله: <b>{rec['trade_type']}</b>\n"
                    f"• قیمت ورود (تومانی): <code>{entry_p_txt}</code>\n"
                    f"• قیمت خروج (تومانی): <code>{curr_price_txt}</code>\n"
                    f"• علت: <b>{exit_reason}</b>\n"
                    f"• سود/ضرر خالص (پس از کارمزد): <code>{format_irt(profit_irt, None, with_unit=True)}</code> (<code>{net_pnl_pct:+.2f}%</code>)\n"
                    f"• موجودی فرضی جدید: <b>{format_irt(balance_irt, None, with_unit=True)}</b>"
                )
            return

        if not any_closed or auto_paused_risk:
            return

        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            return

    best_sym = None
    best_score = MIN_SIGNAL_SCORE - 1
    best_body = 0.0
    best_reason = ""

    for sym in IRT_SYMBOLS:
        sc, body_p, rsn = analyze_symbol_smart(sym)
        if sc >= MIN_SIGNAL_SCORE and sc > best_score:
            best_score = sc
            best_sym = sym
            best_body = body_p
            best_reason = rsn

    with state_lock:
        if not trading_enabled or auto_paused_risk or open_position is not None or not best_sym:
            return

        curr_p = get_current_symbol_price(best_sym)
        if not curr_p or curr_p <= 0 or balance_irt < 50000:
            return

        curr_p_txt = get_current_symbol_price_formatted(best_sym)
        spend_irt = round(balance_irt * TRADE_ALLOCATION_PCT, 0)
        if spend_irt <= 0:
            return

        eff_irt = spend_irt * (1.0 - FEE_RATE)
        qty = round(eff_irt / curr_p, 6)
        if qty <= 0:
            return

        try:
            execute_tabdeal_order(best_sym, "BUY", qty)
        except Exception as e:
            send_telegram_message(f"⚠️ خطا در ورود {best_sym}: <code>{e}</code>")
            return

        balance_irt -= spend_irt
        trade_counter += 1
        trade_type_label = f"چنج / خرید صعودی (LONG: تومان → {best_sym.replace('_IRT', '')})"
        open_position = {
            "id": trade_counter,
            "symbol": best_sym,
            "trade_type": trade_type_label,
            "entry_price": curr_p,
            "entry_price_txt": curr_p_txt,
            "qty": qty,
            "spent_irt": spend_irt,
            "peak_pct": 0.0,
            "opened_at": now_str,
        }

        tp_p = curr_p * (1.0 + TAKE_PROFIT_PCT / 100.0)
        sl_p = curr_p * (1.0 + STOP_LOSS_PCT / 100.0)

        send_telegram_message(
            f"📥 <b>ورود هوشمند کوتاه #{trade_counter} | {best_sym}</b> [<code>{RUN_ID}</code>]\n"
            f"• نوع معامله: <b>{trade_type_label}</b>\n"
            f"• سرمایه فرضی تخصیص‌یافته: <b>{format_irt(spend_irt, None, with_unit=True)}</b>\n"
            f"• مقدار خرید: <code>{qty}</code> | قیمت ورود (تومانی): <code>{curr_p_txt}</code>\n"
            f"• امتیاز تکنیکال: <b>{best_score}</b> | کندل کوتاه: <code>{best_body:+.2f}%</code>\n"
            f"• سیگنال: <i>{best_reason}</i>\n"
            f"• هدف سود کوتاه: <code>{format_irt(tp_p, None, with_unit=True)}</code> ({TAKE_PROFIT_PCT:+.2f}%) | "
            f"حد ضرر: <code>{format_irt(sl_p, None, with_unit=True)}</code> ({STOP_LOSS_PCT:+.2f}%)\n"
            f"• موجودی فرضی باقی‌مانده: <b>{format_irt(balance_irt, None, with_unit=True)}</b>"
        )


def build_status_text():
    with state_lock:
        total_pnl = sum(t["profit_irt"] for t in closed_trades)
        wins = sum(1 for t in closed_trades if t["profit_irt"] > 0)
        total_closed = len(closed_trades)
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        eq = get_total_equity_irt()
        ret_pct = ((eq - INITIAL_IRT_BALANCE) / INITIAL_IRT_BALANCE) * 100.0

        if not trading_enabled:
            mode_s = "🔴 متوقف"
        elif auto_paused_risk:
            mode_s = "🟡 مکث محافظتی ریسک"
        else:
            mode_s = "🟢 روشن (هوشمند کوتاه تومانی)"

        live_s = "فعال (واقعی)" if LIVE_TRADING else "تست ایمن (موجودی فرضی)"
        feed_s = "پشتیبان فعال" if using_fallback_feed else "زنده تبدیل"
        pos_s = f"بله ({open_position['symbol']} | LONG/چنج)" if open_position else "خیر"
        cd_s = f"{cooldown_remaining} دوره" if cooldown_remaining > 0 else "خیر"
        max_fast = max((len(market_state[s]["fast_candles"]) for s in IRT_SYMBOLS), default=0)

        return (
            f"📊 <b>وضعیت ربات هوشمند تریدر تبدیل (تومانی)</b> [<code>{RUN_ID}</code>]\n"
            f"• وضعیت ترید: <b>{mode_s}</b> | سفارش: <b>{live_s}</b>\n"
            f"• فید قیمت: <b>{feed_s}</b> | کندل کوتاه: <b>{max_fast}</b>\n"
            f"• موجودی فرضی نقد: <b>{format_irt(balance_irt, None, with_unit=True)}</b>\n"
            f"• ارزش کل دارایی فرضی: <b>{format_irt(eq, None, with_unit=True)}</b> (<code>{ret_pct:+.2f}%</code>)\n"
            f"• سود/ضرر خالص بسته: <code>{format_irt(total_pnl, None, with_unit=True)}</code>\n"
            f"• نرخ برد: <b>{wins}/{total_closed}</b> (<code>{win_rate:.1f}%</code>) | ضرر پی‌درپی: <b>{consecutive_losses}</b>\n"
            f"• استراحت پس از ضرر: <b>{cd_s}</b> | پوزیشن باز: <b>{pos_s}</b>"
        )


def build_stats_text():
    with state_lock:
        tot = len(closed_trades)
        wins = [t for t in closed_trades if t["profit_irt"] > 0]
        losses = [t for t in closed_trades if t["profit_irt"] < 0]
        net_pnl = sum(t["profit_irt"] for t in closed_trades)
        avg_pnl = (net_pnl / tot) if tot > 0 else 0.0
        avg_win = (sum(t["profit_irt"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t["profit_irt"] for t in losses) / len(losses)) if losses else 0.0
        win_rate = (len(wins) / tot * 100.0) if tot > 0 else 0.0
        return (
            f"📈 <b>آمار دقیق معاملات هوشمند تومانی (LONG/چنج)</b> [<code>{RUN_ID}</code>]\n"
            f"• سرمایه فرضی اولیه: <b>{format_irt(INITIAL_IRT_BALANCE, None, with_unit=True)}</b>\n"
            f"• کل معاملات بسته: <b>{tot}</b> | سودده: <b>{len(wins)}</b> | زیان‌ده: <b>{len(losses)}</b>\n"
            f"• نرخ برد: <b>{win_rate:.1f}%</b>\n"
            f"• سود/ضرر خالص کل: <code>{format_irt(net_pnl, None, with_unit=True)}</code>\n"
            f"• میانگین هر معامله: <code>{format_irt(avg_pnl, None, with_unit=True)}</code>\n"
            f"• میانگین سودده: <code>{format_irt(avg_win, None, with_unit=True)}</code> | میانگین زیان‌ده: <code>{format_irt(avg_loss, None, with_unit=True)}</code>"
        )


def build_positions_text():
    with state_lock:
        if not open_position:
            return f"🔒 در حال حاضر پوزیشن بازی روی بازارهای تومانی وجود ندارد. [<code>{RUN_ID}</code>]"
        sym = open_position["symbol"]
        curr_p = get_current_symbol_price(sym) or open_position["entry_price"]
        curr_p_txt = get_current_symbol_price_formatted(sym)
        entry_p = open_position["entry_price"]
        entry_p_txt = open_position.get("entry_price_txt") or format_irt(entry_p, None, with_unit=True)
        pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
        peak_pct = open_position.get("peak_pct", pnl_pct)
        return (
            f"🔓 <b>پوزیشن باز #{open_position['id']} | {sym}</b> [<code>{RUN_ID}</code>]\n"
            f"• نوع معامله: <b>{open_position.get('trade_type', 'چنج / خرید صعودی (LONG)')}</b>\n"
            f"• سرمایه فرضی درگیر: <b>{format_irt(open_position['spent_irt'], None, with_unit=True)}</b>\n"
            f"• قیمت ورود (تومانی): <code>{entry_p_txt}</code>\n"
            f"• قیمت فعلی (تومانی): <code>{curr_p_txt}</code>\n"
            f"• سود/ضرر شناور: <code>{pnl_pct:+.2f}%</code> (اوج: <code>{peak_pct:+.2f}%</code>)"
        )


def build_history_text():
    with state_lock:
        if not closed_trades:
            return f"📭 هنوز معامله بسته‌شده‌ای ثبت نشده است. [<code>{RUN_ID}</code>]"
        recent = closed_trades[-6:]
        lines = [f"📜 <b>آخرین {len(recent)} معامله هوشمند تومانی (LONG/چنج)</b> [<code>{RUN_ID}</code>]"]
        for t in reversed(recent):
            in_txt = t.get("entry_price_txt") or format_irt(t["entry_price"], None, with_unit=True)
            out_txt = t.get("exit_price_txt") or format_irt(t["exit_price"], None, with_unit=True)
            lines.append(
                f"• #{t['id']} <b>{t['symbol']}</b> | ورود: <code>{in_txt}</code> → خروج: <code>{out_txt}</code> | "
                f"خالص: <code>{format_irt(t['profit_irt'], None, with_unit=True)}</code> (<code>{t['pnl_pct']:+.2f}%</code>)"
            )
        return "\n".join(lines)


def build_prices_text():
    fetch_irt_prices_map()
    with state_lock:
        lines = [
            f"💰 <b>قیمت ارزهای تومانی در موتور ترید ({len(IRT_SYMBOLS)} بازار)</b> [<code>{RUN_ID}</code>]"
        ]
        for sym in IRT_SYMBOLS:
            p_txt = get_current_symbol_price_formatted(sym)
            lines.append(f"• <b>{sym}</b>: <code>{p_txt}</code>")
        return "\n".join(lines)


def build_market_text():
    with state_lock:
        lines = [
            f"💹 <b>وضعیت بازارهای تومانی ({len(IRT_SYMBOLS)} بازار)</b> [<code>{RUN_ID}</code>]"
        ]
        for sym in IRT_SYMBOLS[:20]:
            p_txt = get_current_symbol_price_formatted(sym)
            c_cnt = len(market_state[sym]["fast_candles"])
            lines.append(f"• <b>{sym}</b>: <code>{p_txt}</code> | کندل کوتاه: <b>{c_cnt}</b>")
        if len(IRT_SYMBOLS) > 20:
            lines.append("<i>... برای دیدن همه قیمت‌ها دستور /prices را بفرستید.</i>")
        return "\n".join(lines)


def reset_simulation():
    global balance_irt, open_position, closed_trades, trade_counter, cooldown_remaining, consecutive_losses, auto_paused_risk, day_start_equity
    with state_lock:
        balance_irt = INITIAL_IRT_BALANCE
        open_position = None
        closed_trades = []
        trade_counter = 0
        cooldown_remaining = 0
        consecutive_losses = 0
        auto_paused_risk = False
        day_start_equity = INITIAL_IRT_BALANCE


def handle_command(raw_text: str, chat_id: str):
    global trading_enabled, auto_paused_risk
    cmd = raw_text.strip().split()[0].split("@")[0].lower()

    if cmd in ("/start", "/help"):
        send_telegram_message(
            f"🤖 <b>ربات هوشمند تریدر تومانی</b> [<code>{RUN_ID}</code>]\n"
            "• /trade_on — شروع و ازسرگیری ترید هوشمند\n"
            "• /trade_off — قطع فوری ترید\n"
            "• /status — وضعیت کلی، موجودی فرضی و کندل‌ها\n"
            "• /stats — آمار دقیق سود/ضرر، نرخ برد و میانگین‌ها\n"
            "• /positions — پوزیشن باز فعلی و قیمت ورود/فعلی تومانی\n"
            "• /history — آخرین معاملات بسته با قیمت ورود و خروج تومانی\n"
            "• /market — خلاصه وضعیت بازارهای تومانی\n"
            "• /prices — لیست قیمت‌های فعال در موتور ترید\n"
            "• /reset — ریست موجودی فرضی به 10,000,000 تومان",
            chat_id,
        )
    elif cmd in ("/prices", "/price"):
        send_telegram_message(build_prices_text(), chat_id)
    elif cmd in ("/trade_on", "/run"):
        with state_lock:
            trading_enabled = True
            auto_paused_risk = False
        send_telegram_message(f"🟢 ترید هوشمند تومانی فعال شد. [<code>{RUN_ID}</code>]", chat_id)
    elif cmd in ("/trade_off", "/stop"):
        with state_lock:
            trading_enabled = False
        send_telegram_message(f"🛑 ترید هوشمند تومانی متوقف شد. [<code>{RUN_ID}</code>]", chat_id)
    elif cmd == "/status":
        send_telegram_message(build_status_text(), chat_id)
    elif cmd == "/stats":
        send_telegram_message(build_stats_text(), chat_id)
    elif cmd == "/positions":
        send_telegram_message(build_positions_text(), chat_id)
    elif cmd == "/history":
        send_telegram_message(build_history_text(), chat_id)
    elif cmd == "/market":
        send_telegram_message(build_market_text(), chat_id)
    elif cmd == "/reset":
        reset_simulation()
        send_telegram_message(
            f"🔄 موجودی فرضی ریست شد: <b>{format_irt(INITIAL_IRT_BALANCE, None, with_unit=True)}</b> [<code>{RUN_ID}</code>]",
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
        f"🚀 <b>ربات هوشمند تریدر تومانی آماده شد!</b> [<code>{RUN_ID}</code>]\n"
        f"• تعداد بازارهای IRT تحت نظر: <b>{len(IRT_SYMBOLS)} ارز</b>\n"
        f"• سرمایه فرضی: <b>{format_irt(INITIAL_IRT_BALANCE, None, with_unit=True)}</b> | هدف سود کوتاه: <code>{TAKE_PROFIT_PCT:+.2f}%</code> | حد ضرر: <code>{STOP_LOSS_PCT:+.2f}%</code>\n"
        f"• شروع ترید: <code>/trade_on</code> | قطع فوری: <code>/trade_off</code> | وضعیت: <code>/status</code>"
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
