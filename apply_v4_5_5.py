from pathlib import Path
import re, sys

root=Path(sys.argv[1] if len(sys.argv)>1 else '.')
engine_path=root/'live_server'/'v4_engine.py'
app_path=root/'app.py'
engine=engine_path.read_text(encoding='utf-8')
app=app_path.read_text(encoding='utf-8')

if 'def session_summary(hits)' in engine:
    raise SystemExit('PATCH ABORTED: V4.5.5 engine already present')
if 'Multi-session Shadow Stability' in app:
    raise SystemExit('PATCH ABORTED: V4.5.5 UI already present')

# Backend: add per-market-date shadow session summaries.
needle="""        def dt(v):\n            try:\n                x=datetime.fromisoformat(str(v).replace('Z','+00:00'))\n                return x if x.tzinfo else x.replace(tzinfo=timezone.utc)\n            except Exception:return None\n\n        parsed=[]\n"""
insert="""        def dt(v):\n            try:\n                x=datetime.fromisoformat(str(v).replace('Z','+00:00'))\n                return x if x.tzinfo else x.replace(tzinfo=timezone.utc)\n            except Exception:return None\n\n        def session_day(r):\n            t=r.get('_ts') or dt(r.get('ts'))\n            if not t:return 'UNKNOWN'\n            try:\n                tz=ZoneInfo('America/New_York' if str(r.get('market') or market or '').upper()=='USA' else 'Asia/Seoul')\n                return t.astimezone(tz).date().isoformat()\n            except Exception:\n                return t.date().isoformat()\n\n        def session_summary(hits):\n            groups={}\n            for h in hits:\n                groups.setdefault(session_day(h),[]).append(h)\n            out=[]\n            for day,rows in sorted(groups.items()):\n                def avg(col):\n                    vals=[]\n                    for x in rows:\n                        if x.get(col) is None:continue\n                        v=_f(x.get(col),float('nan'))\n                        if not math.isnan(v):vals.append(v)\n                    return round(sum(vals)/len(vals),3) if vals else None\n                r60=[_f(x.get('ret_60m')) for x in rows if x.get('ret_60m') is not None]\n                out.append({'session_date':day,'episodes':len(rows),'complete_60':len(r60),\n                            'ret_15m':avg('ret_15m'),'ret_30m':avg('ret_30m'),'ret_60m':avg('ret_60m'),\n                            'hit_60_pct':round(sum(1 for x in r60 if x>0)/len(r60)*100,1) if r60 else None,\n                            'mfe_pct':avg('mfe_pct'),'mae_pct':avg('mae_pct')})\n            return out\n\n        parsed=[]\n"""
if needle not in engine: raise SystemExit('PATCH ABORTED: engine dt anchor missing')
engine=engine.replace(needle,insert,1)

needle="""                    meta={\n                        'power_min':pmin,'trigger_min':tmin,'delta_min':dmin,\n                        'core_pass_pct':round(core_pass/len(hits)*100,1) if hits else None\n                    }\n"""
insert="""                    stats=session_summary(hits)\n                    meta={\n                        'power_min':pmin,'trigger_min':tmin,'delta_min':dmin,\n                        'core_pass_pct':round(core_pass/len(hits)*100,1) if hits else None,\n                        'session_stats':stats,'session_count':len(stats)\n                    }\n"""
if needle not in engine: raise SystemExit('PATCH ABORTED: engine meta anchor missing')
engine=engine.replace(needle,insert,1)

# UI: sample-shrunk confidence + multi-session stability.
needle="""                    ranked['confidence_score']=(\n                        ranked['sample_score']+\n                        ranked['expectancy_score']+\n                        ranked['risk_score']+\n                        ranked['core_score']\n                    ).round(1)\n\n                    def _grade(r):\n"""
insert="""                    ranked['raw_confidence']=(\n                        ranked['sample_score']+ranked['expectancy_score']+ranked['risk_score']+ranked['core_score']\n                    )\n                    def _sample_factor(r):\n                        n=int(_n(r.get('complete_60')))\n                        return 0.25 if n<=0 else 0.50 if n<=2 else 0.75 if n<=4 else 1.00\n                    ranked['sample_factor']=ranked.apply(_sample_factor,axis=1)\n                    ranked['confidence_score']=(ranked['raw_confidence']*ranked['sample_factor']).round(1)\n\n                    def _stability(r):\n                        stats=r.get('session_stats')\n                        if not isinstance(stats,list) or not stats:return '표본부족'\n                        usable=[x for x in stats if x.get('ret_30m') is not None or x.get('ret_60m') is not None]\n                        if not usable:return '표본부족'\n                        days=len(usable); pos30=sum(1 for x in usable if x.get('ret_30m') is not None and _n(x.get('ret_30m'))>0)\n                        r60=[x for x in usable if x.get('ret_60m') is not None]; pos60=sum(1 for x in r60 if _n(x.get('ret_60m'))>0)\n                        if days>=3 and pos30/days>=0.67 and r60 and pos60/len(r60)>=0.60:return '반복 우수'\n                        if days==1 and pos30==1:return '1일 우수'\n                        if days>=2 and pos30/days<0.60:return '불안정'\n                        return '관찰'\n                    ranked['세션안정성']=ranked.apply(_stability,axis=1)\n\n                    def _grade(r):\n"""
if needle not in app: raise SystemExit('PATCH ABORTED: confidence anchor missing')
app=app.replace(needle,insert,1)

app=app.replace("""                        if n60<5:\n                            return '표본부족'\n                        if r15>0 and r30>0 and r60>0 and mae>-0.50 and core>=60:\n                            return '추천 후보'\n                        return '관찰'\n""","""                        stability=str(r.get('세션안정성') or '')\n                        if n60<5:return '표본부족'\n                        if r15>0 and r30>0 and r60>0 and mae>-0.50 and core>=60 and stability=='반복 우수':\n                            return '추천 후보'\n                        return '관찰'\n""",1)

app=app.replace("""                        rep['동일결과 조합']=' / '.join(gg['profile'].astype(str).tolist())\n                        rep['중복수']=len(gg)\n                        compact.append(rep)\n""","""                        rep['동일결과 조합']=' / '.join(gg['profile'].astype(str).tolist())\n                        rep['중복수']=len(gg)\n                        stats=rep.get('session_stats'); rep['세션수']=len(stats) if isinstance(stats,list) else int(_n(rep.get('session_count')))\n                        compact.append(rep)\n""",1)

app=app.replace("Confidence 점수는 확률이 아니라 조합 비교용 진단 점수입니다. 동일한 Episode/성과를 만든 임계값 조합은 한 행으로 묶습니다.",
                "Confidence는 확률이 아닌 조합 비교용 진단 점수이며 60분 완료 표본수에 따라 25/50/75/100%로 할인됩니다. 실제 기준 변경 후보는 여러 거래일에서 반복 우수가 확인되어야 합니다.",1)

app=app.replace("""                        ccols=[c for c in ['판정','대표 Shadow','동일결과 조합','중복수','Confidence',\n                                           'Episode','60분완료','15분%','30분%','60분%',\n                                           '60분상승%','MFE%','MAE%','Core통과%'] if c in cv.columns]\n""","""                        ccols=[c for c in ['판정','세션안정성','대표 Shadow','동일결과 조합','중복수','Confidence','세션수',\n                                           'Episode','60분완료','15분%','30분%','60분%',\n                                           '60분상승%','MFE%','MAE%','Core통과%'] if c in cv.columns]\n""",1)

app=app.replace("""                        for (p,tg),g in ranked.groupby(['power_min','trigger_min']):\n                            if len(g)<2:continue\n                            sigs=g['_sig'].nunique(dropna=False)\n                            if sigs==1:\n                                redundant.append(f\"P{int(p)}/T{int(tg)}: D0·D2·D4 동일\")\n""","""                        for (p,tg),g in ranked.groupby(['power_min','trigger_min']):\n                            if len(g)<2:continue\n                            if int(pd.to_numeric(g['episodes'],errors='coerce').fillna(0).max())<3:continue\n                            sigs=g['_sig'].nunique(dropna=False)\n                            if sigs==1:redundant.append(f\"P{int(p)}/T{int(tg)}: D0·D2·D4 동일 (Episode≥3)\")\n""",1)

needle="""                        if redundant:\n                            st.info('ΔPower 중복 관측 · '+' | '.join(redundant[:8])+\n                                    ' · 같은 결과가 여러 세션 반복되면 ΔPower 독립필터 필요성을 재검토합니다.')\n\n                    with st.expander('Shadow 해석 기준'):\n"""
insert="""                        if redundant:\n                            st.info('ΔPower 중복 관측 · '+' | '.join(redundant[:8])+' · Episode가 실제 존재하는 조합만 표시합니다.')\n\n                        st.markdown('##### 📆 Multi-session Shadow Stability')\n                        stability_rows=[]\n                        for _,rr in ranked.iterrows():\n                            stats=rr.get('session_stats')\n                            if not isinstance(stats,list):continue\n                            for ss in stats:\n                                stability_rows.append({'Shadow':rr.get('profile'),'거래일':ss.get('session_date'),'Episode':ss.get('episodes'),\n                                                       '60분완료':ss.get('complete_60'),'15분%':ss.get('ret_15m'),'30분%':ss.get('ret_30m'),\n                                                       '60분%':ss.get('ret_60m'),'MFE%':ss.get('mfe_pct'),'MAE%':ss.get('mae_pct')})\n                        if stability_rows:\n                            sdf=pd.DataFrame(stability_rows); focus=['P55/T3/D0','P55/T4/D0','P60/T4/D0','P60/T4/D2','P60/T4/D4']\n                            sf=sdf[sdf['Shadow'].isin(focus)].copy()\n                            st.dataframe((sf if len(sf) else sdf).sort_values(['Shadow','거래일']),use_container_width=True,hide_index=True)\n                            days=sdf['거래일'].nunique()\n                            if days<3:st.info(f'현재 Shadow 데이터 거래일은 {days}일입니다. 최소 3개 거래일 반복 전에는 실제 ENTRY 기준을 변경하지 않습니다.')\n                        else:\n                            st.caption('거래일별 Shadow 통계가 아직 없습니다.')\n\n                    with st.expander('Shadow 해석 기준'):\n"""
if needle not in app: raise SystemExit('PATCH ABORTED: stability insertion anchor missing')
app=app.replace(needle,insert,1)

app=app.replace("V4.5.4 · SHADOW CONFIDENCE RANKING + DEDUP","V4.5.5 · MULTI-SESSION SHADOW STABILITY + SAMPLE SHRINKAGE",1)

engine_path.write_text(engine,encoding='utf-8')
app_path.write_text(app,encoding='utf-8')
print('PATCHED:',engine_path)
print('PATCHED:',app_path)
