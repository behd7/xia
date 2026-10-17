export interface ClockSnapshot {
  game_sec: number;
  day: number;
  hour: number;
  minute: number;
  time_scale: number;
  real_ts: number;
}

export interface InventoryItem {
  item_id: number;
  name: string;
  type: string;
  qty: number;
  slot: string | null;
  price: number;
  desc: string;
}

export interface SectView {
  id: string;
  name: string;
  rank: string;
}

export interface SkillView {
  skill_id: number;
  name: string;
  kind: string;
  tier: number;
  level: number;
  mp_cost: number;
  multiplier: number;
  effect_desc: string;
  equipped: boolean;
  locked: boolean;
  upgradable: boolean;
  upgrade_cost: number;
}

export interface PlayerState {
  id: number;
  name: string;
  sect: SectView | null;
  level: number;
  exp: number;
  exp_next: number;
  cultivation: number;
  gold: number;
  hp: number;
  mp: number;
  hp_max: number;
  mp_max: number;
  stats: { str: number; con: number; agi: number; int: number };
  free_points: number;
  atk: number;
  def: number;
  spd: number;
  dodge: number;
  crit: number;
  pos: { x: number; y: number };
  equipped: Record<"weapon" | "armor" | "accessory", InventoryItem | null>;
  skills: SkillView[];
  inventory: InventoryItem[];
}

export interface MobState {
  id: string;
  type: string;
  name: string;
  level: number;
  x: number;
  y: number;
  alive: boolean;
  respawn_at_real: number | null;
}

export interface BattleEnemy {
  id: string;
  type: string;
  name: string;
  level: number;
  hp: number;
  hp_max: number;
  atk: number;
  def: number;
  spd: number;
}

export interface BattlePlayerView {
  name: string;
  level: number;
  hp: number;
  mp: number;
  hp_max: number;
  mp_max: number;
}

export interface BattleBuffs {
  def_pct?: number;
  def_rounds?: number;
  dodge_add?: number;
  dodge_rounds?: number;
}

export interface BattleSnapshot {
  id: number;
  enemy: BattleEnemy;
  player: BattlePlayerView;
  player_first: boolean;
  round: number;
  potions_used: number;
  buffs: { player: BattleBuffs; enemy: BattleBuffs };
  skill_cd: Record<string, number>;
  active: boolean;
  result: string | null;
}

export interface LogEntry {
  kind: string;
  text: string;
  value?: number;
  crit?: boolean;
  dodged?: boolean;
  enemy_hp?: number;
  player_hp?: number;
  player_mp?: number;
}

export interface Rewards {
  exp: number;
  gold: number;
  cultivation: number;
  drops: { name: string; qty: number }[];
  level_ups: number[];
}

export interface BattleStartResp {
  battle: BattleSnapshot;
  player: PlayerState;
}

export interface BattleActionResp {
  battle: BattleSnapshot;
  log: LogEntry[];
  result: string | null;
  rewards: Rewards | null;
  player: PlayerState;
}

export interface GameStateResp {
  player: PlayerState | null;
  clock: ClockSnapshot;
  mobs: MobState[];
  battle: BattleSnapshot | null;
}

export interface ShopItem {
  item_id: number;
  name: string;
  type: string;
  tier: string;
  price: number;
  desc: string;
}

export interface ShopInfo {
  id: string;
  name: string;
  items: ShopItem[];
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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep status code */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<HealthResp>("GET", "/api/health"),
  gameState: () => request<GameStateResp>("GET", "/api/game/state"),
  newGame: (name: string) => request<{ player: PlayerState }>("POST", "/api/game/new", { name }),
  battleStart: (targetId: string, pos?: { x: number; y: number }) =>
    request<BattleStartResp>("POST", "/api/battle/start", {
      target_type: "mob",
      target_id: targetId,
      pos: pos ?? null,
    }),
  battleAction: (action: string, id?: number) =>
    request<BattleActionResp>("POST", "/api/battle/action", { action, id: id ?? null }),
  shop: (shopId: string) => request<ShopInfo>("GET", `/api/shop/${shopId}`),
  buy: (shopId: string, itemId: number, qty = 1, pos?: { x: number; y: number }) =>
    request<{ player: PlayerState }>("POST", `/api/shop/${shopId}/buy`, {
      item_id: itemId,
      qty,
      pos: pos ?? null,
    }),
  sell: (shopId: string, itemId: number, qty = 1) =>
    request<{ player: PlayerState; gain: number }>("POST", `/api/shop/${shopId}/sell`, {
      item_id: itemId,
      qty,
    }),
  allocate: (stats: Record<string, number>) =>
    request<{ player: PlayerState }>("POST", "/api/game/allocate", { stats }),
  equipItem: (itemId: number) =>
    request<{ player: PlayerState }>("POST", "/api/game/equip", { item_id: itemId }),
  unequipItem: (slot: string) =>
    request<{ player: PlayerState }>("POST", "/api/game/unequip", { slot }),
  skillEquip: (skillId: number, equip: boolean) =>
    request<{ player: PlayerState }>("POST", "/api/game/skill/equip", { skill_id: skillId, equip }),
  skillUpgrade: (skillId: number) =>
    request<{ player: PlayerState }>("POST", "/api/game/skill/upgrade", { skill_id: skillId }),
  joinSect: (sectId: string) =>
    request<{ player: PlayerState }>("POST", "/api/sect/join", { sect_id: sectId }),
};
