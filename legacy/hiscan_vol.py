import sys, time, statistics, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
from flip_scan import http, saddlebag, DC
now=time.time(); cands={}
for hq in (False, True):
    for kw in (dict(min_desired_avg_ppu=3000, min_profit_amount=3000), dict(min_desired_avg_ppu=3000, min_profit_amount=3000, hours_ago=72, min_sales=3),):
        try:
            for x in saddlebag('Cerberus', hq=hq, **kw):
                iid=int(x.get('item_id') or x['url'].rstrip('/').split('/')[-1]); cands[(iid,hq)]=x
        except Exception as e: print('sb err',e)
print('aday',len(cands), file=sys.stderr)
from concurrent.futures import ThreadPoolExecutor
def one(k):
    (iid,hq),x=k

    q='&hq=true' if hq else '&hq=false'
    try:
        home=http(f'https://universalis.app/api/v2/history/Cerberus/{iid}?entriesToReturn=80&entriesWithin={7*86400}{"&minSalePrice=0"}')
        lst=http(f'https://universalis.app/api/v2/Cerberus/{iid}?listings=20&entries=0{q}')
        eu=http(f'https://universalis.app/api/v2/Europe/{iid}?listings=8&entries=0{q}')
    except Exception: return None
    sales=[s for s in home.get('entries',[]) if bool(s['hq'])==hq]
    if len(sales)<4: return None
    med=statistics.median(s['pricePerUnit'] for s in sales)
    buyers=len({s.get('buyerName') for s in sales}); days=len({int((now-s['timestamp'])//86400) for s in sales})
    units=sum(s['quantity'] for s in sales); vel=units/7
    cheap=[l for l in eu.get('listings',[]) if l.get('worldName')!='Cerberus']
    if not cheap: return None
    buy=cheap[0]['pricePerUnit']; profit=med*0.95-buy
    if profit<6000 or buyers<8 or days<4 or vel<2.5: return None
    qty=sum(l['quantity'] for l in cheap if l['pricePerUnit']<=buy*1.15)
    rival=sum(l['quantity'] for l in lst.get('listings',[]) if l['pricePerUnit']<=med)
    low=min((l['pricePerUnit'] for l in lst.get('listings',[])),default=0)
    last=round((now-max(s['timestamp'] for s in sales))/3600,1)
    return ((profit*vel, x.get('real_name'), 'HQ' if hq else 'NQ', f"{cheap[0]['worldName']} ({DC.get(cheap[0]['worldName'],'?')})", buy, qty, int(med), low, units, round(vel,1), buyers, days, rival, last, int(profit)))
def safe(k):
    try: return one(k)
    except Exception: return None
rows=[r for r in ThreadPoolExecutor(6).map(safe, cands.items()) if r]
rows.sort(reverse=True)
print('eşya|q|nereden|alış|adet≤%15|Cerb medyan|Cerb en düşük ilan|satılan 7g|adet/gün|farklı alıcı|farklı gün|rakip≤medyan|son satış sa|kâr/adet')
for r in rows[:60]: print('|'.join(map(str,r[1:])))
