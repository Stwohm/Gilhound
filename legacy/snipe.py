import sys, time, statistics
sys.path.insert(0, __import__('os').path.dirname(__file__))
from flip_scan import http, saddlebag, DC
from concurrent.futures import ThreadPoolExecutor
now=time.time(); cands={}
for hq in (False,True):
    for kw in (dict(min_desired_avg_ppu=3000,min_profit_amount=3000),dict(min_desired_avg_ppu=3000,min_profit_amount=3000,hours_ago=72,min_sales=3),
               dict(preferred_roi=100,min_desired_avg_ppu=5000,min_profit_amount=4000,hours_ago=168,min_sales=3)):
        try:
            for x in saddlebag('Cerberus',hq=hq,**kw):
                iid=int(x.get('item_id') or x['url'].rstrip('/').split('/')[-1]); cands[(iid,hq)]=x
        except Exception as e: print('sb err',e,file=sys.stderr)
print('aday',len(cands),file=sys.stderr)
def one(k):
    (iid,hq),x=k; q='&hq=true' if hq else '&hq=false'
    home=http(f'https://universalis.app/api/v2/history/Cerberus/{iid}?entriesToReturn=80&entriesWithin={7*86400}')
    eu=http(f'https://universalis.app/api/v2/Europe/{iid}?listings=12&entries=0{q}')
    cl=http(f'https://universalis.app/api/v2/Cerberus/{iid}?listings=5&entries=0{q}')
    sales=[s for s in home.get('entries',[]) if bool(s['hq'])==hq]
    if len(sales)<3: return None
    med=statistics.median(s['pricePerUnit'] for s in sales)
    buyers=len({s.get('buyerName') for s in sales}); vel=sum(s['quantity'] for s in sales)/7
    ls=[l for l in eu.get('listings',[]) if l.get('worldName')!='Cerberus']
    if len(ls)<2: return None
    p1=ls[0]['pricePerUnit']; p2=ls[1]['pricePerUnit']
    low=min((l['pricePerUnit'] for l in cl.get('listings',[])),default=med)
    sell=min(med,low-1); profit=sell*0.95-p1
    age=(now-ls[0].get('lastReviewTime',0))/3600
    if profit<5000 or p1>0.5*p2 or buyers<3: return None
    return (profit, x.get('real_name'),'HQ' if hq else 'NQ',ls[0]['worldName'],DC.get(ls[0]['worldName'],'?'),p1,ls[0]['quantity'],p2,int(med),low,round(vel,1),buyers,round(age,1),int(profit))
def safe(k):
    try: return one(k)
    except Exception: return None
rows=[r for r in ThreadPoolExecutor(6).map(safe,cands.items()) if r]
rows.sort(reverse=True)
print('eşya|q|dünya|DC|snipe fiyat|adet|sonraki ilan|Cerb medyan|Cerb en düşük|adet/gün|alıcı|veri yaşı sa|kâr/adet')
for r in rows: print('|'.join(map(str,r[1:])))
