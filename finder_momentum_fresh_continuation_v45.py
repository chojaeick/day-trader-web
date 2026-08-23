from pathlib import Path

KIWOOM=Path('live_server/kiwoom.py')
KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

FORMULA="52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100) AND (FRESH:MACD0_CROSS_5 OR CONTINUATION:MACD_GT_0_AND_GT_SIGNAL)"

def patch_usa():
    s=KIWOOM.read_text()

    old="""        macd=[a-b for a,b in zip(ema12,ema26)]\n        cross_offsets=[]\n"""
    new="""        macd=[a-b for a,b in zip(ema12,ema26)]\n        macd_signal=self._v39_ema(macd,9)\n        macd_above_signal=bool(macd and macd_signal and macd[-1] > macd_signal[-1])\n        cross_offsets=[]\n"""
    if old in s and 'macd_signal=self._v39_ema(macd,9)' not in s:
        s=s.replace(old,new,1)

    old="""            'macd':round(macd[-1],6),'macd_zero_cross_bars_ago':recent_cross,\n            'macd_cross_5':macd_cross_5,'near_52w_high':near_52w,\n            'momentum_match':bool(macd_cross_5 and near_52w),\n            '_cached_at':now,\n"""
    new="""            'macd':round(macd[-1],6),'macd_signal':round(macd_signal[-1],6),\n            'macd_above_signal':macd_above_signal,'macd_zero_cross_bars_ago':recent_cross,\n            'macd_cross_5':macd_cross_5,'near_52w_high':near_52w,\n            'momentum_fresh':bool(near_52w and macd_cross_5),\n            'momentum_continuation':bool(near_52w and macd[-1] > 0 and macd_above_signal),\n            'momentum_type':('FRESH' if (near_52w and macd_cross_5) else ('CONTINUATION' if (near_52w and macd[-1] > 0 and macd_above_signal) else None)),\n            'momentum_match':bool(near_52w and (macd_cross_5 or (macd[-1] > 0 and macd_above_signal))),\n            '_cached_at':now,\n"""
    if old in s:
        s=s.replace(old,new,1)
    elif "'momentum_fresh':" not in s:
        raise SystemExit('USA_FEATURE_TARGET_NOT_FOUND')

    # V43 match row must report the actual lane instead of hard-coding fresh=True.
    old="""                'momentum_match':True,'macd_cross_5':True,\n                'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),\n                'high_52w':feat.get('high_52w'),'high_52w_gap_pct':feat.get('high_52w_gap_pct'),\n                'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)',\n                'momentum_eval_mode':'ROTATING_BATCH_V43'\n"""
    new="""                'momentum_match':True,'momentum_type':feat.get('momentum_type'),\n                'momentum_fresh':bool(feat.get('momentum_fresh')),\n                'momentum_continuation':bool(feat.get('momentum_continuation')),\n                'macd_cross_5':bool(feat.get('macd_cross_5')),\n                'macd':feat.get('macd'),'macd_signal':feat.get('macd_signal'),\n                'macd_above_signal':bool(feat.get('macd_above_signal')),\n                'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),\n                'high_52w':feat.get('high_52w'),'high_52w_gap_pct':feat.get('high_52w_gap_pct'),\n                'momentum_formula':'"""+FORMULA+"""',\n                'momentum_eval_mode':'ROTATING_BATCH_V43'\n"""
    if old in s:
        s=s.replace(old,new,1)
    elif "'momentum_type':feat.get('momentum_type')" not in s:
        raise SystemExit('USA_MATCH_TARGET_NOT_FOUND')

    s=s.replace("'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'", "'momentum_formula':'"+FORMULA+"'")
    KIWOOM.write_text(s)


def patch_korea():
    s=KOREA.read_text()
    if 'V39 MOMENTUM DISCOVERY' not in s:
        return
    old="""        macd=[a-b for a,b in zip(ema12,ema26)]\n        offsets=[len(macd)-1-i for i in range(1,len(macd)) if macd[i-1] <= 0 < macd[i]]\n"""
    new="""        macd=[a-b for a,b in zip(ema12,ema26)]\n        macd_signal=self._v39_ema(macd,9)\n        macd_above_signal=bool(macd and macd_signal and macd[-1] > macd_signal[-1])\n        offsets=[len(macd)-1-i for i in range(1,len(macd)) if macd[i-1] <= 0 < macd[i]]\n"""
    if old in s and 'macd_signal=self._v39_ema(macd,9)' not in s:
        s=s.replace(old,new,1)

    old="""             'macd':macd[-1],'macd_zero_cross_bars_ago':recent,'macd_cross_5':macd_cross_5,\n             'near_52w_high':near_52w,'momentum_match':bool(macd_cross_5 and near_52w),'_cached_at':now}\n"""
    new="""             'macd':macd[-1],'macd_signal':macd_signal[-1],'macd_above_signal':macd_above_signal,\n             'macd_zero_cross_bars_ago':recent,'macd_cross_5':macd_cross_5,'near_52w_high':near_52w,\n             'momentum_fresh':bool(near_52w and macd_cross_5),\n             'momentum_continuation':bool(near_52w and macd[-1] > 0 and macd_above_signal),\n             'momentum_type':('FRESH' if (near_52w and macd_cross_5) else ('CONTINUATION' if (near_52w and macd[-1] > 0 and macd_above_signal) else None)),\n             'momentum_match':bool(near_52w and (macd_cross_5 or (macd[-1] > 0 and macd_above_signal))),'_cached_at':now}\n"""
    if old in s:
        s=s.replace(old,new,1)

    # Include new fields when Korea discovery enriches ranking rows.
    old="""row.update({k:v for k,v in feat.items() if k in ('momentum_match','macd_cross_5','macd_zero_cross_bars_ago','high_52w','high_52w_gap_pct','near_52w_high')})"""
    new="""row.update({k:v for k,v in feat.items() if k in ('momentum_match','momentum_type','momentum_fresh','momentum_continuation','macd','macd_signal','macd_above_signal','macd_cross_5','macd_zero_cross_bars_ago','high_52w','high_52w_gap_pct','near_52w_high')})"""
    if old in s:
        s=s.replace(old,new,1)
    s=s.replace("'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'", "'momentum_formula':'"+FORMULA+"'")
    KOREA.write_text(s)


def patch_api_diag():
    s=API.read_text()
    # Extend existing v44 cache diagnostic without adding another endpoint.
    old="""            'macd':feat.get('macd'),\n            'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),\n            'macd_cross_5':bool(feat.get('macd_cross_5')),\n"""
    new="""            'macd':feat.get('macd'),'macd_signal':feat.get('macd_signal'),\n            'macd_above_signal':bool(feat.get('macd_above_signal')),\n            'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),\n            'macd_cross_5':bool(feat.get('macd_cross_5')),\n            'momentum_type':feat.get('momentum_type'),\n            'momentum_fresh':bool(feat.get('momentum_fresh')),\n            'momentum_continuation':bool(feat.get('momentum_continuation')),\n"""
    if old in s:
        s=s.replace(old,new,1)

    old="""        'momentum_match_count':sum(1 for r in ok if r.get('momentum_match')),\n        'formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)',\n"""
    new="""        'momentum_fresh_count':sum(1 for r in ok if r.get('momentum_fresh')),\n        'momentum_continuation_count':sum(1 for r in ok if r.get('momentum_continuation')),\n        'momentum_match_count':sum(1 for r in ok if r.get('momentum_match')),\n        'formula':'"""+FORMULA+"""',\n"""
    if old in s:
        s=s.replace(old,new,1)
    API.write_text(s)


def main():
    patch_usa(); patch_korea(); patch_api_diag()
    print('FINDER_MOMENTUM_FRESH_CONTINUATION_V45_OK')

if __name__=='__main__':
    main()
