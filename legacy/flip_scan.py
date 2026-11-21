"""FFXIV flip taraması: Europe bölgesinden (Chaos + Light) al, Cerberus'ta sat.

Light'tan alım = Data Center Travel (MB'den alım serbest, satış yasak).

1) Saddlebag Exchange reselling taraması (POST api.saddlebagexchange.com/api/scan, auth yok).
2) Her aday için Universalis'ten Cerberus'taki son satışlar ve Chaos'taki ucuz ilanlar çekilir.
3) Kâr, Cerberus'un SON SATIŞ medyanına göre yeniden hesaplanır (Saddlebag en düşük ilana bakıyor,
   tek pahalı ilan kârı şişiriyor).

Kullanım:  python -X utf8 flip_scan.py [--home Cerberus] [--min-ppu 1500] [--min-profit 1000]
Not: legacy script, see legacy/README.md.
"""
import argparse, json, statistics, time, urllib.request

DC = {w: 'Chaos' for w in 'Cerberus Louisoix Moogle Omega Phantom Ragnarok Sagittarius Spriggan'.split()}
DC.update({w: 'Light' for w in 'Alpha Lich Odin Phoenix Raiden Shiva Twintania Zodiark'.split()})

UA = {'User-Agent': 'ffxiv-flip-scan/1.0'}

def http(url, body=None, tries=4):
    for t in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={**UA, 'Content-Type': 'application/json'})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception:
            if t == tries - 1:
                raise
            time.sleep(2 + 2 * t)

def saddlebag(home, **kw):
    body = dict(preferred_roi=40, min_profit_amount=1000, min_desired_avg_ppu=1500, min_stack_size=1,
                hours_ago=24, min_sales=5, hq=False, home_server=home, filters=[0],
                region_wide=True, include_vendor=False, show_out_stock=False)
    body.update(kw)
    r = http('https://api.saddlebagexchange.com/api/scan', body)
    return r.get('data', []) if isinstance(r, dict) else r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--home', default='Cerberus')
    ap.add_argument('--dc', default='Europe')  # Universalis bölge/DC adı
    ap.add_argument('--min-ppu', type=int, default=1500)
    ap.add_argument('--min-profit', type=int, default=1000)
    a = ap.parse_args()
    cands = {}
    for hq in (False, True):
        for x in saddlebag(a.home, hq=hq, min_desired_avg_ppu=a.min_ppu, min_profit_amount=a.min_profit):
            iid = int(x.get('item_id') or x['url'].rstrip('/').split('/')[-1])
            cands[(iid, hq)] = x
    now = time.time()
    rows = []
    for (iid, hq), x in cands.items():
        q = '&hq=true' if hq else '&hq=false'
        home = http(f'https://universalis.app/api/v2/{a.home}/{iid}?listings=50&entries=100{q}')
        dc = http(f'https://universalis.app/api/v2/{a.dc}/{iid}?listings=5&entries=0{q}')
        sales = [s for s in home.get('recentHistory', []) if now - s['timestamp'] < 7 * 86400]
        cheap = [l for l in dc.get('listings', []) if l.get('worldName') != a.home]
        if len(sales) < 3 or not cheap:
            continue
        med = statistics.median(s['pricePerUnit'] for s in sales)
        buy = cheap[0]['pricePerUnit']
        profit = med * 0.95 - buy
        if profit <= 0:
            continue
        last_h = round((now - max(s['timestamp'] for s in sales)) / 3600, 1)
        buy_qty = sum(l['quantity'] for l in cheap if l['pricePerUnit'] <= buy * 1.1)
        units7 = sum(s['quantity'] for s in sales)
        vel = home.get('hqSaleVelocity' if hq else 'nqSaleVelocity') or units7 / 7  # adet/gün
        # Rakip stok: Cerberus'ta medyan ve altında bekleyen adet (senin ilanın bunların arkasına girer)
        rival = sum(l['quantity'] for l in home.get('listings', []) if l['pricePerUnit'] <= med)
        wait_d = round((rival + buy_qty) / vel, 1) if vel else 99
        dc_name = DC.get(cheap[0]['worldName'], '?')
        sellable = min(buy_qty, vel * 3)  # 3 günde satılabilecek adet
        rows.append((profit * sellable, x.get('real_name'), 'HQ' if hq else 'NQ',
                     f"{cheap[0]['worldName']} ({dc_name})", buy, buy_qty, int(med), units7,
                     round(vel, 1), rival, wait_d, last_h, int(profit)))
        time.sleep(0.2)
    rows.sort(reverse=True)
    print('eşya | q | nereden | alış | o fiyata adet | Cerberus medyan (7g) | satılan adet (7g) | adet/gün | rakip stok ≤medyan | tahmini bekleme (gün) | son satış (sa) | kâr/adet (%5 vergi sonrası)')
    for r in rows[:30]:
        print(' | '.join(map(str, r[1:])))

if __name__ == '__main__':
    main()
