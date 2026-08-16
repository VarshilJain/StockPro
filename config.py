import os
from dotenv import load_dotenv

# Load .env file if present (won't fail if missing)
load_dotenv()

DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'user':     os.getenv('DB_USER',     'root'),
    'password': os.getenv('DB_PASSWORD', 'root'),
    'database': os.getenv('DB_NAME',     'stock_data'),
}

# OpenRouter API settings
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'openai/gpt-oss-120b:free')
FUNDAMENTALS_CACHE_TTL_HOURS = int(os.getenv('FUNDAMENTALS_CACHE_TTL_HOURS', '24'))

# JWT / Auth settings
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'CHANGE_ME_IN_PRODUCTION_USE_A_LONG_RANDOM_SECRET')
JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '24'))

# ---------------------------------------------------------------------------
# Signal computation constants
# Change values here to tune all signals at once — do NOT hardcode elsewhere.
# ---------------------------------------------------------------------------

# Calendar / trading-day conversions
TRADING_DAYS_PER_YEAR = 252          # used for 52w / 2y / 5y / 10y windows

# Candlestick body / wick ratios
DOJI_BODY_RATIO  = 0.10              # body ≤ 10% of high-low range → Doji
WICK_BODY_RATIO  = 0.10              # wick ≤ 10% of body → Hammer / Shooting Star

# RSI thresholds
# rsi_lt_30 / rsi_gt_70 use the textbook 30/70 levels.
# oversold / overbought use tighter 25/80 levels to flag *extreme* conditions
# separately from the standard zone — both flags coexist intentionally.
RSI_LT_THRESHOLD   = 30             # rsi_lt_30 flag: RSI strictly < 30
RSI_GT_THRESHOLD   = 70             # rsi_gt_70 flag: RSI strictly > 70
RSI_OVERSOLD_LEVEL = 25             # oversold flag: RSI < 25  (extreme)
RSI_OVERBOUGHT_LEVEL = 80           # overbought flag: RSI > 80  (extreme)

# Volume
HRV_MULTIPLIER = 3.0                # High Relative Volume: current > avg * this

# ADX / directional movement
ADX_THRESHOLD = 25                  # ADX must exceed this to confirm a trend

# Delivery Momentum Signal
DELIVERY_RECENT_WINDOW     = 5      # Recent comparison window size (t-4 to t)
DELIVERY_BASELINE_WINDOW   = 22     # Baseline comparison window size (t-26 to t-5)
DELIVERY_BASELINE_GAP      = 5      # Non-overlapping gap separating baseline from current day
DELIVERY_MIN_BASELINE_DAYS = 15     # Minimum valid trading days required in baseline window
DELIVERY_MIN_RECENT_DAYS   = 3      # Minimum valid trading days required in recent window
DELIVERY_LAG_DAYS          = 0      # Default delivery publication lag (1 for T+1 publication)

# Pattern Screens & Base Detection
BASE_MIN_DROP_PCT = 5.0             # Minimum drop from a local high to establish a base pivot
VCP_MAX_PIVOT_DIST_PCT = 20.0       # Max percent distance from pivot for breakouts
VCP_MIN_CONTRACTIONS = 2            # Minimum successive contractions for VCP
BLUE_SKY_ATH_PROXIMITY_PCT = 3.0    # Max percent distance from ATH for a base to qualify
BLUE_SKY_CLOSE_ATH_PROXIMITY_PCT = 5.0 # Max percent distance from ATH for Close to qualify
BLUE_SKY_PIVOT_ATH_PROXIMITY = 0.97 # (Deprecated) Replaced by 1 - BLUE_SKY_ATH_PROXIMITY_PCT/100
MULTI_YEAR_BASE_MIN_DAYS = 365      # Minimum length (days) for a multi-year base
IPO_BASE_MIN_WEEKS = 2              # Min weeks since listing
IPO_BASE_MAX_WEEKS = 50             # Max weeks since listing
IPO_BASE_MIN_DAYS = 21              # Min length (days) for IPO base
IPO_BASE_MIN_DEPTH_PCT = 2.0        # Min depth percent for IPO base
IPO_BASE_MAX_DEPTH_PCT = 35.0       # Max depth percent for IPO base

# Missing Constants from Audit
BASE_SWING_LOOKBACK_DAYS = 21       # Lookback window for swing high detection
STAGE3_NET_PROGRESS_WINDOW = 30     # Days to check for net price progress in Stage 3
STAGE3_MAX_PROGRESS_PCT = 5.0       # Max percent progress allowed to be considered "flattening"

