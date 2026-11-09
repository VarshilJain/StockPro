"""
market_strength_screens.py (v3.2)
==================================
Refined VCP logic:
- Detects multi-wave contraction sequences without over-penalizing 1 intermediate wave
- Latest contraction <= 12.5%
- Overall base contraction (first > latest)
- Distance from pivot <= 15.0%
- RS >= 50 for broad pool, RS >= 70 for primary leadership screen
"""

import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Users\Varshil Jain\Desktop\StockPro")
import database


def compute_minervini_trend_score(grp):
    """Minervini 8-point trend score."""
    if len(grp) < 30:
        return 0
    latest = grp.iloc[-1]
    close = float(latest['Close'])
    sma50 = latest.get('SMA50')
    sma200 = latest.get('SMA200')
    w52_h = latest.get('52w_high')
    w52_l = latest.get('52w_low')

    score = 0
    if not pd.isna(sma50) and close > float(sma50):
        score += 1

    sma150 = grp['Close'].tail(150).mean() if len(grp) >= 50 else np.nan
    if not pd.isna(sma150) and close > sma150:
        score += 1
    if not pd.isna(sma200) and close > float(sma200):
        score += 1
    if not pd.isna(sma50) and not pd.isna(sma150) and float(sma50) > sma150:
        score += 1
    if not pd.isna(sma150) and not pd.isna(sma200) and sma150 > float(sma200):
        score += 1

    if len(grp) >= 25:
        sma200_t20 = grp.iloc[-20].get('SMA200')
        if not pd.isna(sma200) and not pd.isna(sma200_t20) and float(sma200) > float(sma200_t20):
            score += 1

    if not pd.isna(w52_l) and float(w52_l) > 0 and close >= 1.30 * float(w52_l):
        score += 1
    if not pd.isna(w52_h) and float(w52_h) > 0 and close >= 0.75 * float(w52_h):
        score += 1

    return score


def extract_vcp_pullbacks(highs, lows, closes):
    """Extracts progressive contraction wave sequence."""
    n = len(highs)
    if n < 25:
        return None

    window = min(n, 70)
    sub_h = highs[-window:]
    sub_l = lows[-window:]

    piv_h = []
    piv_l = []
    for i in range(1, window - 1):
        if sub_h[i] >= sub_h[i-1] and sub_h[i] >= sub_h[i+1]:
            piv_h.append((i, sub_h[i]))
        if sub_l[i] <= sub_l[i-1] and sub_l[i] <= sub_l[i+1]:
            piv_l.append((i, sub_l[i]))

    waves = []
    for hi_idx, hi_val in piv_h:
        sub_lows = [lo_val for lo_idx, lo_val in piv_l if lo_idx > hi_idx]
        if sub_lows:
            drop = (hi_val - min(sub_lows[:2])) / hi_val * 100.0
            if 3.0 <= drop <= 40.0:
                waves.append(round(drop, 1))

    clean = []
    for w in waves:
        if not clean or abs(w - clean[-1]) >= 1.0:
            clean.append(w)

    clean = clean[-5:]
    if len(clean) < 2:
        return None

    latest = clean[-1]
    if latest > 12.5:
        return None
    if clean[0] <= latest:
        return None

    return clean


def detect_refined_cwh(grp):
    """
    Detects standard Cup with Handle pattern based on classical IBD/O'Neil rules:
    - Cup Formation Duration: 7 to 65 weeks (35 to 325 trading days)
    - Handle Formation Duration: 1 to 4 weeks (minimum 4-5 trading days up to 20 days)
    - Cup Depth: 12% to 35%
    - Handle Depth: 2% to 12% (must remain in upper half of the cup)
    - Left rim precedes cup bottom, which precedes right rim
    - Symmetry: left rim and right rim prices within 15% of each other
    - Current close is within -2.0% to +5.0% of pivot (basing or fresh breakout)
    """
    n = len(grp)
    if n < 40:
        return None

    highs = grp['High'].values
    lows = grp['Low'].values
    closes = grp['Close'].values
    current_close = float(closes[-1])

    dates = [
        pd.to_datetime(d).strftime("%Y-%m-%d")
        for d in grp["Timestamp"].values
    ]

    # Handle duration: 4 to 20 trading days (1 to 4 weeks)
    for h_len in range(4, 21):
        if n - h_len < 35:
            continue

        # Candidate cup lengths: 35 to 325 trading days (7 to 65 weeks)
        for cup_len in [35, 45, 55, 65, 80, 100, 130, 160, 200, 260, 320]:
            if cup_len > n - h_len:
                continue

            cup_start = n - h_len - cup_len
            cup_highs = highs[cup_start : n - h_len]
            cup_lows = lows[cup_start : n - h_len]

            # Left rim peak in first half of cup
            left_half_len = max(5, len(cup_highs) // 2)
            c_high_idx = int(np.argmax(cup_highs[:left_half_len]))
            left_peak = float(cup_highs[c_high_idx])
            left_date = dates[cup_start + c_high_idx]

            # Cup bottom strictly after left rim
            if c_high_idx + 1 >= len(cup_lows):
                continue
            c_low_idx_rel = int(np.argmin(cup_lows[c_high_idx + 1 :]))
            c_low_idx = c_high_idx + 1 + c_low_idx_rel
            cup_low = float(cup_lows[c_low_idx])
            cup_low_date = dates[cup_start + c_low_idx]

            # Right rim peak strictly after cup bottom
            right_rim_highs = highs[cup_start + c_low_idx : n]
            if len(right_rim_highs) == 0:
                continue
            r_rel = int(np.argmax(right_rim_highs))
            right_rim_idx = cup_start + c_low_idx + r_rel

            actual_h_days = n - 1 - right_rim_idx
            if not (4 <= actual_h_days <= 20):
                continue

            pivot = float(highs[right_rim_idx])
            right_date = dates[right_rim_idx]

            # Handle lows between right rim and current bar
            h_lows = lows[right_rim_idx :]
            handle_low = float(np.min(h_lows))
            h_low_idx = right_rim_idx + int(np.argmin(h_lows))
            handle_date = dates[h_low_idx]

            # Depths
            cup_depth = (left_peak - cup_low) / left_peak * 100.0
            handle_depth = (pivot - handle_low) / pivot * 100.0

            if not (12.0 <= cup_depth <= 35.0):
                continue
            if not (2.0 <= handle_depth <= 12.0):
                continue

            pct_vs_pivot = (pivot - current_close) / pivot * 100.0
            if not (-2.0 <= pct_vs_pivot <= 5.0):
                continue

            # Handle low must remain in upper 60% of cup
            mid_point = cup_low + 0.40 * (left_peak - cup_low)
            if handle_low < mid_point:
                continue

            # Left rim vs Right rim price symmetry (within 15%)
            if abs(pivot - left_peak) / max(pivot, left_peak) > 0.15:
                continue

            status = "breakout" if current_close > pivot else ("handle_forming" if pct_vs_pivot <= 5.0 else "basing")

            return {
                "Pivot": round(pivot, 2),
                "%_vs_Pivot": round(-pct_vs_pivot, 1),
                "Cup_Depth_%": round(cup_depth, 1),
                "Cup_Weeks": round(cup_len / 5, 1),
                "Cup_Days": cup_len,
                "Handle_Depth_%": round(handle_depth, 1),
                "Handle_Days": actual_h_days,
                "Handle_Weeks": round(actual_h_days / 5, 1),
                "pattern_points": {
                    "left_rim": {"date": left_date, "price": round(left_peak, 2)},
                    "cup_bottom": {"date": cup_low_date, "price": round(cup_low, 2)},
                    "right_rim": {"date": right_date, "price": round(pivot, 2)},
                    "handle_low": {"date": handle_date, "price": round(handle_low, 2)},
                    "status": status,
                },
            }

    return None


def run_improved_screens(target_date="2026-11-03"):
    conn = database.get_connection()
    query = f"""
        SELECT 
            Symbol, Timestamp, Open, High, Low, Close, Volume,
            SMA50, SMA200, rs_rank, 52w_high, 52w_low,
            (Close * Volume) as daily_turnover
        FROM historical_data
        WHERE Timestamp <= '{target_date}'
        AND Timestamp >= DATE_SUB('{target_date}', INTERVAL 180 DAY)
        ORDER BY Symbol, Timestamp ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    turnover_20d = df.groupby("Symbol")["daily_turnover"].tail(20).groupby(df["Symbol"]).mean()
    liquid_symbols = set(turnover_20d[turnover_20d >= 30_000_000].index)

    df_liquid = df[df['Symbol'].isin(liquid_symbols)]
    grouped = dict(list(df_liquid.groupby("Symbol")))

    vcp_list = []
    cwh_list = []

    for sym, grp in grouped.items():
        latest = grp.iloc[-1]
        rs = latest['rs_rank']
        if pd.isna(rs):
            continue
        rs = float(rs)
        close = float(latest['Close'])
        trend_score = compute_minervini_trend_score(grp)

        # 1. VCP
        sma200 = latest.get('SMA200')
        if not pd.isna(sma200) and close >= float(sma200) and rs >= 50:
            pullbacks = extract_vcp_pullbacks(grp['High'].values, grp['Low'].values, grp['Close'].values)
            if pullbacks:
                last_pb = pullbacks[-1]
                pivot_high = float(grp['High'].tail(25).max())
                dist_pct = (pivot_high - close) / pivot_high * 100.0

                if dist_pct <= 15.0:
                    vcp_score = int(round(trend_score * 5 + rs * 0.4 + (15 - last_pb) * 1.5))
                    vcp_score = min(max(vcp_score, 50), 99)
                    
                    vcp_list.append({
                        "Symbol": sym.replace(".NS", ""),
                        "Close": round(close, 2),
                        "Pill": f"{int(round(last_pb))}% last",
                        "Pullbacks": " > ".join(str(w) for w in pullbacks),
                        "From_Pivot_%": round(-dist_pct, 1),
                        "VCP_Score": vcp_score,
                        "RS": int(rs),
                        "Trend": f"{trend_score}/8"
                    })

        # 2. CWH
        if rs >= 60:
            cwh = detect_refined_cwh(grp)
            if cwh:
                cwh_list.append({
                    "Symbol": sym.replace(".NS", ""),
                    "Close": round(close, 2),
                    "Pivot": cwh['Pivot'],
                    "%_vs_Pivot": cwh['%_vs_Pivot'],
                    "Cup_Depth_%": cwh['Cup_Depth_%'],
                    "Handle_Depth_%": cwh['Handle_Depth_%'],
                    "RS": int(rs),
                    "Trend": f"{trend_score}/8"
                })

    df_vcp = pd.DataFrame(vcp_list)
    if not df_vcp.empty:
        df_vcp = df_vcp.sort_values(by=["RS", "VCP_Score"], ascending=[False, False])

    df_cwh = pd.DataFrame(cwh_list)
    if not df_cwh.empty:
        df_cwh = df_cwh.sort_values(by=["RS", "%_vs_Pivot"], ascending=[False, False])

    return df_vcp, df_cwh


if __name__ == "__main__":
    target_date = "2026-11-03"
    df_vcp, df_cwh = run_improved_screens(target_date)

    targets_vcp = ['ACMESOLAR', 'GESHIP', 'PERSISTENT', 'TIPSMUSIC', 'HEROMOTOCO', 'ULTRACEMCO', 'LTFOODS', 'CHOICEIN']
    matched = df_vcp[df_vcp['Symbol'].isin(targets_vcp)]
    print("\n=== WEBSITE VCP TARGETS CAPTURED ON 3 NOV ===")
    print(matched[['Symbol', 'Close', 'Pill', 'Pullbacks', 'RS', 'Trend']].to_string(index=False))
