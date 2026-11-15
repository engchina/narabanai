# python test_core.py  →  OK
# 失敗パスだけを見る: 正常時は本番で毎回通るが、ここは壊れても気づかない。
import os, sys, types

os.environ.setdefault("DAYTONA_API_KEY", "dummy")
import core


def test_thinking_block_skipped():
    """thinking 系モデルの content[0] は ThinkingBlock。text ブロックだけ拾えること。"""
    blocks = [types.SimpleNamespace(type="thinking", thinking="考え中"),
              types.SimpleNamespace(type="text", text="```python\ncode\n```")]
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(content=blocks, stop_reason="end_turn")

    sys.modules["anthropic"] = types.SimpleNamespace(
        Anthropic=lambda: types.SimpleNamespace(messages=FakeMessages()))
    assert core.llm("x", "anthropic") == "```python\ncode\n```"
    assert captured["messages"][0]["content"] == "x"


def test_empty_text_raises():
    """thinking だけで本文が無い応答は空コードを流さず例外にすること。"""
    class FakeMessages:
        def create(self, **kw):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="thinking", thinking="考えたまま尽きた")],
                stop_reason="max_tokens")

    sys.modules["anthropic"] = types.SimpleNamespace(
        Anthropic=lambda: types.SimpleNamespace(messages=FakeMessages()))
    try:
        core.llm("x", "anthropic")
    except RuntimeError as e:
        assert "max_tokens" in str(e), e
    else:
        raise AssertionError("空応答で例外が出ていない")


def test_worker_falls_back_to_preset_when_llm_dies():
    """AI 生成が例外を投げてもプリセット戦略で走り切り、結果が None にならないこと。"""
    ran = {}

    class FakeProcess:
        def code_run(self, code, timeout=None):
            ran["code"] = code
            return types.SimpleNamespace(exit_code=0, result='RESULT:{"spec": [1], "rides_p50": 7}')

    sb = types.SimpleNamespace(id="deadbeefcafe", process=FakeProcess())
    core.client = lambda: types.SimpleNamespace(create=lambda *a, **k: sb, delete=lambda *a: None)
    core.drop = lambda sb: None
    calls = []

    def dead_llm(*a, **k):
        calls.append(1)
        raise RuntimeError("401 invalid x-api-key")

    core.gen_code = dead_llm

    log = []
    res = core.worker(0, "どんな戦略でも", 1.0, use_ai=True, log=log)
    assert res is not None, f"結果が空: {log}"
    assert res["rides_p50"] == 7
    assert res["fallback"] is True, "代走の印が付いていない"
    assert repr(core.PRESETS[0][1]) in ran["code"], "プリセット戦略が実行されていない"  # worker は {code!r} で埋め込む
    assert any("プリセット戦略で続行" in l for l in log), log
    # LLM が死んでいるのに 3 回叩き直していないこと
    assert len(calls) == 1, f"gen_code を {len(calls)} 回呼んでいる"


def test_openai_retries_without_max_tokens():
    """OpenAI の新しいモデルは max_tokens / temperature を 400 で弾く。言い換えて 1 回で通ること。"""
    import io, json, urllib.error
    core.BACKENDS["openai"].update(base_url="http://x/v1", model="m", api_key="k")
    sent, real = [], core.urllib.request.urlopen

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a): return json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()

    def fake(req, timeout=None):
        body = json.loads(req.data)
        sent.append(body)
        if "max_tokens" in body:
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                         io.BytesIO(b'{"error":{"message":"Unsupported parameter: max_tokens"}}'))
        return Resp()

    core.urllib.request.urlopen = fake
    try:
        assert core.llm("x", "openai") == "ok"
    finally:
        core.urllib.request.urlopen = real
    assert len(sent) == 2, sent
    assert "max_completion_tokens" in sent[1] and "temperature" not in sent[1], sent[1]


if __name__ == "__main__":
    test_thinking_block_skipped()
    test_empty_text_raises()
    test_worker_falls_back_to_preset_when_llm_dies()
    test_openai_retries_without_max_tokens()
    print("OK")
