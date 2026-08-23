from pathlib import Path

KIWOOM=Path('live_server/kiwoom.py')


def main():
    s=KIWOOM.read_text()

    old="""        # V39: independent momentum-discovery lane using the published search formula.\n        momentum=self._v39_momentum_rank_candidates(volume,dollar)\n"""
    new="""        # V42: never block the initial API startup with dozens of usa06012 calls.\n        # First discovery builds the normal liquid universe immediately. Subsequent\n        # background/manual discoveries may enrich it with the V39 momentum lane.\n        if self.discovery.get('updated_at'):\n            momentum=self._v39_momentum_rank_candidates(volume,dollar)\n        else:\n            momentum=[]\n            log.info('V42 momentum deferred on initial discovery; API startup remains non-blocking')\n"""

    if old in s:
        s=s.replace(old,new,1)
    elif 'V42 momentum deferred on initial discovery' not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: V39 momentum call')

    KIWOOM.write_text(s)
    print('FINDER_MOMENTUM_STARTUP_FIX_V42_OK')

if __name__=='__main__':
    main()
