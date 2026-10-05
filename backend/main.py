import os, sys, sqlite3, hashlib, secrets, random, math, datetime as dt
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, R)
from ml.forecast import forecast
DB = os.path.join(R, 'database', 'dlms.db')
app = FastAPI(title='Defence Logistics Management System')
now = lambda: dt.datetime.now().strftime('%Y-%m-%d %H:%M')
today = dt.date.today
lvl = lambda r: 'LOW' if r < 25 else 'MODERATE' if r < 50 else 'HIGH' if r < 75 else 'CRITICAL'

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def q(sql, a=(), one=False):
    c = db(); rows = [dict(r) for r in c.execute(sql, a).fetchall()]; c.close()
    return (rows[0] if rows else None) if one else rows
def ex(sql, a=()):
    c = db(); cur = c.execute(sql, a); c.commit(); i = cur.lastrowid; c.close(); return i
def hp(p, s=None):
    s = s or secrets.token_hex(8); return s + '$' + hashlib.pbkdf2_hmac('sha256', p.encode(), s.encode(), 50000).hex()

# ---------- auth ----------
S = {}
def user(authorization: str = Header('')):
    u = S.get(authorization)
    if not u: raise HTTPException(401, 'Session expired. Please log in again.')
    return u
def writer(u=Depends(user)):
    if u['role'] == 'VIEWER': raise HTTPException(403, 'Read-only access: the Viewer role cannot modify data.')
    return u
def sess(name, role):
    t = secrets.token_hex(16); S[t] = {'username': name, 'role': role}; return {'token': t, **S[t]}
@app.post('/api/login')
def login(b: dict):
    u = q('SELECT * FROM users WHERE username=?', (str(b.get('username', '')).strip(),), True)
    if not u or hp(str(b.get('password', '')), u['pw'].split('$')[0]) != u['pw']: raise HTTPException(401, 'Invalid username or password.')
    return sess(u['username'], u['role'])
@app.post('/api/demo-login')
def demo_login(): return sess('demo.officer', 'LOGISTICS OFFICER')

# ---------- domain helpers ----------
def inv(where='', a=()):
    rows = q('SELECT i.*,l.name location FROM inventory i JOIN locations l ON l.id=i.location_id ' + where, a)
    for r in rows:
        r['days'] = round(r['qty'] / r['daily'], 1) if r['daily'] else 999
        r['status'] = 'CRITICAL' if r['qty'] < r['threshold'] * .5 or r['days'] < 3 else 'LOW' if r['qty'] < r['threshold'] else 'NORMAL'
    return rows
def wrisk(w): return min(100, round(w['rain'] * 1.1 + w['wind'] * .6 + (10 - w['visibility']) * 4 + w['terrain'] * 4 + {'GOOD': 0, 'FAIR': 8, 'POOR': 18, 'BLOCKED': 40}.get(w['road'], 0)))
def weather(lid=None):
    rows = q('SELECT w.*,l.name location FROM weather_data w JOIN locations l ON l.id=w.location_id' + (' WHERE w.location_id=?' if lid else ''), (lid,) if lid else ())
    for w in rows: w.update(risk=wrisk(w), level=lvl(wrisk(w)), delay_pct=round(wrisk(w) / 2), delay_h=round(wrisk(w) / 10, 1))
    return (rows[0] if rows else None) if lid else rows
def fc(i, days):
    h = [r['qty'] for r in q('SELECT qty FROM consumption_history WHERE inventory_id=? ORDER BY day', (i['id'],))]
    w = weather(i['location_id']); return forecast(h, i['qty'], days, w['risk'] if w else 0)
prio = lambda d: 'CRITICAL' if (d or 99) <= 3 else 'HIGH' if (d or 99) <= 7 else 'MEDIUM' if (d or 99) <= 15 else 'LOW'
def sup():
    out = []
    for i in inv():
        try: f = fc(i, 15)
        except ValueError: continue
        if f['recommended'] > 0 or f['shortage_day']:
            out.append(dict(id=i['id'], name=i['name'], location=i['location'], qty=i['qty'], daily=i['daily'], predicted=f['total'], shortage_day=f['shortage_day'],
                            recommended=f['recommended'], prob=f['prob'], priority=prio(f['shortage_day']), confidence=f['confidence']))
    return sorted(out, key=lambda s: (s['shortage_day'] or 99, -s['recommended']))
def alert(sev, cat, loc, msg, act):
    if not q('SELECT 1 FROM alerts WHERE msg=? AND resolved=0', (msg,)): ex('INSERT INTO alerts(ts,severity,category,location,msg,action) VALUES(?,?,?,?,?,?)', (now(), sev, cat, loc, msg, act))
def scan():
    for i in inv():
        if i['status'] != 'NORMAL': alert('CRITICAL' if i['status'] == 'CRITICAL' else 'WARNING', 'Inventory', i['location'], f"{i['name']} stock {i['qty']:.0f} is below threshold {i['threshold']:.0f} ({i['days']} days left)", 'Raise a resupply request')
    for s in sup():
        if s['shortage_day'] and s['shortage_day'] <= 15: alert('HIGH' if s['shortage_day'] <= 7 else 'WARNING', 'Demand', s['location'], f"Predicted 15-day demand {s['predicted']:.0f} exceeds stock {s['qty']:.0f} for {s['name']} (shortage ~day {s['shortage_day']})", f"Dispatch about {s['recommended']:.0f} units")
    for w in weather():
        if w['risk'] >= 50: alert('CRITICAL' if w['risk'] >= 75 else 'HIGH', 'Weather', w['location'], f"Weather risk {w['risk']}/100 ({w['cond']}, road {w['road']})", 'Re-evaluate routes; expect delay of ' + str(w['delay_h']) + ' h')
def hav(a, b):
    p = math.pi / 180; x = math.sin((b['lat'] - a['lat']) * p / 2) ** 2 + math.cos(a['lat'] * p) * math.cos(b['lat'] * p) * math.sin((b['lon'] - a['lon']) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))
CAP = {'Light Truck': 2000, 'Heavy Truck': 6000, 'Tanker': 8000, 'Mule Convoy': 400, 'Utility Vehicle': 800}
SPD = {'Light Truck': 1, 'Heavy Truck': .85, 'Tanker': .8, 'Mule Convoy': .4, 'Utility Vehicle': 1.1}
RV = [('Route A - Valley road', 1.30, 2, 45, 0, (.04, 0)), ('Route B - Ridge track', 1.12, 5, 32, 10, (-.04, .03)), ('Route C - Bypass highway', 1.50, 3, 55, 5, (.02, -.05))]
def routes(s, d, vt='Heavy Truck'):
    a, b = q('SELECT * FROM locations WHERE id=?', (s,), True), q('SELECT * FROM locations WHERE id=?', (d,), True)
    if not a or not b: raise HTTPException(400, 'Invalid location selected.')
    base = hav(a, b); ws = [x for x in (weather(s), weather(d)) if x]; wr = sum(x['risk'] for x in ws) / max(1, len(ws)); out = []
    for n, f, t, sp, extra, o in RV:
        km = round(base * f, 1); h = km / (sp * SPD.get(vt, 1)) * (1 + wr / 200); risk = round(min(100, t * 7 + wr * .6 + extra))
        out.append(dict(name=n, km=km, hours=round(h, 2), time=f"{int(h)}h {int(h % 1 * 60):02d}m", risk=risk, level=lvl(risk), terrain=t, weather_risk=round(wr),
                        path=[[a['lat'], a['lon']], [(a['lat'] + b['lat']) / 2 + o[0], (a['lon'] + b['lon']) / 2 + o[1]], [b['lat'], b['lon']]]))
    best = min(out, key=lambda r: r['hours'] * 8 + r['risk'] * 1.2)
    for r in out: r['recommended'] = r is best
    return out

# ---------- CRUD ----------
T = {'inventory': ('name', 'category', 'location_id', 'qty', 'threshold', 'daily', 'unit_value'), 'vehicles': ('code', 'type', 'capacity', 'location_id', 'dest_id', 'status', 'eta'), 'locations': ('name', 'type', 'lat', 'lon')}
NUM = {'qty', 'threshold', 'daily', 'unit_value', 'capacity', 'lat', 'lon', 'location_id', 'dest_id'}
def clean(t, b):
    if t not in T: raise HTTPException(404, 'Unknown resource.')
    d = {}
    for k in T[t]:
        if k not in b: continue
        v = b[k]
        if k in NUM:
            if v in ('', None): v = None
            else:
                try: v = float(v)
                except: raise HTTPException(400, f'{k.replace("_", " ").capitalize()} must be a number.')
                if k in ('qty', 'threshold', 'daily', 'capacity', 'unit_value') and v < 0: raise HTTPException(400, 'Quantity cannot be negative.' if k == 'qty' else f'{k.capitalize()} cannot be negative.')
                if k.endswith('_id'): v = int(v)
        d[k] = v
    return d
@app.post('/api/crud/{t}')
def create(t: str, b: dict, u=Depends(writer)):
    d = clean(t, b)
    for k in {'inventory': ('name', 'location_id'), 'vehicles': ('code', 'capacity'), 'locations': ('name', 'lat', 'lon')}[t]:
        if d.get(k) in (None, ''): raise HTTPException(400, f'{k.replace("_", " ").capitalize()} is required.')
    if t == 'inventory':
        d['updated'] = now()
        for k in ('qty', 'threshold', 'daily'): d[k] = d.get(k) or 0
    if t == 'vehicles': d['status'] = d.get('status') or 'AVAILABLE'
    i = ex(f"INSERT INTO {t}({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))
    if t == 'inventory': ex('UPDATE inventory SET code=? WHERE id=?', ('ITM-%04d' % i, i)); ex('INSERT INTO inventory_transactions(inventory_id,delta,reason,ts) VALUES(?,?,?,?)', (i, d['qty'], 'Initial stock', now())); scan()
    return {'id': i}
@app.put('/api/crud/{t}/{i}')
def update(t: str, i: int, b: dict, u=Depends(writer)):
    d = clean(t, b)
    if not d: raise HTTPException(400, 'Nothing to update.')
    if t == 'inventory':
        old = q('SELECT qty FROM inventory WHERE id=?', (i,), True)
        if 'qty' in d and old: ex('INSERT INTO inventory_transactions(inventory_id,delta,reason,ts) VALUES(?,?,?,?)', (i, d['qty'] - old['qty'], 'Manual update by ' + u['username'], now()))
        d['updated'] = now()
    ex(f"UPDATE {t} SET {','.join(k + '=?' for k in d)} WHERE id=?", list(d.values()) + [i])
    if t == 'inventory': scan()
    return {'ok': True}
@app.delete('/api/crud/{t}/{i}')
def delete(t: str, i: int, u=Depends(writer)):
    clean(t, {}); ex(f'DELETE FROM {t} WHERE id=?', (i,)); return {'ok': True}

# ---------- data endpoints ----------
@app.get('/api/inventory')
def g_inv(u=Depends(user)): return inv()
@app.get('/api/locations')
def g_loc(u=Depends(user)): return q('SELECT * FROM locations')
@app.get('/api/vehicles')
def g_veh(u=Depends(user)): return q('SELECT v.*,l.name location,d.name destination FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id LEFT JOIN locations d ON d.id=v.dest_id ORDER BY v.id')
@app.get('/api/weather')
def g_wx(u=Depends(user)): return weather()
@app.put('/api/weather/{lid}')
def p_wx(lid: int, b: dict, u=Depends(writer)):
    d = {}
    for k in ('temp', 'rain', 'visibility', 'wind', 'terrain'):
        try: d[k] = float(b[k])
        except: raise HTTPException(400, f'{k.capitalize()} must be a number.')
    if d['rain'] < 0 or d['wind'] < 0 or not 0 <= d['visibility'] <= 10 or not 1 <= d['terrain'] <= 5: raise HTTPException(400, 'Check values: rain/wind >= 0, visibility 0-10 km, terrain 1-5.')
    ex('UPDATE weather_data SET temp=?,rain=?,visibility=?,wind=?,terrain=?,cond=?,road=? WHERE location_id=?', (d['temp'], d['rain'], d['visibility'], d['wind'], int(d['terrain']), b.get('cond', 'Clear'), b.get('road', 'GOOD'), lid)); scan(); return weather(lid)
@app.get('/api/map')
def g_map(u=Depends(user)):
    I = inv(); out = []
    for l in q('SELECT * FROM locations'):
        it = [i for i in I if i['location_id'] == l['id']]; w = weather(l['id']); dm = min([i['days'] for i in it], default=0)
        st = 'CRITICAL' if any(i['status'] == 'CRITICAL' for i in it) else 'LOW' if any(i['status'] == 'LOW' for i in it) else 'NORMAL'
        out.append({**l, 'status': st, 'stock': sum(i['qty'] for i in it), 'daily': sum(i['daily'] for i in it), 'days': dm, 'weather_risk': w['risk'] if w else 0, 'risk': lvl(w['risk'] if w else 0), 'next_supply': str(today() + dt.timedelta(days=max(0, int(dm) - 3)))})
    return out
@app.get('/api/dashboard')
def g_dash(u=Depends(user)):
    I = inv(); vs = {r['status']: r['n'] for r in q('SELECT status,COUNT(*) n FROM vehicles GROUP BY status')}; Ls = q('SELECT type FROM locations'); P = sup(); W = weather()
    crit = [i for i in I if i['status'] == 'CRITICAL']; low = [i for i in I if i['status'] == 'LOW']; score = min(100, len(crit) * 9 + len(low) * 3 + max([w['risk'] for w in W], default=0) * .4)
    return dict(items=len(I), value=sum(i['qty'] * (i['unit_value'] or 0) for i in I), critical=len(crit), low=len(low), crit_pct=round(100 * len(crit) / max(1, len(I)), 1), locations=len(Ls), forward=sum(l['type'] == 'FORWARD' for l in Ls), warehouses=sum(l['type'] in ('DEPOT', 'HUB') for l in Ls),
                vehicles=sum(vs.values()), vs=vs, pending=len(P), prio={p: sum(s['priority'] == p for s in P) for p in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')}, risk=lvl(score), risk_score=round(score), top=P[:6])
@app.get('/api/forecast')
def g_fc(inventory_id: int, days: int = 7, u=Depends(user)):
    i = next(iter(inv('WHERE i.id=?', (inventory_id,))), None)
    if not i: raise HTTPException(404, 'Item not found.')
    H = q('SELECT day,qty FROM consumption_history WHERE inventory_id=? ORDER BY day', (inventory_id,))
    try: f = fc(i, days)
    except ValueError as e: raise HTTPException(400, str(e))
    last = dt.date.fromisoformat(H[-1]['day']); dates = [str(last + dt.timedelta(days=k + 1)) for k in range(days)]
    ex('INSERT INTO demand_forecasts(inventory_id,days,predicted,shortage_prob,recommended,confidence,ts) VALUES(?,?,?,?,?,?,?)', (inventory_id, days, f['total'], f['prob'], f['recommended'], f['confidence'], now()))
    return {**f, 'hist': H[-30:], 'dates': dates, 'item': i, 'shortage_date': str(today() + dt.timedelta(days=f['shortage_day'])) if f['shortage_day'] else None}
@app.get('/api/resupply')
def g_sup(u=Depends(user)): return sup()
@app.post('/api/resupply/plan')
def plan(b: dict, u=Depends(writer)):
    i = next(iter(inv('WHERE i.id=?', (b.get('inventory_id'),))), None)
    if not i: raise HTTPException(400, 'Select an item to plan resupply for.')
    try: f = fc(i, 15)
    except ValueError as e: raise HTTPException(400, str(e))
    qty = math.ceil(f['recommended']) or math.ceil(i['daily'] * 7); dst = q('SELECT * FROM locations WHERE id=?', (i['location_id'],), True)
    deps = q("SELECT * FROM locations WHERE type IN ('DEPOT','HUB') AND id!=?", (dst['id'],))
    if not deps: raise HTTPException(400, 'No supply depot available.')
    src = min(deps, key=lambda l: hav(l, dst)); r = next(x for x in routes(src['id'], dst['id']) if x['recommended'])
    vs = q("SELECT * FROM vehicles WHERE status='AVAILABLE' ORDER BY capacity DESC"); fit = [v for v in vs if v['capacity'] >= qty]; v = min(fit, key=lambda v: v['capacity']) if fit else (vs[0] if vs else None)
    trips = math.ceil(qty / v['capacity']) if v else 0; sd = f['shortage_day'] or 15; lead = math.ceil(r['hours'] / 24) + 1; disp = today() + dt.timedelta(days=max(0, sd - lead - 1))
    p = dict(item=i['name'], location=i['location'], qty=qty, priority=prio(f['shortage_day']), dispatch=str(disp), source=src['name'], route=r['name'], km=r['km'], eta_hours=r['hours'], eta=r['time'], route_risk=r['level'], vehicle=(v['code'] + ' ' + v['type']) if v else 'None available', trips=trips, shortage_day=f['shortage_day'], path=r['path'])
    ex('INSERT INTO resupply_requests(inventory_id,qty,priority,dispatch,source,route,eta_hours,vehicle,ts) VALUES(?,?,?,?,?,?,?,?,?)', (i['id'], qty, p['priority'], p['dispatch'], src['name'], r['name'], r['hours'], p['vehicle'], now())); return p
@app.post('/api/route')
def p_route(b: dict, u=Depends(user)):
    if not b.get('source_id'): raise HTTPException(400, 'Source is required.')
    if not b.get('destination_id'): raise HTTPException(400, 'Destination is required.')
    s, d = int(b['source_id']), int(b['destination_id']); vt = b.get('vehicle_type') or 'Heavy Truck'; qty = float(b.get('qty') or 0)
    if s == d: raise HTTPException(400, 'Source and destination must be different.')
    if qty < 0: raise HTTPException(400, 'Quantity cannot be negative.')
    rs = routes(s, d, vt); cap = CAP.get(vt, 1000); best = next(r for r in rs if r['recommended'])
    ex('INSERT INTO routes(src,dst,name,km,hours,risk,ts) VALUES(?,?,?,?,?,?,?)', (s, d, best['name'], best['km'], best['hours'], best['risk'], now()))
    if best['risk'] >= 50: alert('HIGH', 'Route', q('SELECT name FROM locations WHERE id=?', (d,), True)['name'], f"Every route to destination is {best['level']} risk (best: {best['name']}, {best['risk']}/100)", 'Delay dispatch or use escorted convoy')
    return dict(routes=rs, capacity=cap, trips=math.ceil(qty / cap) if qty else 1, warning='Vehicle capacity is insufficient for one trip: ' + str(math.ceil(qty / cap)) + ' trips needed.' if qty > cap else None)
@app.post('/api/vehicles/{vid}/assign')
def assign(vid: int, b: dict, u=Depends(writer)):
    v = q('SELECT * FROM vehicles WHERE id=?', (vid,), True)
    if not v: raise HTTPException(404, 'Vehicle not found.')
    if not b.get('dest_id'): raise HTTPException(400, 'Destination is required.')
    qty = float(b.get('qty') or 0); dest = int(b['dest_id']); dn = q('SELECT name FROM locations WHERE id=?', (dest,), True)['name']
    if qty > v['capacity']:
        alert('WARNING', 'Transport', dn, f"Vehicle {v['code']} capacity {v['capacity']:.0f} cannot carry {qty:.0f}", 'Pick a larger vehicle or split the load'); raise HTTPException(400, 'Vehicle capacity is insufficient.')
    if v['status'] != 'AVAILABLE': raise HTTPException(400, 'Only AVAILABLE vehicles can be assigned.')
    h = next(r for r in routes(v['location_id'], dest, v['type']) if r['recommended'])['hours'] if v['location_id'] and v['location_id'] != dest else 1
    ex("UPDATE vehicles SET status='ASSIGNED',dest_id=?,eta=? WHERE id=?", (dest, (dt.datetime.now() + dt.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M'), vid)); return {'ok': True}
@app.get('/api/alerts')
def g_al(severity: str = '', u=Depends(user)):
    return q("SELECT * FROM alerts WHERE resolved=0" + (" AND severity=?" if severity else '') + " ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,id DESC", (severity,) if severity else ())
@app.post('/api/alerts/{i}/{act}')
def p_al(i: int, act: str, u=Depends(writer)):
    ex('UPDATE alerts SET ' + ('is_read=1' if act == 'read' else 'resolved=1') + ' WHERE id=?', (i,)); return {'ok': True}
@app.post('/api/scan')
def p_scan(u=Depends(writer)): scan(); return {'ok': True}
@app.get('/api/analytics')
def g_ana(loc: int = 0, cat: str = '', u=Depends(user)):
    I = [i for i in inv() if (not loc or i['location_id'] == loc) and (not cat or i['category'] == cat)]; ids = [i['id'] for i in I]; days = {}
    for r in q('SELECT inventory_id,day,qty FROM consumption_history'):
        if r['inventory_id'] in ids: days[r['day']] = days.get(r['day'], 0) + r['qty']
    ds = sorted(days)[-30:]; cur = sum(i['qty'] for i in I); trend = [round(cur + sum(days[x] for x in days if x > d)) for d in ds]; fcast = [0] * 15
    for i in I:
        try: fcast = [a + b for a, b in zip(fcast, fc(i, 15)['pred'])]
        except ValueError: pass
    byloc = {}
    for i in I: byloc[i['location']] = byloc.get(i['location'], 0) + i['daily']
    bycat = {}
    for i in I:
        if i['status'] == 'CRITICAL': bycat[i['category']] = bycat.get(i['category'], 0) + 1
    return dict(days=ds, daily=[round(days[d]) for d in ds], trend=trend, forecast=[round(x) for x in fcast], byloc=byloc, critcat=bycat,
                vs={r['status']: r['n'] for r in q('SELECT status,COUNT(*) n FROM vehicles GROUP BY status')}, delays={w['location']: w['delay_h'] for w in weather()})
@app.get('/api/report/{kind}')
def report(kind: str, u=Depends(user)):
    if kind == 'inventory': return [{k: i[k] for k in ('code', 'name', 'category', 'location', 'qty', 'threshold', 'daily', 'days', 'status', 'updated')} for i in inv()]
    if kind == 'forecast': return [{k: s[k] for k in ('location', 'name', 'qty', 'daily', 'predicted', 'shortage_day', 'recommended', 'priority', 'confidence')} for s in sup()]
    if kind == 'transport': return [{k: v[k] for k in ('code', 'type', 'capacity', 'location', 'destination', 'status', 'eta')} for v in g_veh(u)]
    if kind == 'route':
        out = []; L = q('SELECT * FROM locations'); D = [l for l in L if l['type'] in ('DEPOT', 'HUB')]
        for l in L:
            if l['type'] == 'FORWARD': s = min(D, key=lambda x: hav(x, l)); r = next(x for x in routes(s['id'], l['id']) if x['recommended']); out.append({'destination': l['name'], 'from': s['name'], 'route': r['name'], 'km': r['km'], 'hours': r['hours'], 'risk': r['risk'], 'level': r['level']})
        return out
    if kind == 'daily':
        d = g_dash(u); return [{'metric': k, 'value': v} for k, v in (('Total items', d['items']), ('Stock value', round(d['value'])), ('Critical items', d['critical']), ('Low items', d['low']), ('Pending resupply', d['pending']), ('Vehicles available', d['vs'].get('AVAILABLE', 0)), ('Vehicles in transit', d['vs'].get('IN TRANSIT', 0)), ('Open alerts', len(g_al('', u))), ('Overall risk', d['risk']))]
    raise HTTPException(404, 'Unknown report.')

# ---------- demo data (fictional) ----------
LOC = [('Northern Sector HQ', 'SECTOR', 31.10, 77.20), ('Forward Post Alpha', 'FORWARD', 31.45, 77.55), ('Forward Post Bravo', 'FORWARD', 31.60, 77.30), ('Forward Post Charlie', 'FORWARD', 31.75, 77.70), ('Forward Post Delta', 'FORWARD', 31.35, 77.85),
       ('Supply Depot 01', 'DEPOT', 30.95, 77.05), ('Supply Depot 02', 'DEPOT', 31.05, 77.60), ('Logistics Hub', 'HUB', 30.85, 77.35), ('Forward Post Echo', 'FORWARD', 31.90, 77.45), ('Forward Post Foxtrot', 'FORWARD', 31.55, 77.05)]
ITEMS = [('Field Rations', 'Food', 120, 40), ('Diesel', 'Fuel', 300, 90), ('Medical Kits', 'Medical Supplies', 25, 900), ('Winter Clothing', 'Clothing', 40, 250), ('Vehicle Spares', 'Spare Parts', 18, 600), ('Tentage & Stores', 'General Supplies', 30, 150)]
def seed():
    c = db()
    for t in ['alerts', 'resupply_requests', 'demand_forecasts', 'routes', 'weather_data', 'vehicles', 'consumption_history', 'inventory_transactions', 'inventory', 'locations']: c.execute(f'DELETE FROM {t}')
    ids = []
    for n, t, la, lo in LOC: ids.append(c.execute('INSERT INTO locations(name,type,lat,lon,last_supply) VALUES(?,?,?,?,?)', (n, t, la, lo, str(today() - dt.timedelta(days=random.randint(2, 12))))).lastrowid)
    for k, lid in enumerate(ids):
        for j in range(3):
            n, cat, base, val = ITEMS[(k * 2 + j) % 6]; base *= random.uniform(.6, 1.4); tr = random.uniform(0, .012); daily = base * (1 + tr * 45 / 2)
            qty = round(daily * random.uniform(1.5, 22)); i = c.execute('INSERT INTO inventory(name,category,location_id,qty,threshold,daily,unit_value,updated) VALUES(?,?,?,?,?,?,?,?)', (n, cat, lid, qty, round(daily * 7), round(daily), val, now())).lastrowid
            c.execute('UPDATE inventory SET code=? WHERE id=?', ('ITM-%04d' % i, i))
            c.executemany('INSERT INTO consumption_history(inventory_id,day,qty) VALUES(?,?,?)', [(i, str(today() - dt.timedelta(days=45 - d)), round(base * (1 + tr * d) * (1 + .12 * math.sin(2 * math.pi * d / 7)) * random.uniform(.93, 1.07), 1)) for d in range(45)])
        rain = random.choice([0, 0, 4, 12, 35, 55]); c.execute('INSERT INTO weather_data VALUES(?,?,?,?,?,?,?,?)', (lid, random.randint(-6, 22), rain, round(max(1, 10 - rain / 8), 1), random.randint(5, 40), 'Heavy Rain' if rain >= 30 else 'Rain' if rain >= 8 else random.choice(['Clear', 'Fog']), 'POOR' if rain >= 30 else 'FAIR' if rain >= 8 else 'GOOD', random.randint(3, 5) if k in (1, 2, 3, 4, 8, 9) else random.randint(1, 3)))
    depots = [i for i, l in zip(ids, LOC) if l[1] in ('DEPOT', 'HUB')]; types = list(CAP)
    for n in range(20):
        t = types[n % 5]; st = random.choices(['AVAILABLE', 'IN TRANSIT', 'MAINTENANCE', 'ASSIGNED'], [8, 5, 3, 4])[0]; dest = random.choice(ids) if st in ('IN TRANSIT', 'ASSIGNED') else None
        c.execute('INSERT INTO vehicles(code,type,capacity,location_id,dest_id,status,eta) VALUES(?,?,?,?,?,?,?)', ('VEH-%03d' % (n + 1), t, CAP[t], random.choice(depots), dest, st, (dt.datetime.now() + dt.timedelta(hours=random.randint(2, 30))).strftime('%Y-%m-%d %H:%M') if dest else None))
    c.commit(); c.close(); scan()
@app.post('/api/demo/load')
def load(u=Depends(writer)): seed(); return {'ok': True}

def init():
    c = db(); c.executescript(open(os.path.join(R, 'database', 'schema.sql')).read())
    if not c.execute('SELECT 1 FROM users').fetchone():
        for n, p, r in [('admin', 'admin123', 'ADMIN'), ('officer', 'officer123', 'LOGISTICS OFFICER'), ('viewer', 'viewer123', 'VIEWER')]: c.execute('INSERT INTO users(username,pw,role) VALUES(?,?,?)', (n, hp(p), r))
    c.commit(); c.close()
    if not q('SELECT 1 FROM locations'): seed()
init()
app.mount('/', StaticFiles(directory=os.path.join(R, 'frontend'), html=True))
