import html, json, os, re, threading, time, urllib.error, urllib.request

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, SessionExecuteRequest

from presets import PRESETS

HERE = os.path.dirname(os.path.abspath(__file__))
# .env を読む（シェルで export 済みの値が優先）。当日 source し忘れても鍵と設定が入るように。
if os.path.exists(os.path.join(HERE, ".env")):
    for _line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
        _k, _sep, _v = _line.strip().partition("=")
        if _sep and _v.strip() and not _k.startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))
ENGINE = open(os.path.join(HERE, "engine.py"), encoding="utf-8").read()
CSV = open(os.path.join(HERE, "attractions.csv"), encoding="utf-8").read()
CROWD = {"平日": 0.8, "週末": 1.0, "祝日": 1.3}
PARTY = 4
LABEL = {"app": "narabanai"}  # 残骸を後から名指しで掃除するための目印
KVM = os.getenv("NARABANAI_KVM") == "1"  # 画面トグルの初期値。Tier が足りなければ自動で通常に落ちる
# 攻略カード（QR）の寿命。作成からの実時間で、過ぎたら必ず消える。質疑でも使うなら伸ばす。
CARD_TTL_MIN = int(os.getenv("CARD_TTL_MIN", "2"))

# LLM は 1 つだけ使う。.env の LLM_PROVIDER で切替（openai 既定 = Nosana 上の Qwen など OpenAI 互換 | anthropic）。
BACKENDS = {
    "anthropic": {"label": "Claude", "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                  "ready": bool(os.getenv("ANTHROPIC_API_KEY"))},
    "openai": {"label": os.getenv("LLM_LABEL", "OpenAI"), "model": os.getenv("LLM_MODEL", "default"),
               "base_url": os.getenv("LLM_BASE_URL"),
               "api_key": os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""},
}
_b = BACKENDS["openai"]
# 本家 OpenAI は鍵が必須。自前の vLLM（Nosana など）は鍵なしでも繋がる。
_b["ready"] = bool(_b["base_url"]) and (bool(_b["api_key"]) or "openai.com" not in _b["base_url"])
# 現場合わせのつまみ。モデルやサーバーごとに癖が違うので、コードを触らず .env で調整する。
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))        # 秒。デモ中に 3 分固まらないように
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "3000"))  # OpenAI 互換側のみ
try:  # リクエストに混ぜる追加項目。例: 思考型モデルの思考を切る {"chat_template_kwargs":{"enable_thinking":false}}
    LLM_EXTRA = json.loads(os.getenv("LLM_EXTRA") or "{}")
except ValueError as e:
    print(f"LLM_EXTRA が JSON として読めないので無視: {e}")
    LLM_EXTRA = {}
DEFAULT_BACKEND = os.getenv("LLM_PROVIDER", "openai").lower()
DEFAULT_BACKEND = {"nosana": "openai"}.get(DEFAULT_BACKEND, DEFAULT_BACKEND)  # 当日 Nosana と書いても通る
if DEFAULT_BACKEND not in BACKENDS:
    DEFAULT_BACKEND = "openai"
BACKEND = BACKENDS[DEFAULT_BACKEND]
PROVIDER = BACKEND["label"]

# Fault injection for the live demo: deliberately broken code, the AI has to repair it.
BROKEN = "def choose(now, remaining, location, est_wait):\n    return remaining[0]['nmae']\n"

PROMPT = """次の東京ディズニーランドの回り方を Python のコードにしてください。
```python のコードブロックを 1 つだけ出力し、説明文は書かないこと。
コード中のコメントと変数名の説明は必ず日本語で書くこと（中国語・英語の散文は使わない）。

choose(now, remaining, location, est_wait) を必ず定義すること:
- now: 現在時刻（float の時間。9.0 = 9:00、21.0 で閉園）
- remaining: まだ乗っていない項目の list[dict]。キー: name, area, ride_min, peak_wait, kids_ok, dpa_price
- location: いま居るエリア名（開園時は "Entrance"）
- est_wait(name): その項目の今の予想待ち時間（分）
戻り値は次に向かう項目の name（必ず remaining の中から選ぶ）。今日を終えるなら None。

任意のグローバル変数:
- DPA = [項目名を最大 2 つ]  有料で待たずに乗れる。dpa_price > 0 の項目にのみ有効。
- LUNCH_AT = 12.0  昼食 45 分。

Python 標準ライブラリのみ使用すること。呼び出しは高速に（1 日分のシミュレーションで 500ms 未満）。

項目データ:
{csv}
戦略:
{strategy}
"""

def llm(msg, backend=None):
    b, stop = BACKENDS[backend or DEFAULT_BACKEND], None
    if b.get("base_url"):
        body = {"model": b["model"], "max_tokens": LLM_MAX_TOKENS, "temperature": 0.2,
                "messages": [{"role": "user", "content": msg}], **LLM_EXTRA}
        for retry in (True, False):
            req = urllib.request.Request(
                b["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {b['api_key'] or 'none'}"})
            try:
                with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
                    c = json.load(r)["choices"][0]
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if retry and e.code == 400 and ("max_tokens" in detail or "temperature" in detail):
                    # OpenAI の新しいモデルは max_tokens / temperature を受け付けない。言い換えて 1 回だけやり直す
                    body.pop("temperature", None)
                    body["max_completion_tokens"] = body.pop("max_tokens", LLM_MAX_TOKENS)
                    continue
                # 401 や モデル名違いを「HTTP Error 400」だけで終わらせない
                raise RuntimeError(f"HTTP {e.code}: {' '.join(detail.split())[:200]}")
        # reasoning 系は content が None で reasoning_content 側に入ることがある
        text, stop = c["message"].get("content"), c.get("finish_reason")
    else:
        import anthropic
        r = anthropic.Anthropic().messages.create(
            model=b["model"], max_tokens=2000, messages=[{"role": "user", "content": msg}])
        # thinking 系モデルは content[0] が ThinkingBlock なので text ブロックだけ拾う
        text, stop = "".join(b.text for b in r.content if b.type == "text"), r.stop_reason
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()  # reasoning models
    if not text:
        # 空文字を返すと空コードが Sandbox で走り、意味不明な仕様テスト失敗になる。
        # ここで落として worker のフォールバックに拾わせる。
        raise RuntimeError(f"LLM が本文を返さなかった (stop_reason={stop})。"
                           "thinking で max_tokens を使い切った可能性あり")
    return text


def gen_code(strategy, err=None, prev=None):
    msg = PROMPT.format(csv=CSV, strategy=strategy)
    if err:
        msg += f"\n前回のコードは実行時にエラーになりました。修正してください。\n前回のコード:\n{prev}\nエラー:\n{err}\n"
    text = llm(msg)
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def client():
    return Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))


def sweep():
    """前回の残骸を消す。delete を投げる daemon スレッドはサーバー終了で道連れになるので、
    Daytona 側の auto_delete と合わせた二重の保険。失敗してもデモは止めない。"""
    try:
        from daytona import ListSandboxesQuery
        c = client()
        gone = []
        for sb in c.list(ListSandboxesQuery(labels=LABEL)):
            c.delete(sb)
            gone.append(sb.id[:8])
        return gone
    except Exception as e:
        print(f"掃除に失敗（続行）: {type(e).__name__}: {e}")
        return []


def new_sandbox(log, kvm=False):
    """AI が書いたコードはネットワーク遮断の使い捨て Sandbox で走らせる。
    KVM=専用カーネルは Tier 次第で弾かれるので、落ちたら通常の Sandbox に下りる。"""
    base = dict(network_block_all=True, ephemeral=True, labels=LABEL,
                auto_stop_interval=5, auto_delete_interval=0,
                ttl_minutes=10)  # 実行は長くても数分。何かが固まっても 10 分で必ず消えて枠を返す
    if kvm:
        try:
            sb = client().create(CreateSandboxFromSnapshotParams(kvm=True, **base))
            log.append("専用カーネル（KVM）で起動")
            return sb
        except Exception as e:
            log.append(f"KVM 不可 → 通常 Sandbox に切替（{type(e).__name__}）")
    return client().create(CreateSandboxFromSnapshotParams(**base))


def live_sandboxes():
    """いま Daytona 上にある Sandbox。画面に出して「本当に生えて消える」のを見せるため。
    ponytail: ラベル絞り込みは作成直後に遅れて見えるので、全件取ってこちらで印を付ける。"""
    out = []
    for sb in client().list():
        labels = getattr(sb, "labels", None) or {}
        out.append({"id": sb.id[:8],
                    "state": str(getattr(sb, "state", "")).split(".")[-1].lower(),
                    "mine": labels.get("app") == LABEL["app"],
                    "role": labels.get("role", "sim")})
    return out


def drop(sb):
    threading.Thread(target=lambda: client().delete(sb), daemon=True).start()


def worker(i, strategy, crowd, use_ai, log, chaos=False, codes=None, kvm=False):
    sb = None
    codes = [] if codes is None else codes
    preset = PRESETS[i % len(PRESETS)][1]  # fanout がプリセット数を超えても回る
    try:
        log.append("Sandbox 作成中（ネットワーク遮断）...")
        t0 = time.time()
        sb = new_sandbox(log, kvm)
        log.append(f"Sandbox 起動: {sb.id[:8]}（{time.time() - t0:.1f} 秒）")
        code, err, fell = preset, None, False
        for attempt in range(3):
            if use_ai and chaos and i == 1 and attempt == 0:
                log.append("故障注入: わざと壊れたコードを投入")
                code = BROKEN
            elif use_ai:
                log.append("AI が戦略コードを生成中..." if attempt == 0 else "AI がエラーを読んで修正中...")
                try:
                    code = gen_code(strategy, err, code)
                except Exception as e:
                    # ponytail: LLM が落ちてもプリセット戦略で走り切る。空画面よりマシ。
                    log.append(f"AI 生成失敗: {type(e).__name__}: {e} → プリセット戦略で続行")
                    code, use_ai, fell = preset, False, True
            codes.append(code)
            log.append(f"Sandbox でシミュレーション 300 回実行（{attempt + 1} 回目）...")
            r = sb.process.code_run(ENGINE + f"\nmain({code!r}, {CSV!r}, {crowd}, party={PARTY})", timeout=60)
            out = r.result or ""
            m = re.search(r"RESULT:(.*)", out)
            if r.exit_code == 0 and m:
                res = json.loads(m.group(1))
                res["fallback"] = fell
                log.append(f"仕様テスト {len(res['spec'])}/{len(res['spec'])} 合格")
                log.append("完了")
                return res
            err = out[-1500:]
            log.append("エラー: " + (err.strip().splitlines() or ["unknown"])[-1])
            if not use_ai:
                break
    except Exception as e:  # show on screen instead of crashing the demo
        log.append(f"エラー: {type(e).__name__}: {e}")
    finally:
        if sb:
            drop(sb)
    return None


CARD = {}  # the one sandbox currently serving the shared plan card


def publish_card(plan):
    # Serves the winning plan as a phone page from a Daytona sandbox; returns a signed preview URL for the QR code.
    # This sandbox only serves our own static HTML (no AI code), so it keeps network for the preview.
    if CARD.get("sb"):
        drop(CARD.pop("sb"))
    sb = client().create(CreateSandboxFromSnapshotParams(
        # auto_stop は「イベントが無ければ」止まる仕組みで、ページ配信やアクセスでタイマーが延びる（実際 2 分で消えなかった）。
        # ttl_minutes は作成からの実時間で、状態に関係なく必ず破棄される。寿命はこちらで決める。
        labels={**LABEL, "role": "card"}, ttl_minutes=CARD_TTL_MIN,
        auto_stop_interval=CARD_TTL_MIN, auto_delete_interval=0))
    CARD["sb"] = sb
    page = render_card(plan)
    sb.process.code_run(f"import os; os.makedirs('/tmp/card', exist_ok=True); open('/tmp/card/index.html', 'w').write({page!r})", timeout=30)
    sb.process.create_session("web")
    sb.process.execute_session_command("web", SessionExecuteRequest(command="cd /tmp/card && python3 -m http.server 3000", run_async=True))
    time.sleep(1.5)
    return sb.create_signed_preview_url(3000, expires_in_seconds=3600).url


def render_card(p):
    e = html.escape
    hm = lambda h: f"{int(h):02d}:{int(round(h % 1 * 60)) % 60:02d}"
    rows = "".join(f"<li><time>{hm(s)}</time>{e('ランチ' if n == 'Lunch' else n)}</li>" for n, s, _ in p["timeline"])
    dpa = f"¥{p['dpa_cost']:,}（{PARTY}人分）" if p["dpa_cost"] else "なし"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>本日の攻略カード</title>
<style>
body{{margin:0;background:#fbf6ea;color:#1d2a5b;font:15px/1.6 "Hiragino Maru Gothic ProN","Hiragino Sans",sans-serif}}
header{{background:#1d2a5b;color:#fbf6ea;padding:22px 20px 18px;border-bottom:3px solid #c9a227;
background-image:radial-gradient(1.5px 1.5px at 20% 30%,#fff8 50%,#0000),radial-gradient(1px 1px at 70% 20%,#fff8 50%,#0000),radial-gradient(1.5px 1.5px at 85% 70%,#fff6 50%,#0000)}}
h1{{margin:0;font:600 22px "Hiragino Mincho ProN","Yu Mincho",serif;letter-spacing:.08em}}
header p{{margin:4px 0 0;color:#e6d9b0;font-size:13px}}
.k{{display:flex;gap:10px;padding:14px 16px 0}}.k div{{flex:1;background:#fff;border:1px solid #eadfc6;border-radius:14px;padding:10px;text-align:center}}
.k b{{display:block;font-size:24px}}.k small{{color:#7a6f57;font-size:11px}}
ol{{list-style:none;margin:14px 16px 24px;padding:6px 14px;background:#fff;border:1px solid #eadfc6;border-radius:16px}}
li{{padding:9px 0;border-bottom:1px dashed #eadfc6}}li:last-child{{border:0}}
time{{display:inline-block;width:58px;font-weight:700;color:#b08a1a}}
footer{{text-align:center;color:#7a6f57;font-size:11px;padding-bottom:24px}}
</style></head><body>
<header><h1>本日の攻略カード</h1><p>プラン {e(p['name'])}：{e(p['strategy'])}</p></header>
<div class="k"><div><b>{p['rides']}</b><small>アトラクション</small></div><div><b>{p['wait']}</b><small>待ち（分）</small></div><div><b style="font-size:16px;line-height:36px">{dpa}</b><small>DPA</small></div></div>
<ol>{rows}</ol>
<footer>並ばない Narabanai · Daytona Sandbox から配信 · サンプルデータによるデモ</footer>
</body></html>"""


# ── 案1: 隔離が本物であることを、攻撃して証明する ─────────────────────────
# 「Sandbox で動かしています」は全員が言う。実際に攻撃して失敗させるのが証明。
# どれも自分たちの Sandbox の隔離を検証するだけで、外部には何も起こさない。
PROBES = [
    ("外部へデータを送信", """
import json, urllib.request
try:
    urllib.request.urlopen("https://api.ipify.org", timeout=8).read()
    v = {"blocked": False, "detail": "外部に到達できてしまった"}
except Exception as e:
    v = {"blocked": True, "detail": f"{type(e).__name__}: {e}"[:160]}
print("VERDICT:" + json.dumps(v, ensure_ascii=False))
"""),
    ("環境変数から鍵を盗む", """
import json, os
hit = [k for k in os.environ if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
v = {"blocked": not hit,
     "detail": ("盗めた: " + ", ".join(hit)) if hit
               else f"環境変数 {len(os.environ)} 件すべて確認、API キーは 1 つも無い"}
print("VERDICT:" + json.dumps(v, ensure_ascii=False))
"""),
    ("クラウドの認証情報を盗む", """
import json, socket, urllib.request, urllib.error
got, notes = [], []
# SSRF の定番: メタデータ API から一時認証情報を取る（成功したかだけ見て、中身は読まない）
for name, url, hdr in [
    ("AWS", "http://169.254.169.254/latest/meta-data/iam/security-credentials/", {}),
    ("GCP", "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
     {"Metadata-Flavor": "Google"})]:
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=4)
        got.append(name + " の認証情報")
    except urllib.error.HTTPError as e:
        notes.append(f"{name} HTTP {e.code}")
    except Exception as e:
        notes.append(f"{name} {type(e).__name__}")
# 内部ホスト（SSH / Docker デーモン）に届くか
for host, port in [("10.0.0.1", 22), ("172.17.0.1", 2375)]:
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((host, port)); got.append(f"{host}:{port}")
    except Exception:
        pass
    finally:
        s.close()
v = {"blocked": not got,
     "detail": ("取れてしまった: " + ", ".join(got)) if got
               else "メタデータ API から認証情報は取れない（" + "、".join(notes) + "）。内部ホスト 2 箇所も到達不能"}
print("VERDICT:" + json.dumps(v, ensure_ascii=False))
"""),
]


def probe_worker(i, log, codes=None):
    """攻撃コードを本物の Sandbox で実行し、防がれたかどうかを返す。"""
    sb, name, code = None, PROBES[i][0], PROBES[i][1]
    try:
        log.append("Sandbox 作成中（ネットワーク遮断）...")
        t0 = time.time()
        sb = new_sandbox(log)
        log.append(f"Sandbox 起動: {sb.id[:8]}（{time.time() - t0:.1f} 秒）")
        if codes is not None:
            codes.append(code.strip())
        log.append(f"攻撃を実行: {name}")
        r = sb.process.code_run(code, timeout=40)
        out = r.result or ""
        m = re.search(r"VERDICT:(.*)", out)
        if m:
            v = json.loads(m.group(1))
            log.append(("防がれた: " if v["blocked"] else "通ってしまった: ") + v["detail"])
            log.append("完了")
            return {"attack": name, **v}
        log.append("エラー: " + ((out.strip().splitlines() or ["出力なし"])[-1])[:160])
    except Exception as e:
        log.append(f"エラー: {type(e).__name__}: {e}")
    finally:
        if sb:
            drop(sb)
    return None
