"""日志配置 - 按模块分文件记录，兼容 loguru 和标准 logging"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path


# ─────────────────────── 日志目录 ────────────────────────────
LOG_DIR = Path(os.environ.get("QUANT_LOG_DIR", "logs"))


# ─────────────────────── JSON 格式化器 ───────────────────────

class JsonFormatter(logging.Formatter):
    """
    结构化 JSON 日志格式化器。

    每条日志输出为单行 JSON，包含：
      ts        : ISO 8601 时间戳（UTC）
      level     : 日志级别
      logger    : logger 名称
      message   : 日志正文
      exc_info  : 异常信息（可选）
      extra_*   : 通过 extra={} 传入的额外字段（前缀 extra_）

    用法：
        logger.info("下单成功", extra={"strategy": "ma_cross", ...})
    """

    _RESERVED = frozenset(logging.LogRecord(
        "", 0, "", 0, "", (), None
    ).__dict__.keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        data: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                         .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        # 将 extra 中的自定义字段直接提升到顶层（去掉 LogRecord 自带字段）
        for key, val in record.__dict__.items():
            if key not in self._RESERVED:
                data[key] = val
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | str | None = None,
    use_json: bool = False,
) -> None:
    """
    初始化全局日志配置。

    - 控制台：彩色输出，级别由 log_level 决定
    - 文件：按日滚动，保留 30 天
      - logs/system.log   ：全局日志
      - logs/trading.log  ：交易相关
      - logs/market.log   ：行情相关
      - logs/risk.log     ：风控相关

    Args:
        log_level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）
        log_dir: 日志目录，None 时使用环境变量 QUANT_LOG_DIR 或 "logs"
        use_json: True 时文件日志使用 JSON 格式，控制台保持可读文本格式
    """
    global LOG_DIR
    if log_dir:
        LOG_DIR = Path(log_dir)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    # ── 格式 ─────────────────────────────────────────────────────
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    text_formatter = logging.Formatter(fmt, datefmt=date_fmt)
    file_formatter: logging.Formatter = JsonFormatter() if use_json else text_formatter

    # ── 根 logger ────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)

    # 避免重复添加 handler
    if root.handlers:
        root.handlers.clear()

    # 控制台 handler（始终使用文本格式，方便人眼阅读）
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(text_formatter)
    root.addHandler(console)

    # ── 分模块文件 handler ────────────────────────────────────────
    _add_file_handler(root, LOG_DIR / "system.log", level, file_formatter)

    # 交易模块单独记录
    trading_logger = logging.getLogger("trading")
    _add_file_handler(trading_logger, LOG_DIR / "trading.log", level, file_formatter)

    # 行情模块单独记录
    market_logger = logging.getLogger("market")
    _add_file_handler(market_logger, LOG_DIR / "market.log", level, file_formatter)

    # 风控模块单独记录
    risk_logger = logging.getLogger("risk")
    _add_file_handler(risk_logger, LOG_DIR / "risk.log", level, file_formatter)

    # 屏蔽三方库噪声
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def _add_file_handler(
    logger: logging.Logger,
    path: Path,
    level: int,
    formatter: logging.Formatter,
) -> None:
    """添加按日滚动的文件 handler。"""
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.suffix = "%Y-%m-%d"
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """获取具名 logger（供各模块使用）。"""
    return logging.getLogger(name)


# ─────────────────────── 交易结构化日志 ──────────────────────

def log_trade(
    *,
    strategy: str,
    inst_id: str,
    side: str,
    price: object,
    quantity: object,
    order_id: str = "",
    log_level: int = logging.INFO,
    extra_fields: dict | None = None,
) -> None:
    """
    以结构化方式记录一条单交易日志到 trading logger。

    所有字段都会被输出到日志，当使用 JsonFormatter 时可直接被
    日志分析平台（ELK / Loki 等）解析。

    Args:
        strategy:     策略名称
        inst_id:      交易对（如 BTC-USDT-SWAP）
        side:         方向（buy / sell）
        price:        委托/成交价格
        quantity:     委托/成交数量
        order_id:     订单 ID（可选，下单前可为空）
        log_level:    日志级别，默认 INFO
        extra_fields: 其他补充字段（如 position_side、fee 等）
    """
    trading_logger = logging.getLogger("trading")
    extra: dict = {
        "strategy": strategy,
        "inst_id": inst_id,
        "side": side,
        "price": str(price),
        "quantity": str(quantity),
        "order_id": order_id,
        **(extra_fields or {}),
    }
    msg = (
        f"strategy={strategy} inst_id={inst_id} side={side} "
        f"price={price} qty={quantity} order_id={order_id!r}"
    )
    trading_logger.log(log_level, msg, extra=extra)
