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
    # Senha do role somente-leitura `grafana_ro` (migration 0006; datasource do Grafana).
    grafana_db_password: str = "grafana_ro"
    db_ping_interval_s: float = 60.0

    # Binance (demo.binance.com)
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    binance_recv_window_ms: int = 5000
    binance_timeout_s: float = 15.0
    binance_max_retries: int = 3
    binance_time_sync_attempts: int = 2
    binance_max_rtt_ms_for_sync: float = 3000.0
    binance_ws_ping_interval_s: float = 20.0
    binance_ws_ping_timeout_s: float = 20.0
    binance_ws_reconnect_initial_s: float = 1.0
    binance_ws_reconnect_max_s: float = 30.0

    # Order fill polling
    order_fill_poll_attempts: int = 5
    order_fill_poll_interval_s: float = 0.5

    # Trading
    trading_symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    default_interval: str = "5m"
    default_market_type: str = "FUTURES"

    # Estratégia
    default_strategy: str = "momentum"
    strategy_per_symbol: dict[str, str] = {}

    # Candle stream
    candle_stream_enabled: bool = True
    candle_stream_interval_s: float = 30.0

    # Auto-execução (default OFF)
    signal_auto_execution_enabled: bool = False

    # Boot cleanup (dev/test)
    close_positions_on_boot: bool = False

    # Ao fechar posição, cancelar SL/TP pendentes antes (evita órfãs).
    cancel_protective_orders_on_close: bool = True

    # Quando um SL/TP dispara, cancelar o(s) irmão(s) pendente(s) do mesmo
    # símbolo. Sem isso, o irmão fica órfão até o próximo close/reconcile.
    cancel_sibling_on_protective_fill: bool = True

    # Ordens protetivas (STOP_MARKET / TAKE_PROFIT_MARKET após fill)
    stop_loss_enabled: bool = False
    take_profit_enabled: bool = False

    # User Data Stream
    user_stream_enabled: bool = True
    user_stream_keepalive_interval_s: float = 1800.0
    user_stream_reconnect_initial_s: float = 1.0
    user_stream_reconnect_max_s: float = 30.0
    user_stream_ping_interval_s: float = 20.0

    # ML thresholds
    ml_long_threshold: float = 0.60
    ml_short_threshold: float = 0.40

    # Risco
    risk_max_position_notional: float = 10_000.0
    risk_max_total_notional: float = 50_000.0
    risk_max_leverage: int = 10
    risk_max_daily_loss: float = 1_000.0
    risk_max_open_positions: int = 5
    # Máximo de posições abertas na **mesma direção** (LONG ou SHORT).
    # Evita concentração direcional quando vários símbolos disparam sinal
    # no mesmo ciclo.
    risk_max_same_direction: int = 2
    risk_min_confidence: float = 0.5
    risk_correlated_groups: dict[str, list[str]] = {"crypto_majors": ["BTCUSDT", "ETHUSDT"]}

    # Sizing
    sizing_risk_per_trade_pct: float = 0.01
    sizing_default_stop_pct: float = 0.02

    # ML
    ml_autotrain_on_boot: bool = True
    ml_autotrain_symbols: list[str] = []
    ml_autotrain_limit: int = 500
    ml_horizon: int = 5
    ml_min_deploy_auc: float = 0.5
    ml_label_min_return_pct: float = 0.0
    ml_boot_model_grace_seconds: float = 120.0
    # Versões mantidas em models/ por símbolo/horizonte após cada treino
    # (+ o deployable mais recente). 0 desliga a retenção.
    ml_model_retention: int = 5
    # Walk-forward validation (ML-3b)
    ml_walk_forward_folds: int = 5
    ml_walk_forward_max_std: float = 0.10
    ml_walk_forward_min_train: int = 100
    # Cross-asset features (ML-3d): símbolo principal → símbolo de referência.
    # Vazio = desabilitado. Ex.: {"SOLUSDT": "BTCUSDT", "XRPUSDT": "BTCUSDT"}.
    ml_cross_asset_ref: dict[str, str] = {}
    ml_cross_asset_ref_horizons: list[int] = [1, 3, 5]

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