from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── qBittorrent ───────────────────────────────────────
    QBIT_HOST:     str = "http://192.168.1.100:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"

    # ── MiniMax ───────────────────────────────────────────
    MINIMAX_API_KEY:        str  = "your-minimax-api-key"
    MINIMAX_MODEL:          str  = "MiniMax-M2.5-highspeed"
    MINIMAX_THINKING_MODEL: str  = "MiniMax-M2.5"
    THINKING_RECHECK:       bool = True

    # ── TTS ───────────────────────────────────────────────
    TTS_ENABLED: bool = True

    # ── 服务 ──────────────────────────────────────────────
    SERVICE_HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8899

    # ── 爬虫 ──────────────────────────────────────────────
    CRAWLER_TIMEOUT:     int  = 30
    CRAWLER_MAX_DEPTH:   int  = 2
    CRAWLER_CONCURRENCY: int  = 3
    CRAWLER_HEADLESS:    bool = True

    # ── 分类下载路径（每个分类独立字段，在 .env 中逐行覆盖）
    PATH_MOVIE:       str = "/volume1/downloads/movies"
    PATH_TV:          str = "/volume1/downloads/tv"
    PATH_ANIME:       str = "/volume1/downloads/anime"
    PATH_MUSIC:       str = "/volume1/downloads/music"
    PATH_GAME:        str = "/volume1/downloads/games"
    PATH_SOFTWARE:    str = "/volume1/downloads/software"
    PATH_VARIETY:     str = "/volume1/downloads/variety"
    PATH_DOCUMENTARY: str = "/volume1/downloads/documentary"
    PATH_OTHER:       str = "/volume1/downloads/others"

    @property
    def CATEGORY_PATHS(self) -> dict[str, str]:
        """动态合并分类→路径映射，每次调用都返回最新值"""
        return {
            "电影":   self.PATH_MOVIE,
            "电视剧": self.PATH_TV,
            "动漫":   self.PATH_ANIME,
            "音乐":   self.PATH_MUSIC,
            "游戏":   self.PATH_GAME,
            "软件":   self.PATH_SOFTWARE,
            "综艺":   self.PATH_VARIETY,
            "纪录片": self.PATH_DOCUMENTARY,
            "其他":   self.PATH_OTHER,
        }

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
