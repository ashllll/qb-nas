from __future__ import annotations

import logging
import os
import ipaddress
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


# ── 配置事实源与优先级 ──────────────────────────────────────────────────────
# 部署形态只有一份配置文件：容器内的 ENV_FILE（compose 把 config/ 目录挂到
# /config，指针是 /config/.env）。界面「保存并测试」写它，install.sh 写它，
# 容器启动读它——同一个文件。
#
# 优先级（唯一的约定，行为由 tests/test_config.py 锁住）：
#   · FILE_AUTHORITATIVE_KEYS：文件 > 进程环境 > 内置默认。
#     这几个键界面可写；pydantic 默认是「环境 > 文件」，只要 compose 用
#     env_file / -e 注入同名变量，界面刚写进文件的值就会在重启时被旧值盖回
#     （「界面改了没用、重启回滚」的机制）。文件优先把这条路堵死。
#   · DEPLOYMENT_PINNED_KEYS：进程环境 > 文件 > 默认。容器监听地址、端口、
#     数据库路径由 compose 钉死，避免 .env 与端口映射对不上。
#   · 其余键：文件 > 环境 > 默认（文件名就是事实源，方便手改）。
FILE_AUTHORITATIVE_KEYS: frozenset[str] = frozenset({"QBIT_HOST", "QBIT_USERNAME", "QBIT_PASSWORD"})
DEPLOYMENT_PINNED_KEYS: frozenset[str] = frozenset(
    {"SERVICE_HOST", "SERVICE_PORT", "STORE_DB_PATH"}
)

# 容器内运行身份（部署约定）：compose 以 1000:1001 跑，/vol3 上的 config/ 与
# data/ 也归这个用户。对不上时界面保存会 Permission denied，启动日志会点名。
EXPECTED_UID = 1000
EXPECTED_GID = 1001

_IN_CONTAINER = Path("/.dockerenv").exists()


class ConfigPersistError(OSError):
    """配置文件写不进去，带上定位信息（路径、errno、uid/gid、目录权限）。

    routes.py 捕获后回 500，并把 str(exc) 记进日志；带这些字段，
    用户/运维不必再猜「Permission denied」是哪一层、哪个用户造成的。
    """


# ── 子配置 ──────────────────────────────


@dataclass
class CrawlerConfig:
    timeout: int = 30
    max_depth: int = 2
    concurrency: int = 6
    max_detail_links: int = 200
    headless: bool = True
    http_first: bool = True
    disable_resources: bool = True
    block_ads: bool = True
    # 默认不做分辨率过滤：产品要求「多条全收」，1080p/720p 也是有效资源。
    allowed_resolutions: tuple[str, ...] = ()
    wait_until: str = "load"
    delay_before_return_html: float = 1.0
    scan_full_page: bool = True
    scroll_delay: float = 0.2
    max_scroll_steps: int = 8
    process_iframes: bool = True
    flatten_shadow_dom: bool = True
    remove_overlay_elements: bool = True
    remove_consent_popups: bool = True
    # — v0.9.0 新增 —
    max_retries: int = 1
    check_robots_txt: bool = False
    # — HTTP 优先取页（v3.1） —
    browser_fallback: bool = True
    http_impersonate: str = "chrome"
    retry_backoff: float = 0.4
    per_host_min_interval: float = 0.15
    per_host_concurrency: int = 4
    browser_max_pages: int = 2
    # mihomo/Clash fake-IP (198.18.0.0/15) 放行开关，开启后 SSRF 检查豁免该网段
    allow_fake_ip: bool = False

    def __post_init__(self) -> None:
        self.timeout = max(1, int(self.timeout))
        self.max_depth = max(1, int(self.max_depth))
        self.concurrency = max(1, int(self.concurrency))
        self.max_detail_links = max(0, int(self.max_detail_links))
        self.delay_before_return_html = max(0.0, float(self.delay_before_return_html))
        self.scroll_delay = max(0.0, float(self.scroll_delay))
        self.max_scroll_steps = max(0, int(self.max_scroll_steps))
        self.max_retries = max(0, int(self.max_retries))
        self.retry_backoff = max(0.0, float(self.retry_backoff))
        self.per_host_min_interval = max(0.0, float(self.per_host_min_interval))
        self.per_host_concurrency = max(1, int(self.per_host_concurrency))
        self.browser_max_pages = max(1, int(self.browser_max_pages))
        if self.wait_until not in {"load", "networkidle"}:
            self.wait_until = "load"


@dataclass
class QBitConfig:
    host: str = "http://192.168.1.100:8080"
    username: str = "admin"
    password: str = "adminadmin"
    fs_base_path: str = ""
    auto_create_dirs: bool = True


@dataclass
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8899


# ── 主配置 ──────────────────────────────


class Settings(BaseSettings):
    QBIT_HOST: str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"
    QBIT_SYNC_INTERVAL: float = 2.0

    SERVICE_HOST: str = "127.0.0.1"
    SERVICE_PORT: int = 8899

    CRAWLER_TIMEOUT: int = 30
    CRAWLER_MAX_DEPTH: int = 2
    CRAWLER_CONCURRENCY: int = 6
    CRAWLER_MAX_DETAIL_LINKS: int = 200
    CRAWLER_HEADLESS: bool = True
    CRAWLER_HTTP_FIRST: bool = True
    CRAWLER_DISABLE_RESOURCES: bool = True
    CRAWLER_BLOCK_ADS: bool = True
    # 空 = 不做分辨率过滤（详情页 1080p/720p 也要收，多条全收）
    CRAWLER_ALLOWED_RESOLUTIONS: str = ""
    CRAWLER_WAIT_UNTIL: str = "load"
    CRAWLER_DELAY_BEFORE_HTML: float = 1.0
    CRAWLER_SCAN_FULL_PAGE: bool = True
    CRAWLER_SCROLL_DELAY: float = 0.2
    CRAWLER_MAX_SCROLL_STEPS: int = 8
    CRAWLER_PROCESS_IFRAMES: bool = True
    CRAWLER_FLATTEN_SHADOW_DOM: bool = True
    CRAWLER_REMOVE_OVERLAYS: bool = True
    CRAWLER_REMOVE_CONSENT_POPUPS: bool = True
    CRAWLER_MAX_RETRIES: int = 1
    CRAWLER_RETRY_BACKOFF: float = 0.4
    CRAWLER_CHECK_ROBOTS_TXT: bool = False
    CRAWLER_BROWSER_FALLBACK: bool = True
    CRAWLER_HTTP_IMPERSONATE: str = "chrome"
    CRAWLER_PER_HOST_MIN_INTERVAL: float = 0.15
    CRAWLER_PER_HOST_CONCURRENCY: int = 4
    CRAWLER_BROWSER_MAX_PAGES: int = 2
    CRAWLER_ALLOW_FAKE_IP: bool = False  # mihomo/Clash fake-IP(198.18.0.0/15) SSRF 豁免

    FS_BASE_PATH: str = ""  # 脚本可创建目录的真实路径（如 Z:\downloads），为空则跳过 mkdir

    MIN_DISK_SPACE_GB: float = 10.0
    AUTO_CREATE_DIRS: bool = True

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = ""  # 留空仅控制台输出；填写路径则同时输出到滚动文件

    # ── 存储 ──
    # 采集中心默认持久化：重启后资源库、抓取任务、上报幂等记录都还在
    STORE_BACKEND: str = "sqlite"  # "memory" 或 "sqlite"
    STORE_DB_PATH: str = "data/magnet_items.db"

    # 采集任务：同时最多跑几个网址、被打断后最多自动重试几次
    INGEST_JOB_CONCURRENCY: int = 2
    INGEST_JOB_MAX_ATTEMPTS: int = 2

    API_KEY: str = ""  # 程序通道（Mac 代理等）的 X-API-Key；浏览器永远不保存它
    ALLOW_INSECURE_WRITE_API: bool = False
    # 授权模型（N23）：trusted = 局域网内页面直接读写（默认，升级即用）；
    # strict = 写接口必须带正确 X-API-Key（给未来暴露到局域网之外的部署）。
    # 两种模式都校验 Host / Origin / Sec-Fetch-Site，见 utils/auth.py。
    AUTH_MODE: str = "trusted"
    # 额外允许的 Host（逗号分隔，例如 nas.example.com）。IP、localhost、
    # 单标签主机名与 *.local 默认已允许；反向代理/域名访问时把域名填在这里。
    TRUSTED_HOSTS: str = ""
    CORS_ALLOWED_ORIGINS: str = ""  # 为空则禁用 CORS（只允许同域），逗号分隔多个域名

    SITE_COOKIES: str = "{}"  # JSON: {"domain": "cookie1=val1; cookie2=val2"}

    # 配置文件的指针（不是配置值本身）：容器内由 compose 指定 ENV_FILE=/config/.env，
    # 本地开发默认仓库根的 .env。除这一个指针外，compose 不再向进程环境注入任何配置键。
    model_config = {
        "env_file": os.environ.get("ENV_FILE")
        or str(Path(__file__).resolve().parent.parent / ".env"),
        "env_file_encoding": "utf-8",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """合并进程环境与配置文件，落实上面那套优先级。

        pydantic-settings 默认把环境排在文件前面；这里把「界面可写键」翻过来，
        并让部署键继续由环境（compose）钉死。合并后的来源仍排在 init 参数之后，
        所以显式传参（测试、CLI）依旧最高优先级。
        """

        def merged_source() -> dict[str, object]:
            env_values = dict(env_settings() or {})
            file_values = dict(dotenv_settings() or {})
            merged: dict[str, object] = dict(env_values)
            for key, value in file_values.items():
                if key in DEPLOYMENT_PINNED_KEYS:
                    merged.setdefault(key, value)  # 环境优先，文件只补缺
                else:
                    merged[key] = value  # 文件优先（含界面可写键）
            return merged

        return (init_settings, merged_source, file_secret_settings)

    def __init__(self, **kwargs):
        # 记住显式传入的 _env_file（测试/CLI 用），写盘时也要写回同一份文件
        env_file_override = kwargs.get("_env_file")
        super().__init__(**kwargs)
        self._env_file_override = str(env_file_override) if env_file_override else None
        self._qbit_config: Optional[QBitConfig] = None
        self._crawler_config: Optional[CrawlerConfig] = None
        self._service_config: Optional[ServiceConfig] = None

    # ── 配置事实源自检（启动日志 + 证据） ──────────────────────────────

    @property
    def config_file(self) -> Path:
        """当前生效的配置文件（容器内 /config/.env）。"""
        configured = (
            getattr(self, "_env_file_override", None) or self.model_config.get("env_file") or ".env"
        )
        return Path(str(configured)).expanduser()

    def describe_config_source(self) -> dict[str, object]:
        """配置事实源的可核对快照——不含任何密钥值，可以安全写进日志/报告。"""
        path = self.config_file
        app_root = Path(__file__).resolve().parent.parent
        file_values = self._read_env_file(path)
        uid = os.getuid() if hasattr(os, "getuid") else -1
        gid = os.getgid() if hasattr(os, "getgid") else -1
        sources = {}
        for key in sorted(FILE_AUTHORITATIVE_KEYS):
            if key in file_values:
                sources[key] = "file"
            elif key in os.environ:
                sources[key] = "env"
            else:
                sources[key] = "default"
        try:
            # tmp+rename 需要目录可写；文件不存在时看目录
            writable = os.access(path if path.exists() else path.parent, os.W_OK)
        except OSError:
            writable = False
        try:
            in_code_layer = path.resolve().is_relative_to(app_root)
        except OSError:
            in_code_layer = False
        try:
            offset = datetime.now().astimezone().utcoffset()
            tz_offset = int(offset.total_seconds()) if offset else 0
        except Exception:  # 诊断不能反过来把启动弄挂
            tz_offset = 0
        return {
            "file": str(path),
            "exists": path.exists(),
            "writable": writable,
            "uid": uid,
            "gid": gid,
            "in_code_layer": in_code_layer,
            "sources": sources,
            "tz_offset_seconds": tz_offset,
        }

    def log_config_source(self) -> dict[str, object]:
        """把「这份进程到底在读哪个文件、哪个键来自哪里」打进启动日志。

        这一行是 ②③ 的现场证据：重建/重启后看一眼 docker logs 就知道配置
        是不是还落在同一份挂载文件上，以及有没有进程环境在抢优先级。
        """
        info = self.describe_config_source()
        log.info(
            "配置事实源 %s（存在=%s 可写=%s uid=%s gid=%s 来源=%s）",
            info["file"],
            "是" if info["exists"] else "否",
            "是" if info["writable"] else "否",
            info["uid"],
            info["gid"],
            ",".join(f"{k}={v}" for k, v in info["sources"].items()),
        )
        if info["in_code_layer"]:
            log.warning(
                "配置文件在代码目录（镜像层）里：重建镜像即丢。请把 ENV_FILE 指到挂载卷（/config/.env）。"
            )
        if not info["writable"]:
            log.warning(
                "配置文件不可写：界面「保存并测试」会失败。%s 要归容器运行用户 uid=%s gid=%s（目录 700）。",
                info["file"],
                EXPECTED_UID,
                EXPECTED_GID,
            )
        if _IN_CONTAINER and info["uid"] != EXPECTED_UID:
            log.warning(
                "容器内运行身份 uid=%s 与部署约定 uid=%s 不一致：/config、/data 上写盘会 Permission denied。",
                info["uid"],
                EXPECTED_UID,
            )
        from_env = [key for key, source in info["sources"].items() if source == "env"]
        if from_env:
            log.info(
                "%s 目前由进程环境提供（配置文件里没有）。界面保存后会写进文件，此后文件永远优先。",
                ",".join(from_env),
            )
        if info["tz_offset_seconds"] != 0:
            log.warning(
                "容器 TZ 偏移 %+d 秒：数据时间戳必须是 UTC-aware（约定 §1）。"
                "naive-local 时间会随 TZ 漂移，部署默认把 TZ 钉成 UTC。",
                info["tz_offset_seconds"],
            )
        return info

    @classmethod
    def _read_env_file(cls, path: Path) -> dict[str, str]:
        """读简单 KEY=VALUE（与 _write_env_values 的解析范围一致），值原样保留。"""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return {}
        values: dict[str, str] = {}
        for line in lines:
            key = cls._env_line_key(line)
            if key:
                values[key] = line.split("=", 1)[1]
        return values

    @property
    def qbit(self) -> QBitConfig:
        if self._qbit_config is None:
            self._qbit_config = QBitConfig(
                host=self.QBIT_HOST,
                username=self.QBIT_USERNAME,
                password=self.QBIT_PASSWORD,
                fs_base_path=self.FS_BASE_PATH,
                auto_create_dirs=self.AUTO_CREATE_DIRS,
            )
        return self._qbit_config

    @property
    def crawler(self) -> CrawlerConfig:
        if self._crawler_config is None:
            self._crawler_config = CrawlerConfig(
                timeout=self.CRAWLER_TIMEOUT,
                max_depth=self.CRAWLER_MAX_DEPTH,
                concurrency=self.CRAWLER_CONCURRENCY,
                max_detail_links=self.CRAWLER_MAX_DETAIL_LINKS,
                headless=self.CRAWLER_HEADLESS,
                http_first=self.CRAWLER_HTTP_FIRST,
                disable_resources=self.CRAWLER_DISABLE_RESOURCES,
                block_ads=self.CRAWLER_BLOCK_ADS,
                allowed_resolutions=self._parse_csv_tuple(
                    self.CRAWLER_ALLOWED_RESOLUTIONS, default=()
                ),
                wait_until=self.CRAWLER_WAIT_UNTIL,
                delay_before_return_html=self.CRAWLER_DELAY_BEFORE_HTML,
                scan_full_page=self.CRAWLER_SCAN_FULL_PAGE,
                scroll_delay=self.CRAWLER_SCROLL_DELAY,
                max_scroll_steps=self.CRAWLER_MAX_SCROLL_STEPS,
                process_iframes=self.CRAWLER_PROCESS_IFRAMES,
                flatten_shadow_dom=self.CRAWLER_FLATTEN_SHADOW_DOM,
                remove_overlay_elements=self.CRAWLER_REMOVE_OVERLAYS,
                remove_consent_popups=self.CRAWLER_REMOVE_CONSENT_POPUPS,
                max_retries=self.CRAWLER_MAX_RETRIES,
                retry_backoff=self.CRAWLER_RETRY_BACKOFF,
                check_robots_txt=self.CRAWLER_CHECK_ROBOTS_TXT,
                browser_fallback=self.CRAWLER_BROWSER_FALLBACK,
                http_impersonate=self.CRAWLER_HTTP_IMPERSONATE,
                per_host_min_interval=self.CRAWLER_PER_HOST_MIN_INTERVAL,
                per_host_concurrency=self.CRAWLER_PER_HOST_CONCURRENCY,
                browser_max_pages=self.CRAWLER_BROWSER_MAX_PAGES,
                allow_fake_ip=self.CRAWLER_ALLOW_FAKE_IP,
            )
        return self._crawler_config

    @property
    def service(self) -> ServiceConfig:
        if self._service_config is None:
            self._service_config = ServiceConfig(
                host=self.SERVICE_HOST,
                port=self.SERVICE_PORT,
            )
        return self._service_config

    def build_qbit_config(
        self,
        host: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> QBitConfig:
        """Build a fully validated candidate without mutating live settings."""
        candidate_host = self.QBIT_HOST if host is None else host.strip()
        candidate_username = self.QBIT_USERNAME if username is None else username.strip()
        candidate_password = self.QBIT_PASSWORD if password is None else password.strip()

        if not candidate_host:
            raise ValueError("qBittorrent 主机地址不能为空")
        if not candidate_host.startswith(("http://", "https://")):
            raise ValueError(
                f"非法的 qBittorrent 主机地址: {candidate_host}（必须以 http:// 或 https:// 开头）"
            )
        if not candidate_username:
            raise ValueError("用户名不能为空")
        if not candidate_password:
            raise ValueError("密码不能为空")

        return QBitConfig(
            host=candidate_host,
            username=candidate_username,
            password=candidate_password,
            fs_base_path=self.FS_BASE_PATH,
            auto_create_dirs=self.AUTO_CREATE_DIRS,
        )

    def commit_qbit_config(self, config: QBitConfig) -> None:
        self.QBIT_HOST = config.host
        self.QBIT_USERNAME = config.username
        self.QBIT_PASSWORD = config.password
        self._qbit_config = None

    def persist_qbit_config(self, config: QBitConfig, env_path: str | Path | None = None) -> None:
        """把 qBittorrent 连接设置写进唯一的那份配置文件（默认 config_file）。"""
        path = Path(env_path or self.config_file)
        updates = {
            "QBIT_HOST": config.host,
            "QBIT_USERNAME": config.username,
            "QBIT_PASSWORD": config.password,
        }
        self._write_env_values(path, updates)
        log.info("qBittorrent 配置已落盘：%s（600，同目录临时文件 + 原子替换）", path)

    def auth_mode_normalized(self) -> str:
        """AUTH_MODE 的唯一解释：只认 strict，其余一律 trusted（默认）。"""
        return "strict" if str(self.AUTH_MODE or "").strip().lower() == "strict" else "trusted"

    def validate_security_posture(self) -> None:
        """启动自检：先报配置事实源，再拒绝无鉴权的对外写接口。

        N23 起默认是 trusted（局域网内写接口直接放行）：这是用户定下的部署口径，
        不再因「有非 loopback 监听 + 无 API_KEY」拒绝启动；但必须把信任边界喊清楚，
        让部署的人知道写接口是靠 Host/Origin 防护 + 端口绑定圈住的。
        strict 模式保持原来的保守逻辑：非 loopback 且没有 API_KEY 时拒绝启动。
        """
        self.log_config_source()
        host = self.SERVICE_HOST.strip().lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False

        if self.auth_mode_normalized() == "trusted":
            if not loopback and not self.API_KEY.strip():
                log.warning(
                    "AUTH_MODE=trusted：写接口在局域网内直接放行（无 API_KEY）。"
                    "信任边界是端口绑定范围 + Host/Origin/Sec-Fetch-Site 校验；"
                    "要暴露到局域网之外，请设 AUTH_MODE=strict 并配置 API_KEY 与 TRUSTED_HOSTS。"
                )
            return

        if loopback or self.API_KEY.strip() or self.ALLOW_INSECURE_WRITE_API:
            return
        raise RuntimeError(
            "Refusing to expose unauthenticated write endpoints on a non-loopback address. "
            "Configure API_KEY or set ALLOW_INSECURE_WRITE_API=true for deliberate development use."
        )

    def check_disk_space(self) -> dict[str, object]:
        """Return disk usage for the configured download filesystem path."""
        path = self.FS_BASE_PATH.strip() or "."
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            log.warning("磁盘空间检查失败: %s", exc)
            return {
                "path": path,
                "error": str(exc),
                "min_free_gb": self.MIN_DISK_SPACE_GB,
                "low_space": False,
            }

        free_gb = self._bytes_to_gb(usage.free)
        return {
            "path": path,
            "total_gb": self._bytes_to_gb(usage.total),
            "used_gb": self._bytes_to_gb(usage.used),
            "free_gb": free_gb,
            "min_free_gb": self.MIN_DISK_SPACE_GB,
            "low_space": free_gb < self.MIN_DISK_SPACE_GB,
        }

    @staticmethod
    def _parse_csv_tuple(
        value: str, *, default: tuple[str, ...] = ("2160p", "4k")
    ) -> tuple[str, ...]:
        values = tuple(item.strip() for item in value.split(",") if item.strip())
        return values or default

    @staticmethod
    def _bytes_to_gb(value: int) -> float:
        return round(value / 1024**3, 2)

    @classmethod
    def _write_env_values(cls, path: Path, updates: dict[str, str]) -> None:
        try:
            lines = (
                path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
            )
        except OSError as exc:
            raise ConfigPersistError(cls._persist_hint(path, "读取现有配置失败", exc)) from exc
        remaining = dict(updates)
        rendered: list[str] = []

        for line in lines:
            key = cls._env_line_key(line)
            if key in remaining:
                newline = "\n" if line.endswith("\n") else ""
                rendered.append(f"{key}={cls._format_env_value(remaining.pop(key))}{newline}")
            else:
                rendered.append(line)

        if remaining:
            if rendered and not rendered[-1].endswith("\n"):
                rendered[-1] += "\n"
            for key, value in remaining.items():
                rendered.append(f"{key}={cls._format_env_value(value)}\n")

        if not rendered:
            rendered = [f"{k}={cls._format_env_value(v)}\n" for k, v in updates.items()]

        cls._atomic_write(path, "".join(rendered))

    @classmethod
    def _atomic_write(cls, path: Path, text: str) -> None:
        """同目录临时文件 → fsync → 原子 rename，权限固定 600。

        三条都是现场踩出来的：
        · 必须与目标同目录：跨目录 rename 不是原子替换；
        · 权限必须显式 600：mkstemp 之后按 umask 会是 600 没错，但历史上
          write_text 走的是 644，密钥对同机其他用户可读；
        · 目录 fsync：断电/崩溃后 rename 不会退回旧内容（尽力而为）。
        单文件 bind mount 上的 rename 会 EBUSY，所以部署侧挂的是整个 config/ 目录。
        """
        directory = path.parent
        fd: int | None = None
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
            tmp_path = Path(tmp_name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
            try:
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass  # 目录 fsync 在部分文件系统/平台上不支持，不影响正确性
        except OSError as exc:
            raise ConfigPersistError(
                cls._persist_hint(path, exc.strerror or "写入失败", exc)
            ) from exc
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _persist_hint(path: Path, reason: str, exc: OSError | None = None) -> str:
        uid = os.getuid() if hasattr(os, "getuid") else "?"
        gid = os.getgid() if hasattr(os, "getgid") else "?"
        parent = path.parent
        try:
            mode = oct(parent.stat().st_mode & 0o7777)
        except OSError:
            mode = "目录不存在"
        errno_text = f"，errno={exc.errno}" if exc is not None and exc.errno else ""
        return (
            f"{reason}：{path}{errno_text}。当前进程 uid={uid} gid={gid}，"
            f"目录 {parent} 权限 {mode}。部署要求：该目录对容器内 uid={EXPECTED_UID} "
            f"gid={EXPECTED_GID} 可写（compose 把 config/ 目录挂到 /config，权限 700）。"
        )

    @staticmethod
    def _env_line_key(line: str) -> str | None:
        # 解析范围：仅处理简单 KEY=VALUE 行。
        # 不覆盖：export 前缀、引号包裹值、行内注释（# in value）、
        # 多行值、变量展开（$VAR / ${VAR}）等高级 dotenv 语法。
        # 这些场景在当前项目中不存在，保持简单即可。
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            return None
        if stripped.startswith("export "):
            return None  # 不覆盖 export 前缀行（与注释一致）
        left, _, _ = stripped.partition("=")
        parts = left.strip().split()
        key = parts[-1] if parts else ""
        if not key or not key.replace("_", "").isalnum():
            return None
        return key

    @staticmethod
    def _format_env_value(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
            .replace("$", "\\$")
        )
        return f'"{escaped}"'


settings = Settings()
