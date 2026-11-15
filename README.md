# 並ばない Narabanai

东京迪士尼排队策略模拟器。用户用自然语言描述策略，AI 写成 Python 代码，代码先通过仕様テスト，再在 3 个断网的 Daytona 沙箱里并行跑 300 次蒙特卡洛模拟。出错时 AI 读报错自动修复。最优方案的攻略卡由另一个 Sandbox 配信，屏幕上显示二维码，评委扫码就能带走。

## 运行

```bash
pip install daytona anthropic
export DAYTONA_API_KEY=...
# LLM 配置见下面的 .env；不配也能跑：关掉 AI 开关就用预置策略
python presets.py                   # 本地自检，不需要 Daytona，输出 OK
python server.py                    # 打开 http://localhost:8000
```

### 配置（`.env`，启动时自动读取，不需要 source）

```bash
DAYTONA_API_KEY=...
LLM_PROVIDER=openai                 # 写代码的 LLM（只用一个）：openai（默认，任意 OpenAI 兼容接口）| anthropic

# OpenAI 兼容接口：默认是 Nosana 上 vLLM 部署的 Qwen 3.8 27B
LLM_BASE_URL=https://<nosana-endpoint>/v1
LLM_MODEL=qwen3.8-27b
LLM_LABEL=Nosana · Qwen 3.8 27B     # 页面上显示的名字
LLM_API_KEY=                        # 自建 vLLM 没有 key 就留空
LLM_EXTRA={"chat_template_kwargs":{"enable_thinking":false}}

ANTHROPIC_API_KEY=...               # 只在 LLM_PROVIDER=anthropic 时需要
```

现场微调：`LLM_TIMEOUT=90`、`LLM_MAX_TOKENS=3000`、`LLM_EXTRA='{...}'`（并入请求体，例如关掉开源思考型模型的 thinking）、`FANOUT_MAX=8`、`CARD_TTL_MIN=2`、`NARABANAI_KVM=1`。

## 文件

- `engine.py` 模拟引擎和仕様テスト：整份源码和 AI 写的策略代码一起发进沙箱执行
- `presets.py` 三个预置策略：AI 关掉或生成失败时的保底
- `attractions.csv` 示意数据（等待时间、DPA 价格）：有真实数据时直接替换数值
- `core.py` 沙箱编排、LLM 调用（Claude / OpenAI 兼容）、故障注入、攻略卡配信（签名预览链接）
- `server.py` 标准库 HTTP 服务，提供页面和 API
- `web/` 已打包好的 React 页面（含 React Flow），现场不依赖 CDN
- `ui/` React 源码；改界面后在 ui/ 下执行 `npm i && npx esbuild app.jsx --bundle --minify --outfile=../web/app.js --define:process.env.NODE_ENV='"production"'`
- `SKILL.md` Agent Skill：让 Claude 等 Agent 调用本工具
- `test_core.py` 失败路径的自检：`python test_core.py` 输出 OK

## Demo 开关

- 「攻撃してみる」（隔離の証明）：把三段真正的攻击代码（外发数据、偷环境变量里的 key、从云元数据偷凭证）放进断网 Sandbox 执行，现场展示全部被挡住。

- 「故障注入」打开时，方案 B 第一次会被故意塞进一段坏代码，AI 必须读报错后修复。这样每次都能稳定演示自动修复和隔离。
- 结果出来后会自动在一个新 Sandbox 里配信攻略卡，并显示二维码（签名链接 1 小时有效）。这个 Sandbox 只放静态页面、不跑 AI 代码，所以不断网。
