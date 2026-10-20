"""运行日志：控制台 + server/data/xia.log（滚动），并给出代理可达性告警。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from urllib.parse import urlparse

from .config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"
_configured = False


def _safe_proxy_url(raw: str) -> str:
    parsed = urlparse(raw)
    if not parsed.hostname:
        return raw
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}" if parsed.scheme else host


def setup_logging() -> str:
    """幂等配置；返回日志文件路径。"""
    global _configured
    log_path = settings.db_path_abs.parent / "xia.log"
    if _configured:
        return str(log_path)
    _configured = True
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT, _DATE)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    marker = "_xia_handler"
    if any(getattr(handler, marker, False) for handler in root.handlers):
        return str(log_path)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    setattr(console, marker, True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    setattr(file_handler, marker, True)
    root.addHandler(console)
    root.addHandler(file_handler)

    # 第三方库的常规请求日志太吵，只保留警告及以上。
    for name in ("httpx", "httpcore", "chromadb", "watchfiles"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return str(log_path)


def log_proxy_env(logger: logging.Logger) -> None:
    """启动时检查 http(s)/all_proxy 是否可达；只做 TCP 连接，不发起 HTTP 请求。"""
    seen: set[str] = set()
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        raw = os.environ.get(key)
        if not raw or raw in seen:
            continue
        seen.add(raw)
        parsed = urlparse(raw)
        if not parsed.hostname:
            logger.warning("环境代理 %s=%s 无法解析", key, _safe_proxy_url(raw))
            continue
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((parsed.hostname, port), timeout=1.0):
                pass
            logger.info("环境代理 %s=%s 可连接", key, _safe_proxy_url(raw))
        except OSError as exc:
            if settings.llm_api_trust_env:
                action = ("外部 LLM API 会使用该代理，可能超时；"
                          "请启动代理，或设置 LLM_API_TRUST_ENV=false 直连")
            else:
                action = "外部 LLM API 已配置为直连（LLM_API_TRUST_ENV=false），不受该代理影响"
            logger.warning("环境代理 %s=%s 不可达（%s）；%s",
                           key, _safe_proxy_url(raw), exc, action)
