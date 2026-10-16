import { api, GameStateResp, MobState, PlayerState } from "../api/client";

class Store {
  player: PlayerState | null = null;
  mobs: MobState[] = [];
  uiBlocked = false;

  async refresh(): Promise<GameStateResp> {
    const s = await api.gameState();
    this.player = s.player;
    this.mobs = s.mobs;
    return s;
  }
}

export const store = new Store();
