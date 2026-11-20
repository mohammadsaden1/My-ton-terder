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

# برای حفظ امنیت: تا زمانی که خودت صریحاً "true" نکنی، سفارش واقعی ارسال نمی‌شود
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

# بازه نمونه‌برداری سریع و تایم‌فریم‌های کوتاه (1m برای واکنش سریع + 5m برای تایید روند کوتاه)
SAMPLE_SECONDS = int(os.environ.get("SAMPLE_SECONDS", "15"))
FAST_TF_SECONDS = int(os.environ.get("FAST_TF_SECONDS", "60"))
TREND_TF_SECONDS = int(os.environ.get("TREND_TF_SECONDS", "300"))

# سرمایه فرضی اولیه: 10,000,000 تومان
INITIAL_IRT_BALANCE = float(os.environ.get("INITIAL_IRT_BALANCE", "10000000.0"))
TRADE_ALLOCATION_PCT = float(os.environ.get("TRADE_ALLOCATION_PCT", "0.10"))
FEE_RATE = float(os.environ.get("FEE_RATE", "0.002"))  # کارمزد هر سمت (0.2% => رفت‌وبرگشت 0.4%)

# مدیریت سود کوتاه منطقی (بزرگ‌تر از کارمزد رفت‌وبرگشت) و ضرر کنترل‌شده
MIN_NET_BUFFER_PCT = (FEE_RATE * 200.0) + 0.35
TAKE_PROFIT_PCT = max(float(os.environ.get("TAKE_PROFIT_PCT", "1.15")), MIN_NET_BUFFER_PCT)
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-0.75"))
BREAKEVEN_TRIGGER_PCT = max(float(os.environ.get("BREAKEVEN_TRIGGER_PCT", "0.65")), (FEE_RATE * 200.0) + 0.15)
TRAILING_STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "0.35"))

# محافظ‌های ضدفرسایش و کنترل ریسک
COOLDOWN_CYCLES = int(os.environ.get("COOLDOWN_CYCLES", "2"))
MAX_CONSECUTIVE_LOSSES = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3"))
DAILY_MAX_DRAWDOWN_PCT = float(os.environ.get("DAILY_MAX_DRAWDOWN_PCT", "-3.5"))
MIN_SIGNAL_SCORE = int(os.environ.get("MIN_SIGNAL_SCORE", "4"))

RUN_ID = str(uuid.uuid4())[:8]

# تمام ارزهای تصاویر تبدیل (به‌همراه TON_IRT) به صورت جفت‌ارز تومانی
IRT_SYMBOLS = [
    "ETH_IRT", "DOGE_IRT", "DOT_IRT", "PUMP_IRT", "ONDO_IRT", "BTTC_IRT", "DOGS_IRT", "SUI_IRT",
    "FLOKI_IRT", "MOG_IRT", "TURBO_IRT", "STRK_IRT", "HBAR_IRT", "BOME_IRT", "AXS_IRT", "SEI_IRT",
    "ICP_IRT", "GIGA_IRT", "CGPT_IRT", "FET_IRT", "MEME_IRT", "ARKM_IRT", "AXL_IRT", "BONK_IRT",
    "CHZ_IRT", "FLUX_IRT", "COW_IRT", "DASH_IRT", "LINK_IRT", "NOT_IRT", "JASMY_IRT", "WIF_IRT",
    "FLR_IRT", "XCN_IRT", "BABYDOGE_IRT", "DUSK_IRT", "GALA_IRT", "HYPE_IRT", "ADA_IRT", "HAEDAL_IRT",
    "XRP_IRT", "CAT_IRT", "XMR_IRT", "ACH_IRT", "BTC_IRT", "SHIB_IRT", "SOL_IRT", "AVAX_IRT",
    "TON_IRT",
]

market_state = {
    sym: {
        "last_price": None,
        "last_price_raw": None,
        "last_updated": None,
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

last_update_id = 0
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
    """نمایش دقیق قیمت یا مبلغ تومانی بدون حذف ارقام واقعی دریافت‌شده از صرافی تبدیل"""
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


def fetch_irt_prices_map():
    """دریافت قیمت لحظه‌ای دقیق تومانی از SDK رسمی تبدیل یا APIهای تبدیل"""
    prices = {}

    if spot_client is not None:
        try:
            for attr_name in ("get_ticker_price", "ticker_price", "get_ticker"):
                if hasattr(spot_client, attr_name):
                    fn = getattr(spot_client, attr_name)
                    for sym in IRT_SYMBOLS:
                        if sym in prices:
                            continue
                        try:
                            data = fn(symbol=sym)
                            p, raw_s = _extract_price_from_obj(data if isinstance(data, dict) else {})
                            if p is not None:
                                prices[sym] = (p, raw_s)
                        except Exception:
                            continue
                    if prices:
                        break
        except Exception as e:
            print("Tabdeal SDK price warning:", repr(e))

    missing = [s for s in IRT_SYMBOLS if s not in prices]
    if missing:
        wanted_with_us = set(missing)
        wanted_no_us = {s.replace("_", "") for s in missing}
        rev_map = {s.replace("_", ""): s for s in missing}

        # ۱. تلاش برای دریافت تیکر یا دفتر سفارش/قیمت از API عمومی تبدیل
        bulk_urls = [
            "https://api.tabdeal.org/r/api/v1/ticker/24hr",
            "https://api1.tabdeal.org/r/api/v1/ticker/24hr",
            "https://api.tabdeal.org/r/api/v1/market/ticker",
        ]
        for url in bulk_urls:
            try:
                res = http_session.get(url, timeout=8)
                if res.status_code == 200:
                    payload = res.json()
                    items = payload if isinstance(payload, list) else payload.get("data", payload)
                    if isinstance(items, dict):
                        items = [dict(v, symbol=k) if isinstance(v, dict) else {"symbol": k, "price": v} for k, v in items.items()]
                    if isinstance(items, list):
                        for row in items:
                            if not isinstance(row, dict):
                                continue
                            raw_sym = str(row.get("symbol") or row.get("pair") or row.get("market") or "").upper()
                            target_sym = None
                            if raw_sym in wanted_with_us:
                                target_sym = raw_sym
                            elif raw_sym in wanted_no_us:
                                target_sym = rev_map.get(raw_sym)
                            if target_sym and target_sym not in prices:
                                p, raw_s = _extract_price_from_obj(row)
                                if p is not None:
                                    prices[target_sym] = (p, raw_s)
                    if prices:
                        break
            except Exception as e:
                print(f"Tabdeal bulk endpoint {url} error:", repr(e))

        # ۲. در صورت نبودِ endpoint کلی 24hr، دریافت قیمت هر نماد از depth/orderbook تبدیل
        still_missing = [s for s in IRT_SYMBOLS if s not in prices]
        if still_missing:
            for sym in still_missing:
                for base in ("https://api.tabdeal.org/r/api/v1", "https://api1.tabdeal.org/r/api/v1"):
                    try:
                        r_ob = http_session.get(f"{base}/depth", params={"symbol": sym, "limit": 5}, timeout=6)
                        if r_ob.status_code == 200:
                            ob = r_ob.json()
                            bids = ob.get("bids") or []
                            asks = ob.get("asks") or []
                            if bids and asks:
                                b_p = float(bids[0][0])
                                a_p = float(asks[0][0])
                                if b_p > 0 and a_p > 0:
                                    mid_p = (b_p + a_p) / 2.0
                                    prices[sym] = (mid_p, str(mid_p))
                                    break
                    except Exception:
                        continue

    return prices


def execute_tabdeal_order(symbol: str, side: str, quantity: float):
    """اجرای سفارش BUY/SELL در تبدیل؛ در حالت LIVE_TRADING=False فقط شبیه‌سازی ایمن انجام می‌دهد"""
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
    now_str = datetime.utcnow().strftime("%Y-%m-%
