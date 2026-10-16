from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # Tunables mirror docs/DESIGN.md §14; names match the .env keys there.
    model_config = SettingsConfigDict(
        env_file=str(SERVER_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama (docs/DESIGN.md §7.1)
    ollama_host: str = "http://localhost:11434"
    chat_model: str = "qwen3:4b-instruct"
    embed_model: str = "qwen3-embedding:0.6b"
    llm_timeout: float = 20.0
    llm_concurrency: int = 2
    max_dialogue_delta: int = 3

    # Game clock: 60 means 1 real second = 1 game minute.
    time_scale: int = 60
    game_start_sec: int = 7 * 3600

    # Affinity / NPC (docs/DESIGN.md §6.5)
    greet_threshold: int = 70
    greet_cooldown_real: int = 300

    # Background agent jobs, in real seconds (docs/DESIGN.md §6.7/§6.9)
    reflect_mem_threshold: int = 8
    reflect_interval_real: int = 360
    gossip_interval_real: int = 120

    # RAG (docs/DESIGN.md §6.4)
    recency_half_life: float = 3.0
    rag_top_k: int = 6
    w_rel: float = 1.0
    w_rec: float = 0.8
    w_imp: float = 0.5

    # Storage
    db_path: str = "data/xia.db"
    chroma_dir: str = "data/chroma"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def db_path_abs(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else SERVER_ROOT / p

    @property
    def chroma_dir_abs(self) -> Path:
        p = Path(self.chroma_dir)
        return p if p.is_absolute() else SERVER_ROOT / p


settings = Settings()
