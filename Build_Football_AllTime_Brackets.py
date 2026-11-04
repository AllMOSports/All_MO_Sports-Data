"""All-Time Class Brackets (football): seeds 1-16 = the 16 state champions (2010-2025) in each class,
ordered by OVR; seeds 17-32 = the 16 highest-OVR non-champion team-seasons in that class (at-large).
No per-program cap. Seasons whose official record was wiped by forfeits are excluded from at-large."""
import json,collections,csv,sys
OUT=sys.argv[1]; LA=26
R=json.load(open('output/mshsaa_historical_records/football/Football_Ratings_History_2010-2025.json'))
S=json.load(open('output/mshsaa_historical_records/football/Football_Stats_History_2010-2025.json'))
T=json.load(open('output/state_tournament_history.json'))['teams']
ALIAS={"St. Mary's St. Louis":"St. Mary's South Side","DeSmet":"De Smet Jesuit"}  # title-history name -> ratings name
stats={(s['year'],t['school']):t for s in S['seasons'] for t in s['teams']}
fin={}; champs=collections.defaultdict(list)
for sch,v in T.items():
  f=v.get('football')
  if not f: continue
  name=ALIAS.get(sch,sch)
  for key,lab in [('final_four_appearances','Final Four'),('third_place_finishes','Final Four'),('fourth_place_finishes','Final Four'),('runner_up_finishes','State Runner-up'),('state_championships','State Champion')]:
    for y in f[key]['years']:
      yr=int((y['year'] if isinstance(y,dict) else y)[:4]); fin[(yr,name)]=lab
      if key=='state_championships' and 2010<=yr<=2025: champs[(yr,int(y['class'].split()[-1]))].append(name)
rows=[];excluded=[]
for s in R['seasons']:
  for t in s['teams']:
    c=t.get('classification')
    if not c: continue
    st=stats.get((s['year'],t['school']),{})
    r=dict(year=s['year'],school=t['school'],cls=c,ovr=t['ovr_rating'],off=t['off_rating'],dfn=t['def_rating'],
      w=st.get('wins'),l=st.get('losses'),ppg=st.get('ppg'),oppg=st.get('oppg'),finish=fin.get((s['year'],t['school']),''))
    r['forfeit']= st.get('ppg')==0 and (st.get('losses') or 0)>0
    rows.append(r)
idx={(r['year'],r['school']):r for r in rows}
def order(n):
  o=[1]
  while len(o)<n:
    m=len(o)*2+1; o=[x for a in o for x in (a,m-a)]
  return o
def proj(a,b): return round(a['off']-b['dfn']+LA), round(b['off']-a['dfn']+LA)
out={'generated':'2026-11-03','method':'Seeds 1-16: the 16 state champions (2010-2025) in each class, ordered by OVR. Seeds 17-32: next 16 highest-OVR non-champion team-seasons in that class (at-large). No program cap. Projections: off - opp def + 26.','classes':{}}
flat=[]
for c in range(1,7):
  ch=[]
  for yr in range(2010,2026):
    names=champs[(yr,c)]; assert len(names)==1,(yr,c,names)
    r=idx[(yr,names[0])]; r['bid']='Champion'; ch.append(r)
  ch.sort(key=lambda r:-r['ovr'])
  al=[dict(r,bid='At-large') for r in sorted([r for r in rows if r['cls']==c and r['finish']!='State Champion' and not r['forfeit']],key=lambda r:-r['ovr'])[:16]]
  pick=ch+al
  for i,r in enumerate(pick): r['seed']=i+1
  bracket=[pick[s-1] for s in order(32)]
  rounds=[];cur=bracket
  while len(cur)>1:
    games=[];nxt=[]
    for i in range(0,len(cur),2):
      a,b=cur[i],cur[i+1];sa,sb=proj(a,b)
      win=a if (sa-sb,a['ovr'])>(sb-sa,b['ovr']) else b
      games.append(dict(a=a['seed'],b=b['seed'],sa=sa,sb=sb,winner=win['seed']));nxt.append(win)
    rounds.append(games);cur=nxt
  keep=['seed','bid','year','school','w','l','ovr','off','dfn','ppg','oppg','finish']
  out['classes'][c]=dict(teams=[{k:r[k] for k in keep} for r in pick],model_rounds=rounds,model_champion=cur[0]['seed'])
  for r in pick: flat.append({'class':c,**{k:r[k] for k in keep}})
  # report
  upsets=[g for g in rounds[0] if g['winner']!=min(g['a'],g['b'])]
  print(f"C{c}: champ seeds OVR {ch[0]['ovr']}..{ch[-1]['ovr']} | top at-large #17 {al[0]['year']} {al[0]['school']} {al[0]['ovr']} ({al[0]['finish'] or '-'}) | #32 {al[-1]['year']} {al[-1]['school']} {al[-1]['ovr']}")
  print("    #1",pick[0]['year'],pick[0]['school'],'| #16',pick[15]['year'],pick[15]['school'],pick[15]['ovr'],'| at-large rated above #16:',sum(1 for r in al if r['ovr']>ch[-1]['ovr']),'| model champ:',cur[0]['year'],cur[0]['school'],f"(#{cur[0]['seed']})",'| R32 upsets:',[(g['a'],g['b']) for g in upsets])
json.dump(out,open(f'{OUT}/football_alltime_brackets.json','w'),indent=1)
with open(f'{OUT}/football_alltime_brackets.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
