import os
import logging
import secrets
from typing import Optional
from dotenv import load_dotenv


# Load .env file if present (won't fail if missing)
load_dotenv(override=True)

logger = logging.getLogger(__name__)

# Environment: 'development', 'staging', 'production'
STOCKPRO_ENV = os.getenv('STOCKPRO_ENV', 'development').strip().lower()
IS_PRODUCTION = STOCKPRO_ENV in ('production', 'prod')

# ---------------------------------------------------------------------------
# Database Configuration & Validation
# ---------------------------------------------------------------------------
_db_host = os.getenv('DB_HOST', 'localhost' if not IS_PRODUCTION else '')
_db_user = os.getenv('DB_USER', 'root' if not IS_PRODUCTION else '')
_db_pass = os.getenv('DB_PASSWORD', 'root' if not IS_PRODUCTION else '')
_db_name = os.getenv('DB_NAME', 'stock_data' if not IS_PRODUCTION else '')

DB_CONFIG = {
    'host':     _db_host,
    'user':     _db_user,
    'password': _db_pass,
    'database': _db_name,
}

# ---------------------------------------------------------------------------
# JWT / Auth Settings & Validation
# ---------------------------------------------------------------------------
_raw_jwt_secret = os.getenv('JWT_SECRET_KEY')
_insecure_defaults = {
    '',
    'CHANGE_ME_IN_PRODUCTION_USE_A_LONG_RANDOM_SECRET',
    'secret',
    'default',
    'password',
}

def validate_security_config(
    is_prod: bool = IS_PRODUCTION,
    jwt_secret: Optional[str] = _raw_jwt_secret,
    db_config: dict = None,
) -> None:
    """Validate critical security configurations and fail startup on unsafe settings in production."""
    db = db_config or DB_CONFIG
    if is_prod:
        if not db.get('host') or not db.get('user') or not db.get('password') or not db.get('database'):
            raise RuntimeError(
                "CRITICAL SECURITY ERROR: Database credentials (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME) "
                "must be explicitly configured via environment variables in production."
            )
        if str(db.get('user', '')).lower() == 'root' and str(db.get('password', '')).lower() == 'root':
            raise RuntimeError(
                "CRITICAL SECURITY ERROR: Default credentials (root/root) are strictly forbidden in production. "
                "Please configure a dedicated least-privilege database user."
            )
        if not jwt_secret or jwt_secret in _insecure_defaults or len(jwt_secret) < 32:
            raise RuntimeError(
                "CRITICAL SECURITY ERROR: JWT_SECRET_KEY must be set in production via environment variable "
                "and must be at least 32 characters long."
            )

# Run validation on module load if in production
if IS_PRODUCTION:
    validate_security_config()
    JWT_SECRET_KEY = _raw_jwt_secret
else:
    if not _raw_jwt_secret or _raw_jwt_secret in _insecure_defaults:
        logger.warning(
            "WARNING: Insecure or missing JWT_SECRET_KEY in development. "
            "Generating an ephemeral session secret. Tokens will invalidate on restart."
        )
        JWT_SECRET_KEY = secrets.token_urlsafe(32)
    else:
        JWT_SECRET_KEY = _raw_jwt_secret

JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '8'))


# Cookie & CSRF flags
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true' if IS_PRODUCTION else 'false').lower() == 'true'

# CORS Origins
_origins_raw = os.getenv('STOCKPRO_FRONTEND_ORIGINS', '')
STOCKPRO_FRONTEND_ORIGINS = [o.strip() for o in _origins_raw.split(',') if o.strip()]
if not STOCKPRO_FRONTEND_ORIGINS and not IS_PRODUCTION:
    STOCKPRO_FRONTEND_ORIGINS = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()

# Proxy & Rate Limiting Settings

BEHIND_TRUSTED_PROXY = os.getenv('BEHIND_TRUSTED_PROXY', 'false').lower() == 'true'
REDIS_URL = os.getenv('REDIS_URL')

# OpenRouter API settings
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'openai/gpt-oss-120b:free')
FUNDAMENTALS_CACHE_TTL_HOURS = int(os.getenv('FUNDAMENTALS_CACHE_TTL_HOURS', '24'))


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
VCP_MAX_PIVOT_DIST_PCT = 20.0       # Max percent distance from pivot for general base breakouts
PCS_MAX_PIVOT_DIST_PCT = 6.0        # Max percent distance from pivot for VCP tightened coil
VCP_MIN_CONTRACTIONS = 2            # Minimum successive contractions for VCP
BLUE_SKY_ATH_PROXIMITY_PCT = 5.0    # Max percent distance from ATH for base ceiling to qualify (BananaPatterns lvl 1 ATH)
BLUE_SKY_CLOSE_ATH_PROXIMITY_PCT = 20.0 # Max percent distance from ATH for Close to qualify (BananaPatterns ext [-20, 0])
BLUE_SKY_PIVOT_ATH_PROXIMITY = 0.95 # Base ceiling within 5% of ATH
MULTI_YEAR_BASE_MIN_DAYS = 250      # Minimum length (trading days, ~1 calendar year) for a multi-year base
IPO_BASE_MIN_WEEKS = 2              # Min weeks since listing
IPO_BASE_MAX_WEEKS = 50             # Max weeks since listing
IPO_BASE_MIN_DAYS = 21              # Min length (days) for IPO base
IPO_BASE_MIN_DEPTH_PCT = 2.0        # Min depth percent for IPO base
IPO_BASE_MAX_DEPTH_PCT = 35.0       # Max depth percent for IPO base


# VCP Enhancement: Base Width & Stage 2 Base Count
VCP_MIN_BASE_DAYS = 15              # Minimum base width in trading days (>= 3 weeks)
VCP_MAX_BASE_COUNT = 3              # Max Stage 2 base number to qualify (Base 1-3 = prime)
BASE_COUNT_MIN_ADVANCE_PCT = 20.0   # Min % rally after breakout to count as new base

# VCP Enhancement: 52-Week High Proximity (Minervini Trend Template)
VCP_MAX_OFF_52W_HIGH_PCT = 15.0     # Close must be within 15% of rolling 52-week high
VCP_MIN_ABOVE_52W_LOW_PCT = 30.0    # Close must be >= 30% above rolling 52-week low

# VCP Enhancement: Volume Dry-Up (VDU) Thresholds
VCP_VDU_BAR_RATIO = 0.60            # Single bar: Volume < 60% of 20-day avg volume
VCP_VDU_CLUSTER_RATIO = 0.75        # 5-day vol SMA <= 75% of 50-day vol SMA
VCP_VDU_ASYMMETRY_RATIO = 0.80      # Down-day avg volume < 80% of up-day avg volume

# VCP Enhancement: Volatility Compression (ATR%)
VCP_ATR_COMPRESSION_RATIO = 0.65    # Current ATR% / ATR%_at_base_start must be <= 0.65
VCP_ATR_PERIOD = 14                 # ATR computation period in trading days

# Missing Constants from Audit
BASE_SWING_LOOKBACK_DAYS = 21       # Lookback window for swing high detection
STAGE3_NET_PROGRESS_WINDOW = 30     # Days to check for net price progress in Stage 3
STAGE3_MAX_PROGRESS_PCT = 5.0       # Max percent progress allowed to be considered "flattening"

# ---------------------------------------------------------------------------
# VCP Enhancement v2.0: Two-Tier Architecture (Hard Gates & 100-Point Scoring)
# ---------------------------------------------------------------------------
VCP_MIN_HISTORY_BARS = 252          # Minimum required daily bars to evaluate VCP
VCP_ZIGZAG_MIN_REVERSAL_PCT = 2.0   # Floor minimum reversal % for adaptive ZigZag swings
VCP_ZIGZAG_ATR_MULT = 1.5           # ATR%14 multiplier for adaptive ZigZag swings
VCP_BASE_TRIGGER_DROP_PCT = 8.0     # Drop from recent peak required to trigger Base Start
VCP_GATE_MIN_WIDTH_DAYS = 15        # Minimum consolidation trading days (3 trading weeks)
VCP_GATE_MAX_WIDTH_DAYS = 65        # Maximum consolidation trading days (13 trading weeks)
VCP_GATE_MAX_CONTRACTION_RATIO = 0.75 # Hard limit: each D_k must be <= 0.75 * D_{k-1}
VCP_GATE_MAX_PIVOT_DIST_PCT = 6.0   # Maximum distance below pivot allowed (0 to 6%)
VCP_GATE_MIN_52W_LOW_PCT = 30.0     # Minimum % above 52-week low
VCP_GATE_MAX_52W_HIGH_PCT = 25.0    # Maximum % below 52-week high
VCP_GATE_MIN_RS_RANK = 70           # Minimum 12-month Relative Strength percentile

# VCP Scoring Weights (100 Points Total)
VCP_WEIGHT_PROGRESSION = 15.0       # Progression mean ratio D_k / D_{k-1}
VCP_WEIGHT_FINAL_DEPTH = 10.0       # Tightness of final contraction wave
VCP_WEIGHT_WAVE_COUNT = 5.0         # Number of contraction waves
VCP_WEIGHT_VOL_COMPRESSION = 15.0   # Final contraction volume vs 50-day SMA
VCP_WEIGHT_QUIETEST_DAY = 5.0       # 10-bar absolute base volume dry-up
VCP_WEIGHT_UP_DOWN_VOL = 5.0        # Up/Down volume asymmetry across base
VCP_WEIGHT_RS_RANK = 10.0           # Relative strength leadership
VCP_WEIGHT_52W_HIGH = 10.0          # Proximity to 52-week high
VCP_WEIGHT_BASE_COUNT = 10.0        # Early Stage 2 base maturity
VCP_WEIGHT_ATR_COMPRESSION = 10.0   # Normalized 14-day ATR% squeeze
VCP_WEIGHT_PIVOT_PROXIMITY = 5.0    # Tight positioning under pivot

# VCP Grade Cutoffs
VCP_GRADE_A_CUTOFF = 80.0           # Grade A: Elite Setup (Score >= 80)
VCP_GRADE_B_CUTOFF = 65.0           # Grade B: High Quality (Score 65 - 79)
VCP_GRADE_C_CUTOFF = 50.0           # Grade C: Developing / Speculative (Score 50 - 64)


