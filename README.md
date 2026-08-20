# PerpMirror

PerpMirror 是一个运行于 Python 3.12+ / WSL Ubuntu 的 Binance 与 OKX USDT 永续合约自动跟单后端。它支持一个 Leader、多个 Follower，并允许每个 Follower 独立选择 FIXED 固定保证金或 RATIO 账户权益风险敞口比例。

> 当前示例配置和程序默认均为 `dry_run: true`。项目不会自行切换到 LIVE，也不应使用真实资金做开发测试。

## 核心设计

PerpMirror 不逐笔照搬 Leader 订单，而是持续维护 Follower 的目标仓位：

```text
Leader private WS ──触发──> debounce / pending tick
                              │
Periodic full reconcile ──────┤
                              ▼
                    REST 获取 Leader 真实快照
                              ▼
                TargetCalculator (FIXED / RATIO)
                              ▼
                    REST 获取 Follower 真实快照
                              ▼
            Reconciler → RiskManager → ExecutionEngine
                              ▼
                  下单 → 再读仓位 → 再次对账
                              │
                              └──> 独立通知队列 → 飞书卡片
```

因此，WS 漏消息、断线、程序重启、部分成交、人工偏离或一次同步失败，都能由下一次全量对账根据交易所真实仓位修复。WS 重连后会立即触发全量对账。

关键安全策略：

- 不确定等于不增加风险。订单 HTTP 状态未知时先按 client order ID 查询，并重读仓位，绝不盲目重下。
- 反手分成 reduce-only 平旧方向、确认归零、开新方向三段；禁止一次下“双倍数量”反手。
- 每个 follower+symbol 有独立异步锁，不同标的受全局信号量控制。
- 风控区分增加风险和减少风险；超限不应阻止退出，Kill Switch 行为由配置明确控制。
- 业务层只处理统一模型；交易所原始 JSON 只在 Binance/OKX 适配器内解析。
- 核心金额、价格、数量、杠杆和合约换算全部使用 `Decimal`。

## 跟单模式

FIXED 是固定保证金模式：

```text
target_notional = fixed_margin_usdt × follower_leverage
```

只要 Leader 的对应仓位非零，目标名义价值保持固定。Leader 加仓或部分减仓不会让 Follower 重复增加固定保证金；Leader 清仓时目标变为零。

RATIO 是账户权益风险敞口比例：

```text
leader_exposure = abs(leader_position_notional) / leader_equity
target_notional = follower_equity × leader_exposure × copy_ratio
```

RATIO 的目标名义价值不依赖 Follower 杠杆；杠杆只影响保证金占用和爆仓风险。

## 支持范围

- Binance USDⓈ-M、USDT 结算永续合约。
- OKX V5、USDT 结算线性 SWAP。
- Binance→Binance、Binance→OKX、OKX→Binance、OKX→OKX。
- Binance One-way/Hedge 和 OKX net/long-short 下单参数适配。

如果同一标的同时存在多空两个 Hedge leg，第一版会拒绝将其压成一个目标，以免含义不明确。Leader 标的在 Follower 交易所不存在时会安全跳过并告警，不会猜测符号。

## 安装（WSL Ubuntu）

```bash
cd /home/yyf/codex/PerpMirror
python3 --version              # 需要 3.12+
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp config.example.yaml config.yaml
cp .env.example .env
```

所有命令均在 WSL Ubuntu 执行；不要调用 Windows Python 或使用 `C:\` 路径。

## API Key 与账户安全

`.env` 仅保存 Secret，`config.yaml` 仅保存环境变量名和非敏感参数。两者中的 `.env` 已被 Git 忽略。

- Leader Key 尽量只授予读取账户/仓位权限。
- Follower Key 只授予读取和合约交易权限。
- **永远不要授予提现权限。**
- 为所有 Key 配置 IP 白名单，并使用专用子账户/独立交易账户。
- 不要把人工合约仓位与 PerpMirror 放在同一个 Follower 账户；目标仓位对账可能会修正人工仓位。
- 日志过滤 API key、secret、passphrase、signature、Authorization 和飞书 Webhook。

将 `.env` 中的占位项填入真实值，但不要提交：

```env
LEADER_API_KEY=
LEADER_SECRET_KEY=
FOLLOWER1_API_KEY=
FOLLOWER1_SECRET_KEY=
FOLLOWER2_API_KEY=
FOLLOWER2_SECRET_KEY=
FOLLOWER2_PASSPHRASE=
FEISHU_WEBHOOK=
FEISHU_SECRET=
```

OKX 账户必须提供 passphrase。飞书可在 `config.yaml` 中保持 `enabled: false`，不配置时交易链路不受影响。

## 配置

完整、安全的默认配置见 [`config.example.yaml`](config.example.yaml)。主要参数：

- `app.dry_run`: 默认 `true`，禁止真实下单和杠杆/保证金模式修改。
- `sync_on_start`: 启动后按交易所真实快照做一次完整对账。
- `full_reconcile_interval_seconds`: 周期全量对账间隔。
- `heartbeat_interval_seconds`: 运行心跳日志间隔，设为 `0` 可关闭。
- `position_drift_threshold_percent` / `position_drift_min_usdt`: 防止价格微变导致频繁调仓。
- `fixed_margin_usdt`: FIXED 模式的固定保证金。
- `copy_ratio`: RATIO 倍率，`0.5/1/1.5` 分别表示 `50%/100%/150%`。
- `copy_leverage`, `fixed_leverage`, `max_leverage`: 杠杆选择和上限。
- `risk.*`: 单币、总仓位、单笔、开仓数量、允许做空、白名单和黑名单。
- `kill_switch`: 禁止增加新风险；`kill_switch_close_positions` 决定是否允许机器人继续减仓/平仓。

## 命令

仅校验配置、认证、服务器时间、产品元数据、权益、仓位与 Position Mode，不启动跟单：

```bash
python -m perpmirror --check-config
```

强制 DRY_RUN（即使 YAML 写成 LIVE，也会在读取 LIVE 安全门前强制覆盖）：

```bash
python -m perpmirror --dry-run
```

只执行一次全量对账并退出：

```bash
python -m perpmirror --once --dry-run
```

正式启动（当前仍建议 DRY_RUN）：

```bash
python -m perpmirror --dry-run
```

自定义文件路径：

```bash
python -m perpmirror --config /absolute/path/config.yaml --env /absolute/path/.env --once --dry-run
```

无 Key 的 Fake 端到端演示：

```bash
python examples/fake_dry_run.py
```

## 飞书通知

使用群自定义机器人的 Interactive Message Card，不发送简单文本。OPEN、ADD、REDUCE、CLOSE、FLIP、失败和风控卡片由统一事件模型渲染；DRY_RUN 卡片有明显黄色模拟标记。通知经过有界 `asyncio.Queue`，最多重试三次并指数退避，永久失败会丢弃并记录错误，绝不会阻塞交易或 WS。

配置群机器人 Webhook 和可选签名 Secret 后：

```yaml
feishu:
  enabled: true
  webhook_env: FEISHU_WEBHOOK
  secret_env: FEISHU_SECRET
  max_retries: 3
```

## 交易所精度与模式

Binance 启动时读取 `/fapi/v1/exchangeInfo`，市价单使用 `MARKET_LOT_SIZE`（缺失才回退到 `LOT_SIZE`），并校验 `MIN_NOTIONAL`。不会用 `quantityPrecision` 简单 round。

OKX 的 `sz` 是合约张数。系统使用 `ctVal × ctMult × markPrice` 将目标名义价值转换成张数，再按 `lotSz` 向下截断，并校验 `minSz`。第一版只接受 `ctType=linear` 且 `settleCcy=USDT` 的 SWAP。

程序只检测并适配 Position Mode，不会擅自修改整个账户模式。Binance Hedge Mode 使用 `positionSide`；OKX long/short mode 使用 `posSide`。OKX 的 cross/isolated 由订单 `tdMode` 选择。

## 日志、停止和恢复

日志同时写入控制台和 `logs/perpmirror.log`。常见事件包括 `STARTUP_CHECK`、`RECONCILE`、`ORDER_FAILED`、`HTTP_RETRY` 和 `LEADER_WS`。

按 `Ctrl+C` 或发送 `SIGTERM` 会停止新的对账触发，等待当前关键操作，取消周期任务和 WS，排空通知队列，关闭 HTTP 连接后退出。

程序不保存仓位数据库。重启后第一件事是读取交易所真实仓位，所以目标已经满足时会 NOOP，不会因本地没有历史记录而重复开仓。

## 测试与质量检查

```bash
source .venv/bin/activate
pytest -q -s
ruff check perpmirror tests examples
mypy perpmirror
python -m compileall -q perpmirror
```

测试覆盖 FIXED/RATIO、开加减平、双向反手、幂等、重启等价状态、漂移阈值、风控、DRY_RUN、部分成交、HTTP 超时但已成交、符号映射、Binance 数量截断、OKX 合约张数、WS pending tick 和飞书 JSON/Secret 脱敏。

## LIVE 前检查清单

只有账户所有者明确决定启用真实交易时才进行以下操作：

1. 使用专用 Follower 子账户，清理人工仓位和挂单。
2. 核对 Leader/Follower API 权限、IP 白名单，确认没有提现权限。
3. 先运行 `--check-config`，再长时间运行 `--dry-run` 并核对每个目标和数量。
4. 核对 Binance Position Mode、OKX account/position mode、cross/isolated 和每个标的杠杆。
5. 核对所有风险上限、allowlist/blocklist、最小名义价值和飞书告警。
6. 小范围 testnet/demo 或最小风险验证由账户所有者本人执行。
7. 将 `dry_run` 改为 `false`，并由本人显式设置二次安全门：

```bash
export PERPMIRROR_LIVE_ACK=I_UNDERSTAND_REAL_FUNDS_ARE_AT_RISK
python -m perpmirror
```

未同时满足 YAML 和环境变量安全门时，程序拒绝 LIVE。PerpMirror 不承诺盈利；杠杆合约可能造成全部保证金损失。

## 官方协议依据

- [Binance USDⓈ-M Futures API](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/Introduction)
- [OKX API V5](https://www.okx.com/docs-v5/en/)
- [飞书自定义机器人发送飞书卡片（Card JSON 2.0）](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/quick-start/send-message-cards-with-custom-bot)
