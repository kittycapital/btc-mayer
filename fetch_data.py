#!/usr/bin/env python3
"""
BTC Mayer Multiple Dashboard - Data Processor
Calculates 200DMA, Mayer Multiple, detects buy/sell zones, computes forward returns.
"""

import csv
import json
import os
import math
from datetime import datetime, timedelta
from collections import OrderedDict

# ============================================================
# CONFIG
# ============================================================
CSV_PATH = os.path.join(os.path.dirname(__file__), 'data', 'BTC_USD.csv')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'data', 'btc_mayer.json')

BUY_THRESHOLD = 0.8       # Mayer ≤ 0.8 = Buy zone
SELL_THRESHOLD = 2.4       # Mayer ≥ 2.4 = Sell zone
TOUCH_THRESHOLD = 0.8      # Backtest trigger
TOUCH_MIN_GAP_DAYS = 120   # Min days between separate touch events
TOUCH_EXIT_THRESHOLD = 1.0 # Mayer > 1.0 = exited buy zone

FORWARD_PERIODS = {
    '1mo': 30,
    '3mo': 90,
    '6mo': 180,
    '12mo': 365
}

SIMULATION_AMOUNT = 10000  # $10K

# ============================================================
# READ CSV
# ============================================================
def read_btc_csv(path):
    rows = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                date_str = row['Date'].strip()
                close = float(row['Close'].strip())
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                rows.append((dt, close))
            except (ValueError, KeyError):
                continue
    rows.sort(key=lambda x: x[0])
    return rows

# ============================================================
# CALCULATE 200DMA & MAYER MULTIPLE
# ============================================================
def calculate_mayer(daily_data):
    """
    Calculate 200-day moving average and Mayer Multiple.
    Returns list of (date, close, dma200, mayer)
    """
    result = []
    closes = [d[1] for d in daily_data]

    for i in range(len(daily_data)):
        if i < 199:
            result.append((daily_data[i][0], daily_data[i][1], None, None))
        else:
            window = closes[i - 199:i + 1]
            dma = sum(window) / 200
            mayer = daily_data[i][1] / dma
            result.append((daily_data[i][0], daily_data[i][1], dma, mayer))

    return result

# ============================================================
# RESAMPLE TO WEEKLY (for chart - reduce data points)
# ============================================================
def resample_weekly(mayer_data):
    """Take every 7th data point for chart rendering performance."""
    weekly = []
    for i, (dt, close, dma, mayer) in enumerate(mayer_data):
        if i % 7 == 0 or i == len(mayer_data) - 1:
            weekly.append((dt, close, dma, mayer))
    return weekly

# ============================================================
# DETECT BUY ZONE TOUCHES
# ============================================================
def detect_touches(mayer_data):
    """Detect periods where Mayer ≤ TOUCH_THRESHOLD."""
    valid = [(dt, close, dma, mayer) for dt, close, dma, mayer in mayer_data if mayer is not None]

    events = []
    in_event = False
    current_event = []

    for dt, close, dma, mayer in valid:
        if not in_event:
            if mayer <= TOUCH_THRESHOLD:
                in_event = True
                current_event = [(dt, close, dma, mayer)]
        else:
            if mayer <= TOUCH_EXIT_THRESHOLD:
                current_event.append((dt, close, dma, mayer))
            else:
                if current_event:
                    events.append(current_event)
                current_event = []
                in_event = False

    if current_event:
        events.append(current_event)

    # Extract touch points (first entry into zone for each event)
    touch_points = []
    last_touch_date = None

    for event in events:
        touch_date, touch_price, touch_dma, touch_mayer = event[0]

        # Find lowest mayer in event
        min_entry = min(event, key=lambda x: x[3])
        min_date, min_price, min_dma, min_mayer = min_entry

        if last_touch_date and (touch_date - last_touch_date).days < TOUCH_MIN_GAP_DAYS:
            continue

        duration_days = (event[-1][0] - event[0][0]).days

        touch_points.append({
            'date': touch_date,
            'price': touch_price,
            'dma200': touch_dma,
            'mayer': touch_mayer,
            'min_mayer': min_mayer,
            'min_price': min_price,
            'min_date': min_date,
            'event_start': event[0][0],
            'event_end': event[-1][0],
            'duration_days': duration_days,
            'still_active': False
        })

        last_touch_date = touch_date

    # Mark last event as active if recent
    if touch_points:
        last_end = touch_points[-1]['event_end']
        latest_date = valid[-1][0]
        if (latest_date - last_end).days <= 14:
            touch_points[-1]['still_active'] = True

    return touch_points

# ============================================================
# FORWARD RETURNS
# ============================================================
def calculate_forward_returns(touch_points, mayer_data):
    date_close = {dt: close for dt, close, dma, mayer in mayer_data}
    all_dates = sorted(date_close.keys())

    def find_closest(target):
        closest = None
        min_diff = timedelta(days=999)
        for d in all_dates:
            diff = abs(d - target)
            if diff < min_diff:
                min_diff = diff
                closest = d
        return closest if min_diff.days <= 7 else None

    for tp in touch_points:
        tp['returns'] = {}
        tp['sim_values'] = {}

        for label, days in FORWARD_PERIODS.items():
            future = tp['date'] + timedelta(days=days)
            closest = find_closest(future)

            if closest and closest <= all_dates[-1]:
                future_price = date_close[closest]
                ret = (future_price - tp['price']) / tp['price']
                tp['returns'][label] = round(ret * 100, 1)
                tp['sim_values'][label] = round(SIMULATION_AMOUNT * (1 + ret))
            else:
                tp['returns'][label] = None
                tp['sim_values'][label] = None

    return touch_points

# ============================================================
# HISTOGRAM DATA
# ============================================================
def build_histogram(mayer_data):
    """Build histogram bins for Mayer Multiple distribution."""
    valid_mayers = [m for _, _, _, m in mayer_data if m is not None]

    # Create bins from 0 to 4.0 in 0.1 increments
    bins = []
    for i in range(50):  # 0.0 to 5.0
        low = i * 0.1
        high = (i + 1) * 0.1
        count = sum(1 for m in valid_mayers if low <= m < high)
        bins.append({
            'range': f'{low:.1f}-{high:.1f}',
            'low': round(low, 1),
            'high': round(high, 1),
            'count': count,
            'pct': round(count / len(valid_mayers) * 100, 2)
        })

    # Percentile of current value
    current_mayer = valid_mayers[-1]
    below = sum(1 for m in valid_mayers if m <= current_mayer)
    percentile = round(below / len(valid_mayers) * 100, 1)

    return bins, percentile, len(valid_mayers)

# ============================================================
# BUILD OUTPUT
# ============================================================
def build_output(mayer_data, weekly_data, touch_points, histogram_bins, percentile, total_days):
    # Chart data (weekly for performance)
    chart_data = OrderedDict()
    for dt, close, dma, mayer in weekly_data:
        key = dt.strftime('%Y-%m-%d')
        chart_data[key] = {
            'close': round(close, 2),
            'dma200': round(dma, 2) if dma else None,
            'mayer': round(mayer, 4) if mayer else None
        }

    # Current values
    latest = mayer_data[-1]

    # Touch events
    touches = []
    for tp in touch_points:
        touches.append({
            'date': tp['date'].strftime('%Y-%m-%d'),
            'date_label': tp['date'].strftime('%b %Y'),
            'price': round(tp['price'], 2),
            'dma200': round(tp['dma200'], 2),
            'mayer': round(tp['mayer'], 2),
            'min_mayer': round(tp['min_mayer'], 2),
            'duration_days': tp['duration_days'],
            'returns': tp['returns'],
            'sim_values': tp['sim_values'],
            'is_current': tp['still_active']
        })

    # Stats
    completed = [t for t in touches if not t['is_current']]
    stats = {
        'total_touches': len(touches),
        'completed': len(completed),
    }

    for period in ['1mo', '3mo', '6mo', '12mo']:
        returns = [t['returns'][period] for t in completed if t['returns'].get(period) is not None]
        if returns:
            stats[f'avg_return_{period}'] = round(sum(returns) / len(returns), 1)
            positive = sum(1 for r in returns if r > 0)
            stats[f'hit_rate_{period}'] = round(positive / len(returns) * 100)
        else:
            stats[f'avg_return_{period}'] = None
            stats[f'hit_rate_{period}'] = None

    output = {
        'metadata': {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
            'source': 'BTC_USD.csv',
            'dma_period': 200,
            'buy_threshold': BUY_THRESHOLD,
            'sell_threshold': SELL_THRESHOLD,
            'touch_threshold': TOUCH_THRESHOLD,
            'simulation_amount': SIMULATION_AMOUNT,
            'total_days': total_days,
        },
        'current': {
            'price': round(latest[1], 2),
            'dma200': round(latest[2], 2) if latest[2] else None,
            'mayer': round(latest[3], 4) if latest[3] else None,
            'percentile': percentile,
            'date': latest[0].strftime('%Y-%m-%d'),
        },
        'chart_data': chart_data,
        'histogram': histogram_bins,
        'touches': touches,
        'stats': stats,
    }

    return output

# ============================================================
# MAIN
# ============================================================
def main():
    print("📊 BTC Mayer Multiple Dashboard - Data Processor")
    print("=" * 50)

    daily = read_btc_csv(CSV_PATH)
    print(f"Read {len(daily)} daily records ({daily[0][0].date()} ~ {daily[-1][0].date()})")

    mayer_data = calculate_mayer(daily)
    valid = [(d, c, dma, m) for d, c, dma, m in mayer_data if m is not None]
    print(f"200DMA available from {valid[0][0].date()} ({len(valid)} days)")

    weekly_data = resample_weekly(mayer_data)
    print(f"Weekly chart data: {len(weekly_data)} points")

    touch_points = detect_touches(mayer_data)
    print(f"\n🔍 Touch Events (Mayer ≤ {TOUCH_THRESHOLD}): {len(touch_points)}")
    for tp in touch_points:
        print(f"  {tp['date'].strftime('%b %Y'):>10} | ${tp['price']:>10,.0f} | Mayer: {tp['mayer']:.2f} | Min: {tp['min_mayer']:.2f} | {tp['duration_days']}d")

    touch_points = calculate_forward_returns(touch_points, mayer_data)

    histogram_bins, percentile, total_days = build_histogram(mayer_data)
    print(f"\nCurrent Mayer: {valid[-1][3]:.4f} (percentile: {percentile}%)")

    output = build_output(mayer_data, weekly_data, touch_points, histogram_bins, percentile, total_days)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Saved to {OUTPUT_PATH}")
    print(f"   Price: ${output['current']['price']:,.0f}")
    print(f"   200DMA: ${output['current']['dma200']:,.0f}")
    print(f"   Mayer: {output['current']['mayer']:.4f}")

if __name__ == '__main__':
    main()
