# 交易所接入复盘与 PerpMirror 落地

本记录对照 [AggregatedAccounts 的交易所接入踩坑总结](https://github.com/spiritedboy/AggregatedAccounts/blob/main/docs/exchange-integration-lessons.md)，只吸收适用于 PerpMirror 实盘写入链路的部分。AggregatedAccounts 是只读聚合平台，PerpMirror 会真实下单，两者不能直接复用同一套容错边界。

## 已落实

- **原生数量与统一名义价值分离**：`PositionSnapshot.quantity` 始终保留交易所下单单位。Binance 是标的币数量，OKX 是合约张数；统一对账和风控只使用 `notional_usdt`。OKX 优先信任 `notionalUsd`，缺失时使用 `pos × ctVal × ctMult × markPx`。
- **不猜仓位关键字段**：Binance 改用明确返回 `leverage`、`marginType` 的 `/fapi/v2/positionRisk`。存在仓位但杠杆或保证金模式缺失时直接拒绝该快照，不再默认 1 倍或 Cross。OKX 同样校验 `lever`、`mgnMode`。
- **业务错误不是 HTTP 错误**：HTTP 200 内的 Binance `code`、OKX 顶层 `code` 和订单项 `sCode/sMsg` 都会转换为明确异常。OKX 非对象 JSON 也会拒绝解析。
- **未知订单状态不盲目重试**：下单网络超时后按 client order ID 查单并重读仓位。只有权威仓位快照仍有偏差时，后续对账才会重新计算差额。
- **部分成交按状态收敛**：每次订单后重新读仓位，只补真实差额。反手必须先 reduce-only 平旧方向、确认归零，再开新方向。
- **单笔交易所上限分批**：读取 Binance `MARKET_LOT_SIZE.maxQty` 和 OKX `maxMktSz`，开仓、加仓、减仓、全平都按上限分批，每批后重新对账。
- **断线补偿**：Leader 私有 WS 只负责触发；重连后立即执行 REST 全量快照，对周期性全量对账做补偿。
- **故障域隔离**：每个 follower+symbol 独立加锁；Follower 快照失败、目标计算失败或单标的意外异常不会中断其他账户。
- **最小权限启动门**：OKX Follower 必须具备 `trade`，任何检测到 `withdraw` 的 OKX Key 都拒绝启动。签名使用 UTC 时间，并有固定向量回归测试。

## 有意不照搬

- 聚合平台的历史成交、资产曲线、周期 PnL、账本去重属于读模型问题。PerpMirror 当前不维护收益历史数据库，只维护实时目标仓位和必要的仓位所有权状态，因此没有伪造历史收益口径。
- 展示系统可以把缺失字段表示为 `None`；交易系统的杠杆和保证金模式会影响真实下单。对已有仓位缺失这两个字段时，本项目选择 fail closed，而不是展示一个空值后继续交易。
- OKX 的标的币估算数量可用于 UI，但不能替代 `pos` 做平仓数量；否则合约面值不为 1 时会多平或少平。

## 仍需在真实账户验证

- Binance One-way/Hedge、OKX net/long-short，以及 Cross/Isolated 的最小风险订单矩阵。
- 小面值币、`ctVal != 1`、`ctMult != 1`、最小张数和最大市价张数边界。
- API Key IP 白名单、限频、维护窗口，以及交易所返回“状态未知”时的查单可见延迟。
- 不同账户模式或产品线。当前明确只支持 USDT 结算线性永续，不会把 USDC、币本位或组合保证金语义猜成同一种产品。

真实验证必须使用专用子账户和最小风险，由账户所有者确认订单、仓位、杠杆和保证金变化；自动化测试不使用生产凭据。
