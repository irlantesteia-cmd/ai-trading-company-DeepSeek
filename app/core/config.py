from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Trading Company"
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_trading?ssl=disable"
    redis_url: str = "redis://127.0.0.1:6379/0"
    db_ping_interval_s: float = 60.0

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    binance_recv_window_ms: int = 5000
    binance_timeout_s: float = 15.0
    binance_max_retries: int = 3
    binance_time_sync_attempts: int = 2
    binance_ws_ping_interval_s: float = 20.0
    binance_ws_ping_timeout_s: float = 20.0
    binance_ws_reconnect_initial_s: float = 1.0
    binance_ws_reconnect_max_s: float = 30.0

    # Trading
    trading_symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    default_interval: str = "5m"
    default_market_type: str = "FUTURES"

    # Estratégia por símbolo. Chaves em maiúsculas (BTCUSDT etc.).
    # Valores aceitos: "momentum", "mean_reversion", "ml", "none".
    default_strategy: str = "momentum"
    strategy_per_symbol: dict[str, str] = {}

    # Risco
    risk_max_position_notional: float = 10_000.0
    risk_max_total_notional: float = 50_000.0
    risk_max_leverage: int = 10
    risk_max_daily_loss: float = 1_000.0
    risk_max_open_positions: int = 5
    risk_min_confidence: float = 0.5
    risk_correlated_groups: dict[str, list[str]] = {
        "crypto_majors": ["BTCUSDT", "ETHUSDT"],
    }

    # Sizing
    sizing_risk_per_trade_pct: float = 0.01
    sizing_default_stop_pct: float = 0.02

    # ML
    ml_autotrain_on_boot: bool = True
    ml_autotrain_symbols: list[str] = []   # vazio = usa trading_symbols
    ml_autotrain_limit: int = 500
    ml_horizon: int = 5

    # GitHub
    github_token: str = ""
    github_repo: str = ""
    github_api_url: str = "https://api.github.com"
    github_default_branch: str = "main"
    github_autonomy_enabled: bool = False

    # Runtime
    heartbeat_max_age_s: float = 120.0
    heartbeat_interval_s: float = 30.0
    health_check_interval_s: float = 60.0
    reconcile_interval_s: float = 300.0
    evolution_interval_s: float = 3600.0
    evolution_cooldown_s: float = 86400.0


settings = Settings()