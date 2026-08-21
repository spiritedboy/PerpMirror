from __future__ import annotations

from decimal import Decimal
from typing import Any

from perpmirror.enums import ReconcileAction
from perpmirror.logging_utils import SecretRedactionFilter
from perpmirror.models import TradeNotification


def _number(value: Decimal | None, suffix: str = "") -> str:
    if value is None:
        return "暂不可用"
    return f"{value.quantize(Decimal('0.01'))}{suffix}"


class FeishuCardBuilder:
    _styles = {
        ReconcileAction.OPEN: ("green", "🟢", "开仓"),
        ReconcileAction.ADD: ("blue", "🔵", "加仓"),
        ReconcileAction.REDUCE: ("orange", "🟠", "减仓"),
        ReconcileAction.CLOSE: ("red", "🔴", "平仓"),
        ReconcileAction.FLIP: ("purple", "🟣", "反手"),
        ReconcileAction.RISK_BLOCKED: ("yellow", "🛡", "风控拦截"),
        ReconcileAction.ORDER_FAILED: ("red", "❌", "执行失败"),
    }
    _side_labels = {"long": "多", "short": "空", "flat": "空仓"}

    def trade(self, event: TradeNotification, *, dry_run: bool = False) -> dict[str, Any]:
        template, icon, action = self._styles.get(
            event.action, ("grey", "ℹ️", "通知")
        )
        if dry_run:
            template, icon, action = "yellow", "🟡", f"模拟{action}"
        title = f"{icon} {action} · {event.symbol}"
        display_after = event.target_notional if dry_run else event.final_notional
        account_line = (
            f"**{event.follower_id}** · {event.exchange.value.upper()} · "
            f"{event.copy_mode.value.upper()}"
        )
        position_line = (
            f"{self._side_labels[event.side.value]} · {_number(event.previous_notional, ' U')} → "
            f"{_number(display_after, ' U')}"
        )
        order_line = (
            f"目标 {_number(event.target_notional, ' U')} · "
            f"本次 {_number(event.order_notional, ' U')} · {_number(event.leverage, 'x')}"
        )
        lines = [account_line, position_line, order_line]
        if event.error_code:
            lines.append(f"错误码 `{event.error_code}`")
        if event.error_message:
            error = " ".join(SecretRedactionFilter.redact(event.error_message).split())
            if len(error) > 180:
                error = f"{error[:177]}..."
            lines.append(f"原因：{error}")
        lines.append(event.created_at.astimezone().strftime("%m-%d %H:%M:%S"))
        content = "\n".join(lines)
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
                "padding": "12px 12px 12px 12px",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [{"tag": "markdown", "content": content}],
            },
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
        mode = "DRY RUN" if dry_run else "LIVE"
        icon = "🟡" if dry_run else "🔴"
        template = "yellow" if dry_run else "red"
        follower_rows = "\n".join(
            f"{name} · {exchange.upper()}/{copy_mode.upper()} · {count} 仓"
            for name, exchange, copy_mode, _equity, count in followers
        )
        content = (
            f"Leader · {leader_exchange.upper()} · {leader_positions} 仓\n"
            f"{follower_rows or '无 Follower'}\n"
            f"每 {reconcile_interval} 秒对账"
        )
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": f"{icon} PerpMirror · {mode}"},
                "padding": "12px 12px 12px 12px",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
