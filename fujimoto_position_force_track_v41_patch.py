from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== FUJIMOTO POSITION FORCE TRACK V4.1 =====
_fujimoto_held_cursor_v41=0

@app.get('/api/v5/fujimoto-tracker-v41/KOREA')
async def v5_fujimoto_tracker_v41_korea(batch_size:int=2,limit:int=10,max_pages:int=1,cache_ttl_sec:int=180):
    """Position-first Fujimoto tracker.

    Held KOREA daytrade positions are always present in output and are refreshed
    round-robin even when they fall outside the ranking/watch pool. Total new
    Kiwoom minute-chart work stays bounded: when held positions exist, the
    ordinary v4 lane is reduced to one fresh candidate and one held position is
    refreshed per cycle.
    """
    import time as _time
    global _fujimoto_held_cursor_v41

    held=_fujimoto_daytrade_positions()

    # Reserve one of the two fresh-fetch slots for a held position.
    base_bs=1 if held else max(1,min(int(batch_size),2))
    result=await v5_fujimoto_tracker_v4_korea(
        batch_size=base_bs,limit=max(10,int(limit)),max_pages=max_pages,cache_ttl_sec=cache_ttl_sec)

    held_syms=sorted(held.keys())
    held_refreshed=0
    held_refresh_symbol=None
    if held_syms:
        idx=int(_fujimoto_held_cursor_v41)%len(held_syms)
        sym=held_syms[idx]
        p=held[sym]
        held_refresh_symbol=sym
        prev=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        try:
            d=await asyncio.to_thread(korea.canonical_minute_bars,sym,1)
            eng=evaluate_fujimoto_engine_v1(
                d.get('bars') or [],
                previous_state=prev.get('state') or 'HOLD',
                position_open=True)
            now_ts=_time.time()
            _fujimoto_tracker_state[sym]={
                'state':eng.get('engine_state') or 'HOLD',
                'position_open':True,
                'signal':eng.get('signal') or 'NONE',
                'score':eng.get('score'),
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'engine':eng,
                'position_source':'V5_PORTFOLIO_DAYTRADE',
                'quantity':float(p.get('quantity') or 0),
                'avg_price':float(p.get('avg_price') or 0),
            }
            sc=dict(eng); sc['_cached_at']=now_ts; _fujimoto_overlay_cache[sym]=sc
            held_refreshed=1
        except Exception as e:
            cur=_fujimoto_tracker_state.get(sym) or {}
            cur.update({
                'state':cur.get('state') or 'HOLD','position_open':True,
                'signal':cur.get('signal') or 'NONE','error':str(e)[:180],
                'updated_at':datetime.now(timezone.utc).isoformat(),
                'position_source':'V5_PORTFOLIO_DAYTRADE',
                'quantity':float(p.get('quantity') or 0),
                'avg_price':float(p.get('avg_price') or 0),
            })
            _fujimoto_tracker_state[sym]=cur
        _fujimoto_held_cursor_v41=(idx+1)%len(held_syms)

    # Force every held position into the response, even when absent from watch pool.
    rows=list(result.get('rows') or [])
    by={str(r.get('symbol') or '').upper():r for r in rows}
    for sym,p in held.items():
        st=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        eng=st.get('engine') or _fujimoto_overlay_cache.get(sym) or {}
        score=st.get('score') if st.get('score') is not None else eng.get('score')
        row=by.get(sym)
        if row is None:
            row={
                'symbol':sym,'name':p.get('name') or sym,
                'value_rank':9999,'volume_rank':9999,
                'rank_sources':['HELD_POSITION'],
                'finder_rank_score':None,
                'trade_priority':None,
            }
            rows.append(row); by[sym]=row
        row.update({
            'position_open':True,
            'position_source':'V5_PORTFOLIO_DAYTRADE',
            'quantity':float(p.get('quantity') or 0),
            'avg_price':float(p.get('avg_price') or 0),
            'fujimoto_score':score,
            'engine_state':st.get('state') or eng.get('engine_state') or 'HOLD',
            'signal':st.get('signal') or eng.get('signal') or 'NONE',
            'transition':eng.get('transition'),
            'actionable':bool(eng.get('actionable')),
            'entry_reasons':eng.get('entry_reasons') or [],
            'exit_reasons':eng.get('exit_reasons') or [],
            'rsi':eng.get('rsi'),'macd':eng.get('macd'),
            'macd_signal':eng.get('macd_signal'),'macd_hist':eng.get('macd_hist'),
            'latest_bar_time':eng.get('latest_bar_time'),
            'held_force_track':True,
        })

    # Held positions first for management visibility; non-held retain priority order.
    rows.sort(key=lambda r:(
        1 if r.get('position_open') else 0,
        1 if r.get('trade_priority') is not None else 0,
        float(r.get('trade_priority') or -1e9)
    ),reverse=True)

    result['rows']=rows[:max(int(limit),len(held))]
    result['count']=len(result['rows'])
    result['version']='FUJIMOTO_TRACKER_V41_FORCE_HELD'
    result['daytrade_position_count']=len(held)
    result['daytrade_positions']=list(held.values())
    result['held_force_track_count']=len(held)
    result['held_refresh_symbol']=held_refresh_symbol
    result['held_fresh_fetch_count']=held_refreshed
    result['fresh_fetch_count']=int(result.get('fresh_fetch_count') or 0)+held_refreshed
    result['max_fresh_fetch_per_call']=2
    result['position_force_track']=True
    return result
'''

def main():
    a=API.read_text()
    if 'FUJIMOTO POSITION FORCE TRACK V4.1' not in a:
        a += PATCH

    # Make the existing v4 auto loop consume the position-first v4.1 tracker.
    old="r=await v5_fujimoto_tracker_v4_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))"
    new="r=await v5_fujimoto_tracker_v41_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))"
    if old in a:
        a=a.replace(old,new,1)
    elif new not in a:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: auto v4 tracker call')

    API.write_text(a)
    print('FUJIMOTO_POSITION_FORCE_TRACK_V41_OK')

if __name__=='__main__': main()
