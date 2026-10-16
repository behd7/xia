export interface ClockSnapshot {
  game_sec: number;
  day: number;
  hour: number;
  minute: number;
  time_scale: number;
  real_ts: number;
}

export interface GameStateResp {
  player: null;
  clock: ClockSnapshot;
}

export interface HealthResp {
  status: string;
  db: boolean;
  ollama: {
    reachable: boolean;
    chat_model: string;
    chat_model_ok: boolean;
    embed_model: string;
    embed_model_ok: boolean;
  };
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  health: () => get<HealthResp>("/api/health"),
  gameState: () => get<GameStateResp>("/api/game/state"),
};
