from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
text = path.read_text(encoding="utf-8")

start = text.find("with t[2]:\n    st.subheader('🧪 Validation Lab')")
end = text.find("\nwith t[3]:", start)

if start < 0 or end < 0:
    raise SystemExit(
        "PATCH ABORTED: Validation Lab block not found. app.py was not changed."
    )

new_block = r"""with t[2]:
    st.subheader('🧪 Validation / Daily Report')
    st.caption('추천 점수는 확률이 아닙니다. 저장된 실제 Tracker 표본의 +5/+15/+30/+60분, MFE/MAE를 이용해 가설을 검증합니다.')

    marks=api(f'/api/v4/validation/marks?market={m}&limit=3000').get('data') or []

    if marks:
        df=pd.DataFrame(marks).copy()

        # Normalize numeric fields safely.
        numeric_cols=[
            'anchor_price','power','power_delta','finder_rank',
            'setup_count','trigger_count','rvol','volume_ratio',
            'ret_5m','ret_15m','ret_30m','ret_60m','mfe_pct','mae_pct'
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col]=pd.to_numeric(df[col],errors='coerce')

        if 'ts' in df.columns:
            df['ts_dt']=pd.to_datetime(df['ts'],utc=True,errors='coerce')
            if m=='USA':
                try:
                    df['session_date']=df['ts_dt'].dt.tz_convert('America/New_York').dt.date.astype(str)
                except Exception:
                    df['session_date']=df['ts_dt'].dt.date.astype(str)
            else:
                try:
                    df['session_date']=df['ts_dt'].dt.tz_convert('Asia/Seoul').dt.date.astype(str)
                except Exception:
                    df['session_date']=df['ts_dt'].dt.date.astype(str)
        else:
            df['session_date']='-'

        dates=[x for x in sorted(df['session_date'].dropna().unique(),reverse=True) if x!='NaT']
        report_date=st.selectbox('리포트 거래일',dates,index=0 if dates else None,key=f'validation_date_{m}') if dates else None
        day=df[df['session_date']==report_date].copy() if report_date else df.copy()

        # Completed observations at each horizon.
        done60=day[day['ret_60m'].notna()].copy() if 'ret_60m' in day.columns else pd.DataFrame()
        done30=day[day['ret_30m'].notna()].copy() if 'ret_30m' in day.columns else pd.DataFrame()
        done15=day[day['ret_15m'].notna()].copy() if 'ret_15m' in day.columns else pd.DataFrame()
        done5=day[day['ret_5m'].notna()].copy() if 'ret_5m' in day.columns else pd.DataFrame()

        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric('당일 표본',len(day))
        c2.metric('60분 완료',len(done60))
        c3.metric('추적 종목',day['symbol'].nunique() if 'symbol' in day.columns else 0)
        if len(done60):
            c4.metric('60분 평균',f"{done60['ret_60m'].mean():+.2f}%")
            c5.metric('60분 상승 비율',f"{(done60['ret_60m']>0).mean()*100:.1f}%")
        elif len(done30):
            c4.metric('30분 평균',f"{done30['ret_30m'].mean():+.2f}%")
            c5.metric('30분 상승 비율',f"{(done30['ret_30m']>0).mean()*100:.1f}%")
        else:
            c4.metric('완료 평균','-')
            c5.metric('상승 비율','-')

        st.markdown('#### 📊 시간대별 성과')
        perf=[]
        for mins,col in [(5,'ret_5m'),(15,'ret_15m'),(30,'ret_30m'),(60,'ret_60m')]:
            if col not in day.columns: continue
            x=day[day[col].notna()]
            if len(x):
                perf.append({
                    '구간':f'+{mins}분',
                    '표본':len(x),
                    '평균%':round(x[col].mean(),3),
                    '중앙값%':round(x[col].median(),3),
                    '상승비율%':round((x[col]>0).mean()*100,1),
                    '평균 MFE%':round(x['mfe_pct'].mean(),3) if 'mfe_pct' in x.columns else None,
                    '평균 MAE%':round(x['mae_pct'].mean(),3) if 'mae_pct' in x.columns else None,
                })
        if perf:
            st.dataframe(pd.DataFrame(perf),use_container_width=True,hide_index=True)

        st.markdown('#### 🏆 종목별 실제 추적 성과')
        # Snapshot table is intentionally not treated as independent trades.
        # Aggregate per symbol so repeated minute snapshots do not look like many trades.
        agg={}
        for sym,g in day.groupby('symbol'):
            row={'종목':sym,'표본':len(g)}
            for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                row[label]=round(vals.mean(),3) if len(vals) else None
            row['MFE%']=round(g['mfe_pct'].max(),3) if 'mfe_pct' in g.columns and g['mfe_pct'].notna().any() else None
            row['MAE%']=round(g['mae_pct'].min(),3) if 'mae_pct' in g.columns and g['mae_pct'].notna().any() else None
            row['평균Power']=round(g['power'].mean(),1) if 'power' in g.columns and g['power'].notna().any() else None
            row['최대Power']=round(g['power'].max(),1) if 'power' in g.columns and g['power'].notna().any() else None
            row['평균Setup']=round(g['setup_count'].mean(),2) if 'setup_count' in g.columns and g['setup_count'].notna().any() else None
            row['평균Trigger']=round(g['trigger_count'].mean(),2) if 'trigger_count' in g.columns and g['trigger_count'].notna().any() else None
            agg[sym]=row

        symdf=pd.DataFrame(list(agg.values()))
        horizon='60분%' if '60분%' in symdf.columns and symdf['60분%'].notna().any() else \
                '30분%' if '30분%' in symdf.columns and symdf['30분%'].notna().any() else \
                '15분%' if '15분%' in symdf.columns and symdf['15분%'].notna().any() else '5분%'
        if len(symdf):
            symdf=symdf.sort_values(horizon,ascending=False,na_position='last')
            st.dataframe(symdf,use_container_width=True,hide_index=True)

            valid_rank=symdf[symdf[horizon].notna()]
            if len(valid_rank):
                best=valid_rank.iloc[0]
                worst=valid_rank.iloc[-1]
                a,b=st.columns(2)
                a.success(f"잘 잡은 종목 · {best['종목']} · {horizon} {best[horizon]:+.2f}% · MFE {f(best.get('MFE%')):+.2f}%")
                b.warning(f"부진 종목 · {worst['종목']} · {horizon} {worst[horizon]:+.2f}% · MAE {f(worst.get('MAE%')):+.2f}%")

        st.markdown('#### 🎯 엔진 상태별 성과')
        if 'state' in day.columns:
            state_rows=[]
            for state,g in day.groupby('state',dropna=False):
                r={'상태':stko(state),'표본':len(g)}
                for col,label in [('ret_5m','5분평균%'),('ret_15m','15분평균%'),('ret_30m','30분평균%'),('ret_60m','60분평균%')]:
                    vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                    r[label]=round(vals.mean(),3) if len(vals) else None
                r['평균Power']=round(g['power'].mean(),1) if 'power' in g.columns and g['power'].notna().any() else None
                r['평균Setup']=round(g['setup_count'].mean(),2) if 'setup_count' in g.columns and g['setup_count'].notna().any() else None
                r['평균Trigger']=round(g['trigger_count'].mean(),2) if 'trigger_count' in g.columns and g['trigger_count'].notna().any() else None
                state_rows.append(r)
            st.dataframe(pd.DataFrame(state_rows),use_container_width=True,hide_index=True)

        st.markdown('#### ⚡ Power 구간별 성과')
        if 'power' in day.columns:
            p=day[day['power'].notna()].copy()
            if len(p):
                p['Power구간']=pd.cut(
                    p['power'],
                    bins=[-1e9,0,20,40,60,1e9],
                    labels=['≤0','0~20','20~40','40~60','60+'],
                    right=False
                )
                power_rows=[]
                for bucket,g in p.groupby('Power구간',observed=True):
                    r={'Power구간':str(bucket),'표본':len(g)}
                    for col,label in [('ret_5m','5분%'),('ret_15m','15분%'),('ret_30m','30분%'),('ret_60m','60분%')]:
                        vals=g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                        r[label]=round(vals.mean(),3) if len(vals) else None
                    r['상승비율60%']=round((g['ret_60m'].dropna()>0).mean()*100,1) if 'ret_60m' in g.columns and g['ret_60m'].notna().any() else None
                    power_rows.append(r)
                st.dataframe(pd.DataFrame(power_rows),use_container_width=True,hide_index=True)

        st.markdown('#### 🧭 오늘 엔진 판정')
        notes=[]
        completed=done60 if len(done60) else done30 if len(done30) else done15 if len(done15) else done5
        retcol='ret_60m' if len(done60) else 'ret_30m' if len(done30) else 'ret_15m' if len(done15) else 'ret_5m'
        if len(completed):
            avg=completed[retcol].mean()
            hit=(completed[retcol]>0).mean()*100
            notes.append(f"현재 완료 표본 기준 {retcol.replace('ret_','').replace('m','분')} 평균 {avg:+.2f}%, 상승 비율 {hit:.1f}%")
            if avg>0.30 and hit>=55:
                notes.append('현재 표본에서는 후보 추적 방향이 우호적입니다.')
            elif avg<-0.20 or hit<45:
                notes.append('현재 표본에서는 후보 선정/진입 기준 재검토가 필요합니다.')
            else:
                notes.append('현재 표본은 우위가 아직 뚜렷하지 않습니다.')
        if 'mfe_pct' in day.columns and 'mae_pct' in day.columns and len(day):
            mfe=day['mfe_pct'].mean()
            mae=day['mae_pct'].mean()
            notes.append(f"평균 MFE {mfe:+.2f}% / 평균 MAE {mae:+.2f}%")
        for n in notes:
            st.write('• '+n)

        st.caption('주의: Validation 표본은 분당 Tracker 스냅샷입니다. 동일 종목의 여러 시점이 포함되므로 “거래 횟수”나 독립 표본으로 해석하면 안 됩니다. 종목별 표는 중복 스냅샷을 묶어 참고용으로 보여줍니다.')

        with st.expander('원본 Validation 표본'):
            show=[c for c in [
                'ts','symbol','state','anchor_price','power','power_delta','finder_rank',
                'setup_count','trigger_count','rvol','volume_ratio',
                'ret_5m','ret_15m','ret_30m','ret_60m',
                'mfe_pct','mae_pct','floor_mode'
            ] if c in day.columns]
            st.dataframe(day[show],use_container_width=True,hide_index=True)
    else:
        st.info('정상 데이터로 Tracker가 동작하면 분당 검증 표본이 자동 저장됩니다.')

    st.markdown(
        '다음 보정 원칙  \n'
        '1. 단일 하루 결과로 임계값을 바꾸지 않기  \n'
        '2. Power/Setup/Trigger별 +15/+30/+60분 기대값 비교  \n'
        '3. MFE/MAE로 Floor와 부분익절 폭 검증  \n'
        '4. 여러 세션에서 반복되는 패턴만 CURRENT 기준으로 승격'
    )
"""

text2 = text[:start] + new_block + text[end:]

old_footer = "st.caption('V4.4.7 UI · BROAD FINDER + LIGHT20 + FRESH EXPLAINABILITY · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')"
new_footer = "st.caption('V4.4.9 UI · FINDER + LIGHT20 + FRESH + VALIDATION DAILY REPORT · MAX 5 HEAVY TRACKING · MANUAL ORDER ONLY')"
if old_footer in text2:
    text2 = text2.replace(old_footer,new_footer,1)

path.write_text(text2,encoding="utf-8")
print(f"PATCHED: {path}")
