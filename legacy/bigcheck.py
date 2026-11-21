import json,urllib.request,urllib.parse,time,statistics,sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
def get(u): return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'curl/8'}),timeout=60))
now=time.time()
names=sys.argv[1:]
def one(n):
    hq=n.endswith(' HQ'); nm=n[:-3] if hq else n
    q=urllib.parse.quote(f'Name="{nm}"')
    iid=get(f'https://v2.xivapi.com/api/search?sheets=Item&fields=Name&query={q}')['results'][0]['row_id']
    h=get(f'https://universalis.app/api/v2/history/Cerberus/{iid}?entriesToReturn=200&entriesWithin={14*86400}')
    e=[x for x in h['entries'] if bool(x['hq'])==hq]
    w7=[x for x in e if now-x['timestamp']<7*86400]
    if not w7: return f'{n}: 7 günde satış yok'
    cnt=Counter(x['buyerName'] for x in w7); top=cnt.most_common(1)[0][1]
    med=statistics.median(x['pricePerUnit'] for x in w7)
    last=(now-max(x['timestamp'] for x in e))/3600
    qh='&hq=true' if hq else '&hq=false'
    eu=get(f'https://universalis.app/api/v2/Europe/{iid}?listings=6&entries=0{qh}')
    ls=[l for l in eu['listings'] if l['worldName']!='Cerberus'][:3]
    cl=get(f'https://universalis.app/api/v2/Cerberus/{iid}?listings=6&entries=0{qh}')['listings']
    low=min((l['pricePerUnit'] for l in cl),default=None)
    buy=ls[0]['pricePerUnit'] if ls else None
    sell=min(med,(low or med)-1)
    prof=int(sell*0.95-buy) if buy else None
    times=sorted(time.strftime('%m-%d',time.localtime(x['timestamp'])) for x in w7)
    return f"{n} | alış {[(l['worldName'],l['pricePerUnit'],round((now-l['lastReviewTime'])/3600,1)) for l in ls]} | Cerb ilan {sorted(l['pricePerUnit'] for l in cl)[:4]} | 7g {len(w7)} satış, {len(cnt)} alıcı, en büyük alıcı {top} | medyan {int(med)} | son satış {last:.0f} sa | günler {sorted(set(times))} | kâr {prof}"
for r in ThreadPoolExecutor(5).map(lambda n: (lambda: one(n))() if True else None, names):
    print(r)
