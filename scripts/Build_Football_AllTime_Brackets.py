"""All-Time Class Brackets (football): seeds 1-16 = the 16 state champions (2010-2025) in each class,
ordered by OVR; seeds 17-32 = the 16 highest-OVR non-champion team-seasons in that class (at-large).
No per-program cap. Seasons whose official record was wiped by forfeits are excluded from at-large."""
import json,collections,csv,sys,statistics
OUT=sys.argv[1]; LA=26
R=json.load(open('output/mshsaa_historical_records/football/Football_Ratings_History_2010-2025.json'))
S=json.load(open('output/mshsaa_historical_records/football/Football_Stats_History_2010-2025.json'))
T=json.load(open('output/state_tournament_history.json'))['teams']
SCH=json.load(open('output/schools.json'))['schools']
SLUG=dict(json.load(open('school_aliases.json')))
for _slug,_v in SCH.items():
  for _k in ('mshsaa_name','name'):
    if _v.get(_k): SLUG[_v[_k]]=_slug
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
    r['state_rank']=t.get('ovr_rank'); r['alltime_class_rank']=t.get('ovr_rank_class_alltime')
    r['off_alltime_class_rank']=t.get('off_rank_class_alltime'); r['def_alltime_class_rank']=t.get('def_rank_class_alltime')
    rows.append(r)
idx={(r['year'],r['school']):r for r in rows}
 
# ---- Matchup data -------------------------------------------------------
YEARS=range(2010,2026)
SCHED={y:json.load(open(f'output/football_schedule_{y}.json'))['teams'] for y in YEARS}
Q=json.load(open('output/mshsaa_historical_records/football/Football_Quad_Records_History_2010-2025.json'))
QUAD={(t['year'],t['school']):[t.get('q1',''),t.get('q2',''),t.get('q3',''),t.get('q4','')] for s in Q['seasons'] for t in s['teams']}
# Strength of schedule = average end-of-season OVR of opponents played (out-of-state opponents have no rating and are skipped)
for r in rows:
  opp=[idx[(r['year'],g['opponent'])]['ovr'] for g in SCHED[r['year']].get(r['school'],[]) if (r['year'],g['opponent']) in idx]
  r['sos']=round(sum(opp)/len(opp),2) if opp else None
by_season_class=collections.defaultdict(list)
for r in rows: by_season_class[(r['year'],r['cls'])].append(r)
for grp in by_season_class.values():
  n=len(grp)
  for i,r in enumerate(sorted(grp,key=lambda r:-r['ovr'])): r['class_rank']=i+1; r['class_size']=n
  ranked=sorted([r for r in grp if r['sos'] is not None],key=lambda r:-r['sos'])
  for i,r in enumerate(ranked): r['sos_rank']=i+1
def season_games(r):
  out=[]
  for g in SCHED[r['year']].get(r['school'],[]):
    if g.get('team_score') is None: continue
    o=idx.get((r['year'],g['opponent']))
    out.append(dict(opp=g['opponent'],opp_ovr=o['ovr'] if o else None,res=g['result'],score=f"{g['team_score']}-{g['opp_score']}",date=g['date']))
  return out
# Model error spread: actual margin minus projected margin, all 2010-2025 games
_resid=[(g['team_score']-g['opp_score'])-(g['predicted_team_score']-g['predicted_opp_score'])
        for y in YEARS for gs in SCHED[y].values() for g in gs
        if g.get('predicted_team_score') is not None if g.get('team_score') is not None]
MODEL_SD=round(statistics.pstdev(_resid),1)
def program_h2h(names):
  # Every game between two programs in the field, 2010-2025, counted once
  names=set(names); seen=set(); h=collections.defaultdict(list)
  for y in YEARS:
    for team,gs in SCHED[y].items():
      if team not in names: continue
      for g in gs:
        o=g['opponent']
        if o not in names or o==team or g.get('team_score') is None: continue
        a,b=sorted([team,o]); key=(g['date'],a,b)
        if key in seen: continue
        seen.add(key)
        sa,sb=(g['team_score'],g['opp_score']) if team==a else (g['opp_score'],g['team_score'])
        h[a+'|'+b].append([y,sa,sb])
  return {k:sorted(v) for k,v in h.items()}
def order(n):
  o=[1]
  while len(o)<n:
    m=len(o)*2+1; o=[x for a in o for x in (a,m-a)]
  return o
def proj(a,b): return round(a['off']-b['dfn']+LA), round(b['off']-a['dfn']+LA)
out={'generated':'2026-11-03','model_sd':MODEL_SD,'league_average':LA,'method':'Seeds 1-16: the 16 state champions (2010-2025) in each class, ordered by OVR. Seeds 17-32: next 16 highest-OVR non-champion team-seasons in that class (at-large). No program cap. Projections: off - opp def + 26.','classes':{}}
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
  for r in pick:
    r['slug']=SLUG.get(r['school'],'')
    r['quad']=QUAD.get((r['year'],r['school']))
    gs=season_games(r)
    r['best_wins']=[[g['opp'],g['score'],g['opp_ovr']] for g in sorted([g for g in gs if g['res']=='W' and g['opp_ovr'] is not None],key=lambda g:-g['opp_ovr'])[:3]]
    r['losses']=[[g['opp'],g['score'],g['opp_ovr']] for g in gs if g['res']=='L']
    r['games']=[[g['date'],g['opp'],g['res'],g['score']] for g in gs]
  keep=['seed','bid','year','school','slug','w','l','ovr','off','dfn','ppg','oppg','finish','sos','sos_rank','class_rank','class_size','state_rank','alltime_class_rank','off_alltime_class_rank','def_alltime_class_rank','quad','best_wins','losses','games']
  out['classes'][c]=dict(teams=[{k:r.get(k) for k in keep} for r in pick],model_rounds=rounds,model_champion=cur[0]['seed'],alltime_size=sum(1 for r in rows if r['cls']==c),h2h=program_h2h({r['school'] for r in pick}))
  flat_keep=[k for k in keep if k not in ('quad','best_wins','losses','games')]
  for r in pick: flat.append({'class':c,**{k:r.get(k) for k in flat_keep}})
  # report
  upsets=[g for g in rounds[0] if g['winner']!=min(g['a'],g['b'])]
  print(f"C{c}: champ seeds OVR {ch[0]['ovr']}..{ch[-1]['ovr']} | top at-large #17 {al[0]['year']} {al[0]['school']} {al[0]['ovr']} ({al[0]['finish'] or '-'}) | #32 {al[-1]['year']} {al[-1]['school']} {al[-1]['ovr']}")
  print("    #1",pick[0]['year'],pick[0]['school'],'| #16',pick[15]['year'],pick[15]['school'],pick[15]['ovr'],'| at-large rated above #16:',sum(1 for r in al if r['ovr']>ch[-1]['ovr']),'| model champ:',cur[0]['year'],cur[0]['school'],f"(#{cur[0]['seed']})",'| R32 upsets:',[(g['a'],g['b']) for g in upsets])
print('model sd',MODEL_SD)
json.dump(out,open(f'{OUT}/football_alltime_brackets.json','w'),separators=(',',':'))
with open(f'{OUT}/football_alltime_brackets.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
