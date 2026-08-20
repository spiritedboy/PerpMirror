from __future__ import annotations

from decimal import Decimal
from typing import Any

from perpmirror.enums import CopyMode, ReconcileAction
from perpmirror.logging_utils import SecretRedactionFilter
from perpmirror.models import TradeNotification


def _number(value: Decimal | None, suffix: str = "") -> str:
    if value is None:
        return "暂不可用"
    return f"{value.quantize(Decimal('0.01'))}{suffix}"


class FeishuCardBuilder:
    _styles = {
        ReconcileAction.OPEN: ("green", "🟢 跟单开仓成功"),
        ReconcileAction.ADD: ("blue", "🔵 跟单加仓成功"),
        ReconcileAction.REDUCE: ("orange", "🟠 跟单减仓成功"),
        ReconcileAction.CLOSE: ("red", "🔴 跟单平仓成功"),
        ReconcileAction.FLIP: ("purple", "🟣 跟单反手成功"),
        ReconcileAction.RISK_BLOCKED: ("yellow", "🛡 风控拦截"),
        ReconcileAction.ORDER_FAILED: ("red", "❌ 跟单失败"),
    }

    def trade(self, event: TradeNotification, *, dry_run: bool = False) -> dict[str, Any]:
        template, title = self._styles.get(event.action, ("grey", "PerpMirror 通知"))
        if dry_run:
            template, title = "yellow", f"🟡 DRY RUN · {title.replace('成功', '模拟')}"
        mode = "FIXED 固定金额" if event.copy_mode == CopyMode.FIXED else "RATIO 等比例"
        fields = [
            ("Follower", event.follower_id),
            ("Exchange", event.exchange.value.upper()),
            ("Symbol", f"{event.symbol} · {event.side.value.upper()}"),
            ("模式", mode),
            ("执行前", _number(event.previous_notional, " U")),
            ("目标仓位", _number(event.target_notional, " U")),
            ("本次订单", _number(event.order_notional, " U")),
            ("执行后", _number(event.final_notional, " U")),
            ("杠杆", _number(event.leverage, "x")),
        ]
        if event.copy_mode == CopyMode.FIXED:
            fields.append(("固定保证金", _number(event.fixed_margin, " U")))
        else:
            fields.extend(
                [
                    ("Leader Equity", _number(event.leader_equity, " U")),
                    ("Leader Position", _number(event.leader_notional, " U")),
                    (
                        "Leader Exposure",
                        _number(
                            event.leader_exposure_ratio * Decimal("100")
                            if event.leader_exposure_ratio is not None
                            else None,
                            "%",
                        ),
                    ),
                    (
                        "Copy Ratio",
                        _number(
                            event.copy_ratio * Decimal("100") if event.copy_ratio is not None else None, "%"
                        ),
                    ),
                    ("Follower Equity", _number(event.follower_equity, " U")),
                ]
            )
        fields.extend(
            [
                ("Order ID", event.order_id or "暂不可用"),
                ("Realized PnL", _number(event.realized_pnl, " U")),
                ("状态", "✅ SUCCESS" if event.success else "❌ FAILED"),
                ("时间", event.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")),
            ]
        )
        if event.error_code:
            fields.append(("Error Code", event.error_code))
        if event.error_message:
            fields.append(("Error", SecretRedactionFilter.redact(event.error_message)))
        content = "\n".join(f"**{name}**\n{value}" for name, value in fields)
        return {
            "config": {"wide_screen_mode": True},
            "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        }

    @staticmethod
    def startup(
        *,
        dry_run: bool,
        leader_exchange: str,
        leader_equity: Decimal,
        leader_positions: int,
        followers: list[tuple[str, str, str, Decimal, int]],
        reconcile_interval: Decimal,
    ) -> dict[str, Any]:
        mode = "🟡 DRY RUN · 不会真实下单" if dry_run else "🔴 LIVE · 真实资金交易"
        follower_rows = "\n".join(
            f"- {name} | {exchange.upper()} | {copy_mode.upper()} | "
            f"Equity {_number(equity, ' U')} | {count} positions"
            for name, exchange, copy_mode, equity, count in followers
        )
        content = (
            f"**运行模式**\n{mode}\n"
            f"**Leader**\n{leader_exchange.upper()} | Equity {_number(leader_equity, ' U')} | "
            f"{leader_positions} positions\n"
            f"**Followers**\n{follower_rows or '无'}\n"
            "**WebSocket State**\nSTARTING\n"
            f"**Full Reconcile**\n每 {reconcile_interval} 秒"
        )
        return {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "🚀 PerpMirror 已启动"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        }
