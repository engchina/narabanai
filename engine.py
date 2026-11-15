# Monte Carlo engine. Its full source is sent into a Daytona sandbox together with
# the (untrusted, AI-written) strategy code, then main() is called there.
import csv, io, json, math, random, time

OPEN, CLOSE = 9.0, 21.0
SEED = 42
KEYS = ("name", "area", "ride_min", "peak_wait", "kids_ok", "dpa_price")


def load(csv_text):
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    for r in rows:
        for k in ("ride_min", "base_wait", "peak_wait", "peak_hour", "dpa_price"):
            r[k] = float(r[k])
        r["kids_ok"] = r["kids_ok"] == "1"
    return rows


def mean_wait(a, t, crowd):
    # ponytail: gaussian peak per attraction; swap for real hourly data if you have it
    bump = math.exp(-((t - a["peak_hour"]) / 2.5) ** 2)
    return crowd * (a["base_wait"] + (a["peak_wait"] - a["base_wait"]) * bump)


def walk(src, dst):
    if src == dst:
        return 3
    return 10 if src == "Entrance" else 8  # ponytail: area-level walking minutes


def run_once(ns, attrs, crowd, rng):
    by = {a["name"]: a for a in attrs}
    lunch = ns.get("LUNCH_AT")
    t, loc, done, tl, waited, cost = OPEN, "Entrance", set(), [], 0.0, 0.0
    for _ in range(60):
        if lunch is not None and t >= lunch:
            tl.append(["Lunch", t, t + 0.75])
            t, lunch = t + 0.75, None
            continue
        remaining = [{k: a[k] for k in KEYS} for a in attrs if a["name"] not in done]
        if not remaining:
            break
        est = lambda x, t=t: round(mean_wait(by[x["name"] if isinstance(x, dict) else x], t, crowd))
        pick = ns["choose"](t, remaining, loc, est)
        if pick is None:
            break
        if isinstance(pick, dict):
            pick = pick.get("name")
        if pick not in by or pick in done:
            raise ValueError(f"仕様違反: choose() が無効な名前を返した: {pick!r}")
        a = by[pick]
        dpa = pick in list(ns.get("DPA", []))[:2] and a["dpa_price"] > 0
        w = 5 if dpa else mean_wait(a, t, crowd) * rng.uniform(0.7, 1.3)
        end = t + (walk(loc, a["area"]) + w + a["ride_min"]) / 60
        if end > CLOSE:
            break
        tl.append([pick, t, end])
        waited += w
        cost += a["dpa_price"] if dpa else 0
        t, loc = end, a["area"]
        done.add(pick)
    return len(done), waited, tl, cost


def spec_check(ns, attrs, crowd):
    # Contract every AI-written strategy must pass before it is simulated.
    if not callable(ns.get("choose")):
        raise AssertionError("仕様違反: choose() が定義されていない")
    passed = ["choose() 定義"]
    t0 = time.perf_counter()
    run_once(ns, attrs, crowd, random.Random(0))  # raises on invalid names
    ms = (time.perf_counter() - t0) * 1000
    passed += ["有効な名前のみ返す", "閉園までに終了"]
    if ms > 500:
        raise AssertionError(f"仕様違反: 1 試行 {ms:.0f}ms（上限 500ms）")
    passed.append(f"1 試行 {ms:.0f}ms")
    if len(list(ns.get("DPA", []))) > 2:
        raise AssertionError("仕様違反: DPA は最大 2 件")
    passed.append("DPA ≤ 2 件")
    return passed


def main(strategy_src, csv_text, crowd=1.0, n=300, party=4):
    ns = {}
    exec(strategy_src, ns)
    attrs = load(csv_text)
    spec = spec_check(ns, attrs, crowd)
    rng = random.Random(SEED)
    runs = sorted((run_once(ns, attrs, crowd, rng) for _ in range(n)), key=lambda r: (r[0], -r[1]))
    rides = [r[0] for r in runs]
    waits = sorted(r[1] for r in runs)
    q = lambda xs, p: xs[int(p * (len(xs) - 1))]
    mid = runs[len(runs) // 2]
    out = {
        "rides_p50": q(rides, 0.5),
        "rides_p20": q(rides, 0.2),  # 80% of runs ride at least this many
        "wait_p50": round(q(waits, 0.5)),
        "wait_p90": round(q(waits, 0.9)),
        "dpa_cost": round(mid[3] * party),
        "timeline": mid[2],
        "spec": spec,
        "seed": SEED,
    }
    print("RESULT:" + json.dumps(out, ensure_ascii=False))
    return out
