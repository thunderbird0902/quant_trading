"""配置文件加载器 - 支持 YAML + 环境变量覆盖"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from core.exceptions import ConfigError, MissingConfigError

logger = logging.getLogger(__name__)

# 项目根目录（本文件位于 utils/，上层为根目录）
_PROJECT_ROOT = Path(__file__).parent.parent


def load_yaml(path: str | Path) -> dict:
    """
    加载 YAML 配置文件。

    Args:
        path: YAML 文件路径（绝对路径或相对于项目根目录）

    Returns:
        解析后的配置字典

    Raises:
        ConfigError: 文件不存在或解析失败
    """
    path = Path(path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path

    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        logger.debug("已加载配置文件: %s", path)
        return data
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件解析失败 {path}: {e}") from e


def load_settings(config_dir: str | Path | None = None) -> dict:
    """
    加载系统主配置 settings.yaml，并将环境变量覆盖进来。

    加载顺序（后者覆盖前者）：
    1. config/settings.yaml
    2. 环境变量（QUANT_ 前缀）

    Args:
        config_dir: 配置目录，默认为项目根目录下的 config/

    Returns:
        合并后的配置字典
    """
    config_dir = Path(config_dir) if config_dir else _PROJECT_ROOT / "config"
    settings_path = config_dir / "settings.yaml"

    config = load_yaml(settings_path) if settings_path.exists() else {}

    # 环境变量覆盖（QUANT_SYSTEM__LOG_LEVEL=DEBUG → config["system"]["log_level"]="DEBUG"）
    _merge_env_overrides(config)

    return config


def load_okx_config(config_dir: str | Path | None = None) -> dict:
    """加载 OKX 专属配置，优先读 YAML，环境变量可覆盖。"""
    config_dir = Path(config_dir) if config_dir else _PROJECT_ROOT / "config"
    config = load_yaml(config_dir / "okx_config.yaml") if (config_dir / "okx_config.yaml").exists() else {}

    okx = config.setdefault("okx", {})

    # 环境变量优先级高于 YAML（若设置则覆盖）
    okx["api_key"]    = os.environ.get("OKX_API_KEY",    okx.get("api_key", ""))
    okx["secret_key"] = os.environ.get("OKX_SECRET_KEY", okx.get("secret_key", ""))
    okx["passphrase"] = os.environ.get("OKX_PASSPHRASE", okx.get("passphrase", ""))
    okx["flag"]       = os.environ.get("OKX_FLAG",       okx.get("flag", "1"))

    if not okx["api_key"]:
        logger.warning("OKX API Key 未配置，部分功能不可用")

    return config


def get_env(key: str, default: str | None = None, required: bool = False) -> str:
    """
    读取环境变量。

    Args:
        key:      环境变量名
        default:  默认值
        required: 为 True 时若变量不存在则抛出异常

    Returns:
        环境变量值

    Raises:
        MissingConfigError: required=True 且变量不存在
    """
    value = os.environ.get(key, default)
    if required and not value:
        raise MissingConfigError(key)
    return value or ""


def get_nested(config: dict, *keys: str, default: Any = None) -> Any:
    """
    从嵌套字典中安全取值。

    Examples:
        get_nested(cfg, "system", "log_level", default="INFO")
        → cfg["system"]["log_level"] 或 "INFO"
    """
    current = config
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, None)
        if current is None:
            return default
    return current


# ─────────────────────── 内部工具 ────────────────────────────

def _merge_env_overrides(config: dict) -> None:
    """
    将 QUANT__ 前缀的环境变量合并入配置。

    规则：QUANT__SECTION__KEY=VALUE → config[section][key] = VALUE
         QUANT__KEY=VALUE          → config[key] = VALUE
    键名统一转为小写。
    """
    prefix = "QUANT__"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        parts = env_key[len(prefix):].lower().split("__")
        target = config
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = env_val
