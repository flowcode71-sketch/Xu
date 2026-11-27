#!/usr/bin/env python3
"""Build frozen XAUUSD levels from GLD options and replace JSON atomically."""
import json, math, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import NormalDist
import pandas as pd
import yfinance as yf
import requests
ROOT=Path(__file__).resolve().parent; RATE=.04; EAT=timezone(timedelta(hours=3))
def retry(fn,attempts=3):
    err=None
    for i in range(attempts):
        try:return fn()
        except Exception as e:
            err=e
            if i+1<attempts:time.sleep(2**i)
    raise RuntimeError(f"Network request failed after {attempts} attempts: {err}")
def get_json(url):
    r=requests.get(url,timeout=20);r.raise_for_status();return r.json()
def spot_price():
    for url,label,key in [("https://fapi.binance.com/fapi/v1/ticker/price?symbol=XAUUSDT","Binance XAUUSDT futures","price"),("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT","Binance PAXGUSDT spot","price")]:
        try:
            x=retry(lambda:get_json(url));v=float(x[key])
            if v>0:return v,label
        except Exception:pass
    def future():
        d=yf.download("GC=F",period="5d",interval="1d",progress=False)
        if d.empty:raise RuntimeError("No gold futures price")
        return float(d["Close"].dropna().iloc[-1])
    return retry(future),"Yahoo Finance GC=F fallback"
def pdf(x):return math.exp(-.5*x*x)/math.sqrt(2*math.pi)
def bs(spot,strike,t,vol,typ):
    root=math.sqrt(t);d1=(math.log(spot/strike)+(RATE+vol*vol/2)*t)/(vol*root);d2=d1-vol*root
    delta=NormalDist().cdf(d1) if typ=="call" else NormalDist().cdf(d1)-1
    return delta,pdf(d1)/(spot*vol*root),spot*pdf(d1)*root/100
def option_price(s,k,t,v,typ):
    d1=(math.log(s/k)+(RATE+v*v/2)*t)/(v*math.sqrt(t));d2=d1-v*math.sqrt(t);n=NormalDist().cdf
    return s*n(d1)-k*math.exp(-RATE*t)*n(d2) if typ=="call" else k*math.exp(-RATE*t)*n(-d2)-s*n(-d1)
def solve_iv(p,s,k,t,typ):
    if p<=0:raise ValueError("Missing last traded price")
    lo,hi=.005,5
    for _ in range(90):
        m=(lo+hi)/2
        if option_price(s,k,t,m,typ)>p:hi=m
        else:lo=m
    return (lo+hi)/2
def atomic(path,data):
    tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(data,indent=2,allow_nan=False)+"\n",encoding="utf-8");os.replace(tmp,path)
def main():
    spot,source=retry(spot_price);ticker=yf.Ticker("GLD");hist=retry(lambda:ticker.history(period="5d",interval="1d"))
    if hist.empty:raise RuntimeError("Unable to retrieve GLD price")
    gld=float(hist["Close"].dropna().iloc[-1]);ratio=spot/gld;gld_spot=spot/ratio;now=datetime.now(EAT)
    exps=retry(lambda:ticker.options);exps=sorted(e for e in exps if 0<=(datetime.strptime(e,"%Y-%m-%d").date()-now.date()).days<=30)
    if not exps:raise RuntimeError("No GLD option expiries within 30 days")
    rows=[]
    for exp in exps:
        chain=retry(lambda e=exp:ticker.option_chain(e));expiry=datetime.strptime(exp,"%Y-%m-%d").replace(tzinfo=EAT);t=max((expiry-now).total_seconds()/(365.25*86400),1/365.25)
        for typ,frame in (("call",chain.calls),("put",chain.puts)):
            for _,r in frame.iterrows():
                oi=int(r.get("openInterest") or 0)
                if oi<=0:continue
                k=float(r.strike);iv=float(r.get("impliedVolatility") or 0);bid=float(r.get("bid") or 0);ask=float(r.get("ask") or 0)
                if not math.isfinite(iv) or not .01<=iv<=5:iv=0
                if bid>0 and ask>0 and ask>=bid:
                    if iv==0:
                        try:iv=solve_iv((bid+ask)/2,gld_spot,k,t,typ)
                        except Exception:continue
                else:
                    try:iv=solve_iv(float(r.get("lastPrice") or 0),gld_spot,k,t,typ)
                    except Exception:continue
                try:delta,gamma,vega=bs(gld_spot,k,t,iv,typ)
                except Exception:continue
                rows.append({"expiry":exp,"typ":typ,"strike":k,"price":k*ratio,"oi":oi,"iv":iv,"delta":delta,"gamma":gamma,"vega":vega,"years":t})
    if not rows:raise RuntimeError("No usable options rows")
    f=pd.DataFrame(rows);f["gex"]=f.gamma*f.oi*100*gld_spot**2*.01;f.loc[f.typ=="put","gex"]*=-1;f["vega_oi"]=f.vega*f.oi*100;front_exp=exps[0];next_exp=exps[1] if len(exps)>1 else None
    def aggregate(part):
        if part.empty:return pd.DataFrame(columns=["price","gex","call_oi","put_oi","vega_oi"])
        out=part.groupby("price").apply(lambda g:pd.Series({"gex":g.gex.sum(),"call_oi":g.loc[g.typ=="call","oi"].sum(),"put_oi":g.loc[g.typ=="put","oi"].sum(),"vega_oi":g.vega_oi.sum()})).reset_index()
        return out
    groups={"front":aggregate(f[f.expiry==front_exp]),"next":aggregate(f[f.expiry==next_exp]) if next_exp else aggregate(f.iloc[0:0]),"all":aggregate(f)}
    def wall(typ):
        o=f[(f.typ==typ)&((f.price>spot) if typ=="call" else (f.price<spot))]
        if o.empty:return {"price":spot,"open_interest":0,"gex":0}
        g=o.groupby("price").agg(open_interest=("oi","sum"),gex=("gex","sum"));p=g.gex.idxmax() if typ=="call" else g.gex.idxmin();return {"price":float(p),"open_interest":int(g.loc[p,"open_interest"]),"gex":float(g.loc[p,"gex"])}
    cw,pw=wall("call"),wall("put");tot=groups["all"].set_index("price").gex.to_dict();grid=[spot*(1+i/1000) for i in range(-50,51)];flip=spot;previous=None
    for p in grid:
        v=sum(g for k,g in tot.items() if abs(k-p)<spot*.002)
        if previous and previous[1]*v<0:flip=previous[0]+(p-previous[0])*abs(previous[1])/(abs(previous[1])+abs(v));break
        previous=(p,v)
    atm=f.iloc[(f.price-spot).abs().argsort()[:min(4,len(f))]];atm_iv=float(atm.iv.mean());move=spot*atm_iv*math.sqrt(1/252)
    def select(side):
        typ="call" if side=="resistance" else "put";g=f[f.typ==typ].groupby("price").agg(oi=("oi","sum"),gex=("gex","sum"),iv=("iv","mean"),delta=("delta","sum"),vega=("vega_oi","sum"));out=[]
        for p,row in g.iterrows():
            if abs(p/spot-1)>.04 or (side=="resistance" and p<=spot) or (side=="support" and p>=spot):continue
            oi=min(100,row.oi/max(1,g.oi.max())*100);gx=min(100,abs(row.gex)/max(1,g.gex.abs().max())*100);dv=min(100,(abs(row.delta)+row.vega/max(1,g.vega.max()))/max(1,g.delta.abs().max()+1)*100);ivscore=max(0,100-abs(row.iv-atm_iv)*500);wallprice=cw["price"] if side=="resistance" else pw["price"];prox=max(0,100-abs(p-wallprice)/spot*2500);score=.35*oi+.25*gx+.15*dv+.15*ivscore+.10*prox;out.append({"price":float(p),"score":round(float(score),1),"open_interest":int(row.oi),"gex":float(row.gex)})
        out.sort(key=lambda x:x["score"],reverse=True);picked=[]
        for x in out:
            if all(abs(x["price"]/z["price"]-1)>=.0015 for z in picked):picked.append(x)
            if len(picked)==3:break
        return sorted(picked,key=lambda x:abs(x["price"]-spot))
    prev_path=ROOT/"data_prev.json"
    try:old=json.loads(prev_path.read_text())
    except Exception:old={}
    date=now.date().isoformat();base=old.get("prev_net_all",{}) if old.get("date")==date else old.get("net_all",{});allg=groups["all"].copy();front=groups["front"].set_index("price");nxt=groups["next"].set_index("price");allg["front_gex"]=allg.price.map(front.gex).fillna(0);allg["next_gex"]=allg.price.map(nxt.gex).fillna(0);allg=allg[(allg.price-spot).abs()<=spot*.08]
    records=[]
    for _,r in allg.iterrows():
        key=f"{r.price:.4f}";records.append({"strike":float(r.price),"net_gex":float(r.gex),"front_gex":float(r.front_gex),"next_gex":float(r.next_gex),"call_oi":int(r.call_oi),"put_oi":int(r.put_oi),"vega_exposure":float(r.vega_oi),"change_1d":None if key not in base else float(r.gex)-float(base[key])})
    current={f"{r.price:.4f}":float(r.gex) for _,r in groups["all"].iterrows()};prevdata={"date":date,"net_all":current,"prev_net_all":base}
    def peak(g):
        if g.empty:return {"price":spot,"gex":0}
        r=g.iloc[g.gex.abs().argmax()];return {"price":float(r.price),"gex":float(r.gex)}
    def ivwall(g,above):
        z=g[g.price>spot] if above else g[g.price<spot]
        if z.empty:return {"price":spot,"vega_oi":0}
        r=z.loc[z.vega_oi.idxmax()];return {"price":float(r.price),"vega_oi":float(r.vega_oi)}
    def top(name,n):
        z=groups[name].copy();z["abs"]=z.gex.abs();z=z.nlargest(n,"abs");return [{"rank":i+1,"price":float(r.price),"net_gex":float(r.gex)} for i,(_,r) in enumerate(z.iterrows())]
    output={"sample":False,"analyzed_at":now.isoformat(timespec="seconds"),"analyzed_at_utc":now.astimezone(timezone.utc).isoformat(timespec="seconds"),"spot_source":source,"gld_ratio":ratio,"gld_spot":gld_spot,"xauusd_spot_at_analysis":spot,"atm_iv":atm_iv,"expected_move_1sd":move,"call_wall":cw,"put_wall":pw,"gamma_flip":{"price":flip},"resistances":select("resistance"),"supports":select("support"),"front_expiry":front_exp,"next_expiry":next_exp,"gex_strikes":records,"peak_gex":{"front":peak(groups["front"]),"all":peak(groups["all"])},"iv_wall_above":{"front":ivwall(groups["front"],True),"all":ivwall(groups["all"],True)},"iv_wall_below":{"front":ivwall(groups["front"],False),"all":ivwall(groups["all"],False)},"top_front":top("front",10),"top_next":top("next",5),"top_all":top("all",10)}
    atomic(ROOT/"levels.json",output);atomic(prev_path,prevdata)
if __name__=="__main__":
    try:main()
    except Exception as e:print(f"Analysis failed: {e}",file=sys.stderr);sys.exit(1)
