"""Demand model: least-squares regression on trend + day-of-week seasonality,
scaled by a weather-risk uplift. Residual spread gives shortage probability."""
import math, numpy as np

def _X(t):
    return np.column_stack([np.ones(len(t)), t] + [(t % 7 == k).astype(float) for k in range(1, 7)])

def forecast(hist, stock, days, weather_risk=0):
    y = np.array(hist, float); n = len(y)
    if n < 14: raise ValueError('Unable to generate forecast. Please provide historical data (14+ days).')
    t = np.arange(n); beta, *_ = np.linalg.lstsq(_X(t), y, rcond=None)
    res = y - _X(t) @ beta; sd = float(res.std()); uplift = 1 + weather_risk / 400
    pred = np.maximum(_X(np.arange(n, n + days)) @ beta, 0) * uplift
    cum = np.cumsum(pred); total = float(cum[-1])
    day = next((i + 1 for i, c in enumerate(cum) if c > stock), None)
    z = (stock - total) / (sd * math.sqrt(days) + 1e-9)
    prob = 1 / (1 + math.exp(max(-30, min(30, 1.7 * z))))
    r2 = max(0.0, 1 - res.var() / (y.var() + 1e-9))
    conf = max(50, min(97, round(100 * (0.55 + 0.4 * r2 - 0.2 * sd / (y.mean() + 1e-9)))))
    wk = [float(np.mean(y[t % 7 == k])) for k in range(7)]
    why = [f"Trend: {beta[1]:+.2f} units/day change per day over {n} days of history.",
           f"Weekly pattern: demand peaks on cycle day {int(np.argmax(wk))+1} (avg {max(wk):.0f}) and is lowest on day {int(np.argmin(wk))+1} (avg {min(wk):.0f}).",
           f"Weather risk {weather_risk:.0f}/100 raises demand by {100*(uplift-1):.1f}%.",
           f"Stock {stock:.0f} vs predicted {total:.0f} over {days} days: shortage probability {prob*100:.0f}%."]
    return dict(pred=[round(float(p), 1) for p in pred], total=round(total, 1), shortage_day=day, prob=round(prob, 3),
                recommended=round(max(0, total * 1.15 - stock), 0), confidence=conf, why=why,
                level='CRITICAL' if prob > .85 else 'HIGH' if prob > .6 else 'MODERATE' if prob > .3 else 'LOW')

if __name__ == '__main__':
    import random; h = [100 + i * .5 + 15 * math.sin(i * 2 * math.pi / 7) + random.gauss(0, 4) for i in range(45)]
    print(forecast(h, 900, 15, 30))
