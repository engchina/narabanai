---
name: narabanai
description: Compare Tokyo Disneyland touring strategies (queue order, Premier Access/DPA purchases, lunch timing) by simulating them in isolated Daytona sandboxes. Use when someone asks how to plan a Disney day, whether DPA is worth buying, or which ride order wastes the least time.
---

# Narabanai: Disney touring strategy simulator

Requires the Narabanai server running locally (`python server.py`, http://localhost:8000).

1. Write three strategies as one-line natural-language rules (Japanese or English), e.g.
   "Buy DPA for Beauty and the Beast, then always pick the shortest wait in the same area."
2. Start a run:
   `curl -s localhost:8000/api/run -d '{"strategies":["...","...","..."],"crowd":"週末","use_ai":true}'`
   → `{"id": "..."}`. `crowd` is one of 平日 / 週末 / 祝日.
3. Poll `curl -s localhost:8000/api/jobs/<id>` until every entry of `done` is true.
4. Each `results[i]` has `rides_p50`, `rides_p20` (80% chance of at least this many), `wait_p50`, `wait_p90` (minutes),
   `dpa_cost` (yen for a family of 4) and `timeline` (`[name, start_hour, end_hour]`).
5. Recommend the plan with the most rides; if it costs DPA money, also report the best plan with `dpa_cost` 0
   and how many rides the money buys.
6. The winning plan is also served as a phone page from a Daytona sandbox: POST `{"plan": {...}}` to `localhost:8000/api/share` → `{"url": signed preview URL}`.

Wait times and DPA prices are sample data. Every AI-written strategy passes a spec test and runs in a network-blocked sandbox.
