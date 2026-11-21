import json,time,urllib.request,sys
def get(u):
    r=urllib.request.Request(u,headers={'User-Agent':'curl/8'})
    return json.load(urllib.request.urlopen(r,timeout=30))
items={31403:'Dark Entreaty',41109:'Royal Leather Sofa',46856:'Synthetic Dark Matter Beta',17836:'Timeworn Gazelleskin Map',51266:'Everkeep Kitchen Counter'}
ids=','.join(map(str,items))
eu=get(f'https://universalis.app/api/v2/Europe/{ids}?listings=15&entries=0')['items']
now=time.time()
for i,n in items.items():
    h=get(f'https://universalis.app/api/v2/Cerberus/{i}?listings=10&entries=0')
    hist=get(f'https://universalis.app/api/v2/history/Cerberus/{i}?entriesToReturn=200&entriesWithin=604800')['entries']
    sp=sorted(x['pricePerUnit'] for x in hist)
    med=sp[len(sp)//2] if sp else 0
    vel=sum(x['quantity'] for x in hist)/7
    low=h['listings'][0]['pricePerUnit'] if h['listings'] else 0
    nl=len([l for l in h['listings'] if l['pricePerUnit']<=low*1.05])
    ls=[l for l in eu[str(i)]['listings'] if l['worldName']!='Cerberus'][:4]
    buys=' · '.join(f"{l['worldName']} {l['pricePerUnit']}x{l['quantity']}" for l in ls)
    b=ls[0]['pricePerUnit'] if ls else 0
    sell=low-1
    print(f"{n} | alış {buys} | Cerb en düşük {low} ({nl} ilan ≤%5) | medyan7g {med} | {vel:.1f}/gün | kâr/adet {int(sell*0.95-b)}")
