# Fallback strategies: used when AI is off, or if live generation fails on stage.
# Run `python presets.py` for a local self-check (no Daytona needed).

PRESETS = [
    ("開園ダッシュで一番人気から。待ち時間が長い順に回る。",
     '''
def choose(now, remaining, location, est_wait):
    return max(remaining, key=lambda a: a["peak_wait"])["name"]
'''),
    ("今いちばん空いているものへ。同じエリア優先、12時にランチ。",
     '''
LUNCH_AT = 12.0
def choose(now, remaining, location, est_wait):
    return min(remaining, key=lambda a: est_wait(a["name"]) + (0 if a["area"] == location else 5))["name"]
'''),
    ("人気トップ2はプレミアアクセスを購入。残りは空いている順。",
     '''
_top = None
DPA = []
def choose(now, remaining, location, est_wait):
    global _top
    if _top is None:
        _top = [a["name"] for a in sorted(remaining, key=lambda a: -a["peak_wait"])[:2]]
        DPA.extend(_top)
    for n in _top:
        if any(a["name"] == n for a in remaining):
            return n
    return min(remaining, key=lambda a: est_wait(a["name"]))["name"]
'''),
]

if __name__ == "__main__":
    import os, engine
    csv_text = open(os.path.join(os.path.dirname(__file__) or ".", "attractions.csv"), encoding="utf-8").read()
    res = [engine.main(code, csv_text) for _, code in PRESETS]
    assert all(r["rides_p50"] > 0 and r["timeline"] for r in res)
    assert res[2]["wait_p50"] < res[0]["wait_p50"], "DPA should cut waiting vs naive"
    try:
        engine.main("def choose(*a): return 'nope'", csv_text, n=1)
        raise AssertionError("invalid pick should raise")
    except ValueError:
        pass
    print("OK")
