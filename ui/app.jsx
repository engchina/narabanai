import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Handle, Position, MarkerType } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import QRCode from "qrcode";

const COLORS = ["#2b4ea2", "#c8456b", "#1f8a70", "#b8761f", "#6b4ea2", "#1f6b8a", "#8a1f4e", "#4e8a1f"];
const OPEN = 9, CLOSE = 21;
const colorOf = (i) => COLORS[i % COLORS.length];
const nameOf = (i) => String.fromCharCode(65 + i); // A, B, C, …
const yen = (n) => `¥${n.toLocaleString()}`;

const hhmm = (h) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.round((h % 1) * 60) % 60).padStart(2, "0")}`;

// Derive node states from the worker's log lines (core.py writes them).
function lane(logs = [], result, done) {
  const s = { code: "idle", sbx: "idle", res: "idle", fixes: 0, errs: 0, boot: null, lastErr: null, spec: null, chaos: false };
  for (const l of logs) {
    if (l.includes("作成中")) s.sbx = "active";
    else if (l.startsWith("Sandbox 起動")) { s.sbx = "ready"; s.boot = l.match(/（(.+?) 秒）/)?.[1]; }
    else if (l.includes("生成中")) s.code = "active";
    else if (l.includes("修正中")) { s.code = "active"; s.fixes++; }
    else if (l.includes("シミュレーション")) { if (s.code === "active") s.code = "done"; s.sbx = "active"; }
    else if (l.startsWith("エラー")) { s.sbx = "error"; s.errs++; s.lastErr = l.slice(4); }
    else if (l.startsWith("仕様テスト")) s.spec = l.match(/(\d+\/\d+)/)?.[1];
    else if (l.startsWith("故障注入")) { s.chaos = true; s.code = "error"; }
    else if (l.startsWith("AI 生成失敗")) { s.fell = true; s.code = "error"; }
    else if (l === "完了") { s.sbx = "done"; s.res = "done"; }
  }
  if (done && !result) s.res = "error";
  return s;
}

function Step({ data }) {
  return (
    <div className={`step ${data.state}`} style={{ "--line": data.color }}>
      <Handle type="target" position={Position.Left} id="in" />
      <Handle type="target" position={Position.Top} id="top-in" />
      <Handle type="source" position={Position.Top} id="top-out" />
      <div className="step-k">{data.kicker}{data.state === "active" && <span className="dot" />}</div>
      <div className="step-t">{data.title}</div>
      {data.sub && <div className="step-s">{data.sub}</div>}
      <Handle type="source" position={Position.Right} id="out" />
    </div>
  );
}
const nodeTypes = { step: Step };


function Flow({ strategies, job, useAi, who }) {
  const { nodes, edges } = useMemo(() => {
    const nodes = [], edges = [];
    strategies.forEach((_, i) => {
      const n = nameOf(i);
      const c = colorOf(i), r = job?.results?.[i], st = lane(job?.logs?.[i], r, job?.done?.[i]);
      const started = !!job?.logs?.[i]?.length, y = i * 112, id = (k) => `${n}-${k}`;
      const codeState = useAi ? st.code : started ? "done" : "idle";
      const codeSub = st.fell ? "生成失敗 → プリセットで代走" : st.chaos ? `故障注入 → 修正 ${st.fixes} 回` : st.fixes ? `修正 ${st.fixes} 回` : null;
      const sbxSub = st.sbx === "error" ? "エラーを隔離" : st.spec ? `仕様 ${st.spec} 合格 · 300 回` : st.boot ? `起動 ${st.boot} 秒` : null;
      const short = (t = "") => t.slice(0, 20) + (t.length > 20 ? "…" : "");
      nodes.push(
        { id: id("s"), type: "step", position: { x: 0, y }, data: { color: c, state: started ? "done" : "idle", kicker: `プラン ${n}`, title: short(strategies[i]) } },
        { id: id("c"), type: "step", position: { x: 250, y }, data: { color: c, state: codeState, kicker: "コード生成", title: useAi ? `${who[i]} が書く` : "プリセット戦略", sub: codeSub } },
        { id: id("x"), type: "step", position: { x: 500, y }, data: { color: c, state: st.sbx === "ready" ? "active" : st.sbx, kicker: "Daytona Sandbox", title: "隔離・ネットワーク遮断", sub: sbxSub } },
        { id: id("r"), type: "step", position: { x: 750, y }, data: { color: c, state: st.res, kicker: "結果", title: r ? `${r.rides_p50} アトラクション` : st.res === "error" ? "失敗" : "—", sub: r ? `待ち ${r.wait_p50} 分 · DPA ${r.dpa_cost ? yen(r.dpa_cost) : "なし"}` : null } },
      );
      const e = (a, b, on) => ({ id: `${n}:${a}>${b}`, source: id(a), sourceHandle: "out", target: id(b), targetHandle: "in", animated: on, style: { stroke: on ? "#1d2a5b" : "#cfc4a8", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed, color: on ? "#1d2a5b" : "#cfc4a8" } });
      edges.push(e("s", "c", codeState === "active"), e("c", "x", st.sbx === "active"), e("x", "r", false));
      if (st.errs) edges.push({
        id: `${n}-heal`, source: id("x"), sourceHandle: "top-out", target: id("c"), targetHandle: "top-in",
        type: "smoothstep", animated: st.code === "active", label: `自動修復 ×${st.fixes || st.errs}`,
        style: { stroke: "#c8102e", strokeWidth: 1.5, strokeDasharray: "5 4" },
        labelStyle: { fill: "#c8102e", fontWeight: 700, fontSize: 11 }, labelBgStyle: { fill: "#fffdf7" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#c8102e" },
      });
    });
    return { nodes, edges };
  }, [strategies, job, useAi, who]);

  // fitView は初回しか効かないので、本数が変わったら key で貼り直して再フィットさせる。
  // 高さも本数に追随させないと 4 本目以降が枠の下に隠れる。
  const n = strategies.length;
  // 小さい窓や裏タブで開いてから広げると、開いた時の倍率と位置のまま固まって枠外に消える。
  // 枠の大きさが変わるたびに再フィットする。
  const box = useRef(null), inst = useRef(null);
  useEffect(() => {
    const ro = new ResizeObserver(() => requestAnimationFrame(() => inst.current?.fitView({ padding: 0.08 })));
    if (box.current) ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  return (
    <div className="flow" ref={box} style={{ height: Math.min(280 + Math.max(0, n - 3) * 100, 760) }}>
      <ReactFlow key={n} onInit={(i) => (inst.current = i)} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.08 }}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
        panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false} preventScrolling={false}
        proOptions={{ hideAttribution: true }} />
    </div>
  );
}

function Timetable({ results }) {
  const hours = Array.from({ length: CLOSE - OPEN + 1 }, (_, k) => OPEN + k);
  const pos = (h) => `${((h - OPEN) / (CLOSE - OPEN)) * 100}%`;
  return (
    <div className="tt">
      <div className="tt-axis">{hours.map((h) => <span key={h} style={{ left: pos(h) }}>{h}</span>)}</div>
      {results.map((r, i) => r && (
        <div className="tt-row" key={i}>
          <span className="badge" style={{ "--line": colorOf(i) }}>{nameOf(i)}</span>
          <div className="tt-track">
            {hours.map((h) => <i key={h} style={{ left: pos(h) }} />)}
            {r.timeline.map(([name, s, e]) => (
              <div key={name + s} title={`${hhmm(s)}–${hhmm(e)} ${name}`} className={`blk ${name === "Lunch" ? "lunch" : ""}`}
                style={{ "--line": colorOf(i), left: pos(s), width: `${((e - s) / (CLOSE - OPEN)) * 100}%` }}>
                <span>{name === "Lunch" ? "ランチ" : name}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// Daytona 上の Sandbox をそのまま出す。ダッシュボードに切り替えずに
// 「生えて、消える」を同じ画面で見せるための帯。
function Sandboxes() {
  const [sbs, setSbs] = useState([]);
  useEffect(() => {
    let alive = true;
    const tick = () => fetch("/api/sandboxes").then((r) => r.json())
      .then((d) => alive && setSbs(d.sandboxes || [])).catch(() => {});
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const mine = sbs.filter((s) => s.mine).length;
  return (
    <div className="sbx-bar">
      <b>Daytona 上の Sandbox</b>
      <span className="sbx-n">{sbs.length}</span>
      <div className="sbx-chips">
        {sbs.length === 0
          ? <span className="sbx-empty">いま 0 個 — 使い終わった Sandbox は消えています</span>
          : sbs.map((s) => (
              <span key={s.id} className={`sbx ${s.state} ${s.mine ? "mine" : ""} ${s.role === "card" ? "card" : ""}`}>
                {s.id}<i>{s.role === "card" ? "攻略カード配信中" : s.state}</i></span>
            ))}
      </div>
      {mine > 0 && <span className="sbx-note">うち {mine} 個がこの実行のもの</span>}
    </div>
  );
}

// 案1: 「Sandbox で動かしています」を言葉でなく実演で示す。攻撃コードを本当に実行して、防がれるのを見せる。
function Probes({ names }) {
  const [job, setJob] = useState(null);
  const timer = useRef();
  const running = job && !job.done.every(Boolean);
  async function go() {
    clearInterval(timer.current);
    setJob({ logs: names.map(() => []), codes: names.map(() => []), results: names.map(() => null), done: names.map(() => false) });
    const { id } = await (await fetch("/api/probe", { method: "POST", body: "{}" })).json();
    timer.current = setInterval(async () => {
      const j = await (await fetch(`/api/jobs/${id}`)).json();
      setJob(j);
      if (j.done.every(Boolean)) clearInterval(timer.current);
    }, 400);
  }
  const MARK = { run: "実行中", ok: "防御", ng: "突破", err: "不明" };
  return (
    <section className="probes">
      <div className="probes-head">
        <b>隔離の証明</b>
        <span>AI が悪意あるコードを書いたら？ 攻撃コードを本当に Sandbox の中で実行します</span>
        <button onClick={go} disabled={running}>{running ? "攻撃中…" : "攻撃してみる"}</button>
      </div>
      {job && names.map((n, i) => {
        const r = job.results[i], done = job.done[i];
        const st = !done ? "run" : !r ? "err" : r.blocked ? "ok" : "ng";
        return (
          <div className={`probe ${st}`} key={n}>
            <span className="probe-mark">{MARK[st]}</span>
            <b>{n}</b>
            <span className="probe-detail">{r ? r.detail : done ? (job.logs[i][job.logs[i].length - 1] || "") : "Sandbox で実行中…"}</span>
            {job.codes[i]?.[0] && <details><summary>攻撃コード</summary><pre>{job.codes[i][0]}</pre></details>}
          </div>
        );
      })}
    </section>
  );
}

function Codes({ job, names }) {
  if (!job?.codes?.some((c) => c.length)) return null;
  return (
    <details className="logs-box" open>
      <summary>AI が書いたコード（このまま Sandbox で動いた実物）</summary>
      <section className="codes">
        {job.codes.map((cs, i) => cs.length > 0 && (
          <div className="code-lane" key={i} style={{ "--line": colorOf(i) }}>
            <b>{names[i] ?? `プラン ${nameOf(i)}`}</b>
            {cs.length > 1 ? (
              <div className="code-diff">
                <div><span className="tag bad">落ちた版</span><pre>{cs[cs.length - 2]}</pre></div>
                <div><span className="tag good">AI が読んで直した版</span><pre>{cs[cs.length - 1]}</pre></div>
              </div>
            ) : <pre>{cs[0]}</pre>}
          </div>
        ))}
      </section>
    </details>
  );
}

const better = (a, b) => a.rides_p50 - b.rides_p50 || b.wait_p50 - a.wait_p50; // >0 if a beats b

function App() {
  const [cfg, setCfg] = useState(null);
  const [strategies, setStrategies] = useState(["", "", ""]);
  const [crowd, setCrowd] = useState("週末");
  const [useAi, setUseAi] = useState(false);
  const [chaos, setChaos] = useState(true);
  const [kvm, setKvm] = useState(false); // 画面には出さない。解禁されたら NARABANAI_KVM=1
  const [job, setJob] = useState(null);
  const [share, setShare] = useState(null); // {state, url, qr, msg}
  const timer = useRef();

  useEffect(() => {
    fetch("/api/config").then((r) => r.json()).then((c) => { setCfg(c); setStrategies(c.presets); setUseAi(c.ai); setKvm(c.kvm); });
  }, []);

  const running = job && !job.done.every(Boolean);
  const lanes = strategies;
  const who = lanes.map(() => cfg?.provider);
  const names = lanes.map((_, i) => `プラン ${nameOf(i)}`);
  const res = job?.results || [];
  const ok = !running ? res.map((_, i) => i).filter((i) => res[i]) : [];
  const pick = (xs) => xs.length ? xs.reduce((a, b) => (better(res[a], res[b]) >= 0 ? a : b)) : null;
  const best = pick(ok);
  const freeBest = pick(ok.filter((i) => !res[i].dpa_cost));

  async function run() {
    clearInterval(timer.current);
    setShare(null);
    // 空欄は Sandbox を 1 個無駄にして、フロー図に中身のないレーンを作るだけ。実行前に落とす。
    const used = strategies.map((s) => s.trim()).filter(Boolean);
    const plans = used.length ? used : cfg.presets;
    if (plans.length !== strategies.length) setStrategies(plans);
    const n = plans.length;
    setJob({ logs: Array.from({ length: n }, () => []), codes: Array.from({ length: n }, () => []),
             results: Array(n).fill(null), done: Array(n).fill(false) });
    const { id } = await (await fetch("/api/run", { method: "POST", body: JSON.stringify({ strategies: plans, crowd, use_ai: useAi, chaos: useAi && chaos, kvm }) })).json();
    timer.current = setInterval(async () => {
      const j = await (await fetch(`/api/jobs/${id}`)).json();
      setJob(j);
      if (j.done.every(Boolean)) clearInterval(timer.current);
    }, 400);
  }

  // As soon as a winner exists, publish its card from a sandbox so the QR is ready without an extra click.
  useEffect(() => {
    if (best === null || share) return;
    const r = res[best];
    setShare({ state: "busy" });
    const plan = { name: nameOf(best), strategy: lanes[best], rides: r.rides_p50, wait: r.wait_p50, dpa_cost: r.dpa_cost, timeline: r.timeline };
    fetch("/api/share", { method: "POST", body: JSON.stringify({ plan }) }).then((x) => x.json()).then(async (out) => {
      if (out.error) throw new Error(out.error);
      setShare({ state: "ok", url: out.url, qr: await QRCode.toDataURL(out.url, { margin: 1, width: 320, color: { dark: "#1d2a5b", light: "#ffffff" } }) });
    }).catch((e) => setShare({ state: "err", msg: e.message }));
  }, [best, share]);

  if (!cfg) return null;
  const Badge = ({ i }) => <span className="badge" style={{ "--line": colorOf(i) }}>{nameOf(i)}</span>;

  return (
    <main>
      <header>
        <div>
          <div className="title-row">
            <h1>並ばない <small>Narabanai</small></h1>
            <span className="topic">東京ディズニーランドの回り方<em>（{cfg.party} 人家族で試算）</em></span>
          </div>
          <p className="pitch">AI が書いたコードを信じないための検証ループ</p>
          <p className="flowline">自然言語 → AI がコードを書く → 仕様テストで門前払い → 隔離実行 → 300 回の統計検証 → 落ちたらエラーを読んで自力修復</p>
        </div>
        <div className="controls">
          <div className="seg">{cfg.crowd.map((c) => <button key={c} className={c === crowd ? "on" : ""} onClick={() => setCrowd(c)}>{c}</button>)}</div>
          <div className="tgls">
            <label className="tgl"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> AI がコードを書く</label>
            <label className={`tgl ${useAi ? "" : "off"}`}><input type="checkbox" disabled={!useAi} checked={chaos} onChange={(e) => setChaos(e.target.checked)} /> 故障注入</label>
          </div>
        </div>
      </header>

      <section className="plans">
        {strategies.map((v, i) => (
          <div className="plan" key={i}>
            <Badge i={i} />
            <textarea value={v} rows={3} placeholder="回り方を一言で（例: 人気トップ2だけ課金して、あとは空いている順）"
              onChange={(e) => setStrategies(strategies.map((s, k) => (k === i ? e.target.value : s)))} />
          </div>
        ))}
        <div className="plan-ops">
          <button onClick={() => setStrategies([...strategies, cfg.presets[strategies.length % cfg.presets.length]])}
            disabled={running || strategies.length >= cfg.fanout_max} title={`最大 ${cfg.fanout_max} 本`}>＋ プラン</button>
          <button onClick={() => setStrategies(strategies.slice(0, -1))}
            disabled={running || strategies.length <= 1}>− プラン</button>
        </div>
        <button className="run" onClick={run} disabled={running}>{running ? "実行中…" : "比べる"}</button>
      </section>

      <Flow strategies={lanes} job={job} useAi={useAi} who={who} />

      <Sandboxes />

      {cfg.show_probes && <Probes names={cfg.probes} />}

      {best !== null && (
        <section className="stage">
          <div>
            <div className="kpis">
              {ok.map((i) => (
                <div key={i} className={`kpi ${i === best ? "best" : ""}`}>
                  <Badge i={i} />
                  <div className="kpi-n">{res[i].rides_p50}<small> アトラクション</small></div>
                  <div className="kpi-s">待ち {res[i].wait_p50} 分 · DPA {res[i].dpa_cost ? yen(res[i].dpa_cost) : "なし"}</div>
                  <div className="kpi-s">80% の確率で {res[i].rides_p20} 以上</div>
                  {res[i].fallback && <div className="kpi-s warn">AI の生成に失敗 → プリセットで代走</div>}
                </div>
              ))}
            </div>
            <div className="verdict">
              <p>最多はプラン <Badge i={best} />：<b>{res[best].rides_p50}</b> アトラクション{res[best].dpa_cost ? `（DPA ${yen(res[best].dpa_cost)}）` : "（DPA なし）"}</p>
              {res[best].dpa_cost > 0 && freeBest !== null && (
                <p>DPA なしならプラン <Badge i={freeBest} />：{res[freeBest].rides_p50} アトラクション → 差は <b>{res[best].rides_p50 - res[freeBest].rides_p50}</b> 個で <b>{yen(res[best].dpa_cost)}</b></p>
              )}
            </div>
          </div>
          <div className="share">
            {share?.state === "ok" ? (
              <>
                <img src={share.qr} alt="攻略カードの QR コード" />
                <b>スマホで攻略カードを受け取る</b>
                <span>Daytona Sandbox から配信 · 署名付き URL</span>
              </>
            ) : share?.state === "err" ? (
              <span className="note">配信に失敗：{share.msg}</span>
            ) : (
              <span className="wait">Sandbox で配信準備中…</span>
            )}
          </div>
        </section>
      )}

      <Codes job={job} names={names} />

      {job && (
        <details className="logs-box">
          <summary>Sandbox ログ</summary>
          <section className="logs">
            {job.logs.map((ls, i) => (
              <pre key={i} style={{ "--line": colorOf(i) }}><b>Sandbox {nameOf(i)}</b>{"\n"}{ls.map((l) => (l.startsWith("エラー") || l.startsWith("故障") ? `✕ ${l}` : `· ${l}`)).join("\n")}</pre>
            ))}
          </section>
        </details>
      )}

      {best !== null && (
        <>
          <details className="logs-box">
            <summary>時刻表（プランごとの 1 日）</summary>
            <Timetable results={res} />
          </details>
        </>
      )}
      <footer>待ち時間・DPA 料金はデモ用のサンプルデータです · AI が書いたコードはすべて仕様テストを通してから Daytona Sandbox（ネットワーク遮断）で実行</footer>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
