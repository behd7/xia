import Phaser from "phaser";
import { api } from "../api/client";
import { store } from "../systems/store";
import { LocalGameClock } from "../systems/clock";
import { promptNewGame } from "../ui/NewGame";
import { ShopPanel } from "../ui/Shop";
import { CharacterPanel } from "../ui/CharacterPanel";
import { InventoryPanel } from "../ui/InventoryPanel";
import { SkillsPanel } from "../ui/SkillsPanel";
import { JoinSectPanel } from "../ui/JoinSectPanel";
import { Hud, UI_FONT } from "../ui/Hud";
import { BATTLE_KEY } from "./BattleScene";

export const FIELD_KEY = "field";

const TILE = 16;
const MAP_COLS = 100;
const MAP_ROWS = 60;
const WORLD_W = MAP_COLS * TILE;
const WORLD_H = MAP_ROWS * TILE;
const PLAYER_SPEED = 150;

const T = {
  GRASS: 1,
  ROAD: 2,
  BUILDING: 3,
  WATER: 4,
  TREE: 5,
  CLIFF: 6,
  SAND: 7,
  WALL: 8,
  WELL: 9,
  TOWN: 10,
} as const;
const SOLID_TILES = [T.BUILDING, T.WATER, T.TREE, T.CLIFF, T.WALL, T.WELL];

interface Building {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  shop?: string;
  sect?: string;
}

const BUILDINGS: Building[] = [
  { x: 4, y: 4, w: 10, h: 6, label: "剑阳道馆", sect: "jianyang" },
  { x: 18, y: 4, w: 10, h: 6, label: "玄铁堂", sect: "xuantie" },
  { x: 32, y: 4, w: 10, h: 6, label: "烟雨小筑", sect: "yanyu" },
  { x: 4, y: 16, w: 8, h: 6, label: "药铺", shop: "clinic" },
  { x: 15, y: 16, w: 8, h: 6, label: "铁匠铺", shop: "smith" },
  { x: 26, y: 16, w: 12, h: 8, label: "客栈" },
  { x: 80, y: 18, w: 10, h: 7, label: "破庙" },
];

const POI_RECTS: { name: string; x: number; y: number; w: number; h: number }[] = [
  ...BUILDINGS.map((b) => ({ name: b.label, x: b.x, y: b.y, w: b.w, h: b.h })),
  { name: "练武场", x: 52, y: 5, w: 18, h: 12 },
  { name: "镇口", x: 42, y: 22, w: 14, h: 8 },
  { name: "东山林地", x: 0, y: 32, w: 47, h: 28 },
  { name: "后山崖", x: 53, y: 32, w: 47, h: 28 },
  { name: "清溪镇", x: 1, y: 2, w: 97, h: 28 },
];

// Shop interaction uses the building footprint plus a 2-tile door margin:
// the buildings themselves are solid tiles the player can never stand on.
const SHOP_AREAS: { shop: string; label: string; x: number; y: number; w: number; h: number }[] =
  BUILDINGS.filter((b) => b.shop).map((b) => ({
    shop: b.shop!,
    label: b.label,
    x: b.x - 2,
    y: b.y - 2,
    w: b.w + 4,
    h: b.h + 4,
  }));

const SECT_AREAS: { sect: string; label: string; x: number; y: number; w: number; h: number }[] =
  BUILDINGS.filter((b) => b.sect).map((b) => ({
    sect: b.sect!,
    label: b.label,
    x: b.x - 2,
    y: b.y - 2,
    w: b.w + 4,
    h: b.h + 4,
  }));

function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildMap(): number[][] {
  const grid: number[][] = Array.from({ length: MAP_ROWS }, () => Array(MAP_COLS).fill(T.GRASS));

  for (let y = 2; y <= 29; y++) for (let x = 1; x <= 98; x++) grid[y][x] = T.TOWN;
  for (let x = 2; x <= 97; x++) {
    grid[12][x] = T.ROAD;
    grid[13][x] = T.ROAD;
  }
  for (let y = 12; y <= 38; y++) for (let x = 48; x <= 51; x++) grid[y][x] = T.ROAD;
  for (let x = 8; x <= 90; x++) {
    grid[38][x] = T.ROAD;
    grid[39][x] = T.ROAD;
  }

  for (const b of BUILDINGS) {
    for (let y = b.y; y < b.y + b.h; y++) {
      for (let x = b.x; x < b.x + b.w; x++) grid[y][x] = T.BUILDING;
    }
  }

  for (let y = 5; y <= 16; y++) for (let x = 52; x <= 69; x++) grid[y][x] = T.SAND;
  for (let y = 24; y <= 27; y++) for (let x = 72; x <= 78; x++) grid[y][x] = T.WATER;
  grid[25][44] = T.WELL;
  grid[25][45] = T.WELL;
  grid[26][44] = T.WELL;
  grid[26][45] = T.WELL;

  for (let y = 30; y <= 31; y++) for (let x = 0; x <= 99; x++) grid[y][x] = T.WALL;
  for (let y = 30; y <= 31; y++) for (let x = 48; x <= 51; x++) grid[y][x] = T.ROAD;

  const treeRnd = mulberry32(20261016);
  for (let y = 34; y <= 58; y++) {
    for (let x = 1; x <= 45; x++) {
      if (grid[y][x] === T.GRASS && treeRnd() < 0.13) grid[y][x] = T.TREE;
    }
  }
  const rockRnd = mulberry32(4396);
  for (let y = 34; y <= 58; y++) {
    for (let x = 54; x <= 98; x++) {
      if (grid[y][x] === T.GRASS && (y >= 51 || rockRnd() < 0.09)) grid[y][x] = T.CLIFF;
    }
  }
  return grid;
}

interface MobSprite {
  sprite: Phaser.Physics.Arcade.Sprite;
  label: Phaser.GameObjects.Text;
}

export class FieldScene extends Phaser.Scene {
  private player!: Phaser.Physics.Arcade.Sprite;
  private playerLabel!: Phaser.GameObjects.Text;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasd!: Record<"W" | "A" | "S" | "D", Phaser.Input.Keyboard.Key>;
  private keyE!: Phaser.Input.Keyboard.Key;
  private keyEsc!: Phaser.Input.Keyboard.Key;
  private keyI!: Phaser.Input.Keyboard.Key;
  private keyB!: Phaser.Input.Keyboard.Key;
  private keyK!: Phaser.Input.Keyboard.Key;
  private hud!: Hud;
  private clock = new LocalGameClock();
  private poi = "";
  private poiTimer = 0;
  private mobSprites = new Map<string, MobSprite>();
  private mobCollider: Phaser.Physics.Arcade.Collider | null = null;
  private battleLock = false;
  private booted = false;
  private immunityUntil = 0;
  private panel: { close(): void } | null = null;

  constructor() {
    super(FIELD_KEY);
  }

  init(data: { fledFrom?: string }): void {
    this.battleLock = false;
    this.mobSprites = new Map();
    this.mobCollider = null;
    this.panel = null;
    this.poi = "";
    if (data?.fledFrom) {
      this.events.once("postcreate", () => this.pushAwayFrom(data.fledFrom!));
    }
  }

  create(): void {
    const map = this.make.tilemap({ data: buildMap(), tileWidth: TILE, tileHeight: TILE });
    const tileset = map.addTilesetImage("tiles", "tiles", TILE, TILE, 0, 0)!;
    const layer = map.createLayer(0, tileset, 0, 0)!;
    layer.setCollision(SOLID_TILES);

    this.physics.world.setBounds(0, 0, WORLD_W, WORLD_H);

    const spawn = store.player?.pos ?? { x: 792, y: 440 };
    this.player = this.physics.add.sprite(spawn.x, spawn.y, "player");
    this.player.setCollideWorldBounds(true);
    this.player.setBodySize(12, 12);
    this.physics.add.collider(this.player, layer);

    this.playerLabel = this.add
      .text(0, 0, "侠", {
        fontFamily: UI_FONT,
        fontSize: "13px",
        color: "#f5f3ec",
        stroke: "#16181d",
        strokeThickness: 3,
      })
      .setOrigin(0.5)
      .setDepth(30);

    this.placeLabels();

    this.cameras.main.setBounds(0, 0, WORLD_W, WORLD_H);
    this.cameras.main.startFollow(this.player, true, 0.12, 0.12);
    this.cameras.main.fadeIn(250);

    this.hud = new Hud(this);

    const kb = this.input.keyboard!;
    this.cursors = kb.createCursorKeys();
    this.wasd = kb.addKeys("W,A,S,D") as Record<
      "W" | "A" | "S" | "D",
      Phaser.Input.Keyboard.Key
    >;
    this.keyE = kb.addKey("E");
    this.keyEsc = kb.addKey("ESC");
    this.keyI = kb.addKey("I");
    this.keyB = kb.addKey("B");
    this.keyK = kb.addKey("K");

    this.syncMobs();
    this.time.addEvent({ delay: 10000, loop: true, callback: () => void this.pollRefresh() });

    void this.bootstrap();
  }

  private placeLabels(): void {
    const labelStyle = (size: number): Phaser.Types.GameObjects.Text.TextStyle => ({
      fontFamily: UI_FONT,
      fontSize: `${size}px`,
      color: "#e8e6e0",
      stroke: "#16181d",
      strokeThickness: 4,
    });
    for (const b of BUILDINGS) {
      this.add
        .text((b.x + b.w / 2) * TILE, (b.y + b.h / 2) * TILE, b.label, labelStyle(14))
        .setOrigin(0.5)
        .setDepth(10);
    }
    this.add.text(61 * TILE, 11 * TILE, "练武场", labelStyle(16)).setOrigin(0.5).setDepth(10);
    this.add.text(45 * TILE, 23.6 * TILE, "水井", labelStyle(12)).setOrigin(0.5).setDepth(10);
    for (const [x, y, text] of [
      [50, 3.2, "清溪镇"],
      [23, 36, "东山林地"],
      [76, 36, "后山崖"],
    ] as const) {
      this.add
        .text(x * TILE, y * TILE, text, labelStyle(20))
        .setOrigin(0.5)
        .setDepth(10)
        .setAlpha(0.8);
    }
  }

  private async bootstrap(): Promise<void> {
    let state;
    try {
      state = await store.refresh();
      this.clock.anchor(
        state.clock.game_sec,
        state.clock.real_ts,
        state.clock.time_scale,
      );
    } catch {
      this.hud.setNet("服务器 未连接");
      return;
    }

    if (!store.player) {
      store.uiBlocked = true;
      const name = await promptNewGame();
      try {
        await api.newGame(name);
        state = await store.refresh();
        this.clock.anchor(state.clock.game_sec, state.clock.real_ts, state.clock.time_scale);
      } catch (e) {
        this.hud.setNet(`开档失败：${e instanceof Error ? e.message : "未知错误"}`);
      } finally {
        store.uiBlocked = false;
      }
    }

    if (store.player) {
      const p = store.player;
      this.player.setPosition(p.pos.x, p.pos.y);
      this.hud.update(p);
      this.syncMobs();
    }

    let net = "服务器 已连接";
    try {
      const health = await api.health();
      const o = health.ollama;
      if (!o.reachable) net += " · Ollama 不可达";
      else if (!o.chat_model_ok || !o.embed_model_ok) net += " · Ollama 模型缺失";
      else net += " · Ollama 就绪";
    } catch {
      /* keep the plain connected state */
    }
    this.hud.setNet(net);
    this.booted = true;

    if (state.battle) {
      this.scene.start(BATTLE_KEY, { battle: state.battle, player: state.player });
    }
  }

  private async pollRefresh(): Promise<void> {
    if (this.battleLock || store.uiBlocked) return;
    try {
      const s = await store.refresh();
      this.clock.anchor(s.clock.game_sec, s.clock.real_ts, s.clock.time_scale);
      this.hud.update(store.player);
      this.syncMobs();
    } catch {
      /* transient network error, next poll retries */
    }
  }

  private syncMobs(): void {
    const alive = new Map(store.mobs.filter((m) => m.alive).map((m) => [m.id, m]));
    for (const [id, entry] of this.mobSprites) {
      if (!alive.has(id)) {
        entry.sprite.destroy();
        entry.label.destroy();
        this.mobSprites.delete(id);
      }
    }
    for (const [id, m] of alive) {
      if (this.mobSprites.has(id)) continue;
      const sprite = this.physics.add.sprite(m.x, m.y, m.type);
      sprite.setImmovable(true);
      this.physics.world.enableBody(sprite);
      sprite.body.allowGravity = false;
      sprite.body.setCircle(8);
      sprite.setData("mobId", id);
      sprite.setDepth(20);
      const label = this.add
        .text(m.x, m.y - 16, m.name, {
          fontFamily: UI_FONT,
          fontSize: m.type === "boss" ? "14px" : "12px",
          color: m.type === "boss" ? "#d8938c" : "#c9ccd4",
          stroke: "#16181d",
          strokeThickness: 3,
        })
        .setOrigin(0.5)
        .setDepth(21);
      this.mobSprites.set(id, { sprite, label });
    }
    if (this.mobCollider) this.mobCollider.destroy();
    const sprites = [...this.mobSprites.values()].map((e) => e.sprite);
    if (sprites.length > 0) {
      this.mobCollider = this.physics.add.overlap(
        this.player,
        sprites,
        (p, m) => this.onMobTouch(p as Phaser.Physics.Arcade.Sprite, m as Phaser.Physics.Arcade.Sprite),
      );
    } else {
      this.mobCollider = null;
    }
  }

  private pushAwayFrom(mobId: string): void {
    const m = store.mobs.find((x) => x.id === mobId);
    if (!m) return;
    const dx = this.player.x - m.x;
    const dy = this.player.y - m.y;
    const len = Math.hypot(dx, dy) || 1;
    this.player.setPosition(
      this.player.x + (dx / len) * 60,
      this.player.y + (dy / len) * 60,
    );
    this.immunityUntil = this.time.now + 2500;
  }

  private async onMobTouch(_p: Phaser.Physics.Arcade.Sprite, mob: Phaser.Physics.Arcade.Sprite): Promise<void> {
    if (!this.booted || this.battleLock || this.time.now < this.immunityUntil || store.uiBlocked || !store.player) {
      return;
    }
    this.battleLock = true;
    this.player.setVelocity(0, 0);
    const mobId = mob.getData("mobId") as string;
    try {
      const resp = await api.battleStart(mobId, { x: this.player.x, y: this.player.y });
      this.cameras.main.fadeOut(300);
      this.cameras.main.once("camerafadeoutcomplete", () => {
        this.scene.start(BATTLE_KEY, { battle: resp.battle, player: resp.player });
      });
    } catch {
      this.battleLock = false;
    }
  }

  update(_time: number, delta: number): void {
    if (Phaser.Input.Keyboard.JustDown(this.keyEsc) && this.panel) {
      this.panel.close();
      this.panel = null;
      return;
    }

    if (!store.uiBlocked) {
      if (Phaser.Input.Keyboard.JustDown(this.keyI)) this.openPanel((onClose) => new CharacterPanel(this, onClose));
      if (Phaser.Input.Keyboard.JustDown(this.keyB)) this.openPanel((onClose) => new InventoryPanel(this, onClose));
      if (Phaser.Input.Keyboard.JustDown(this.keyK)) this.openPanel((onClose) => new SkillsPanel(this, onClose));
    }

    if (!this.panel && !store.uiBlocked) {
      const shopHere = this.shopNearby();
      const sectHere = shopHere ? null : this.sectNearby();
      if (shopHere) {
        this.hud.setHint(`${shopHere.label} · E 逛店　·　WASD 移动`);
        if (Phaser.Input.Keyboard.JustDown(this.keyE)) {
          void this.openShop(shopHere.shop);
          return;
        }
      } else if (sectHere) {
        this.hud.setHint(`${sectHere.label} · E 拜师　·　WASD 移动`);
        if (Phaser.Input.Keyboard.JustDown(this.keyE)) {
          this.openPanel((onClose) => new JoinSectPanel(this, sectHere.sect, onClose));
          return;
        }
      } else {
        this.hud.setHint("WASD / 方向键 移动　·　I 角色　B 背包　K 武学");
      }
    }

    let vx = 0;
    let vy = 0;
    if (!store.uiBlocked && !this.panel && !this.battleLock) {
      if (this.cursors.left.isDown || this.wasd.A.isDown) vx -= 1;
      if (this.cursors.right.isDown || this.wasd.D.isDown) vx += 1;
      if (this.cursors.up.isDown || this.wasd.W.isDown) vy -= 1;
      if (this.cursors.down.isDown || this.wasd.S.isDown) vy += 1;
      if (vx !== 0 && vy !== 0) {
        vx *= Math.SQRT1_2;
        vy *= Math.SQRT1_2;
      }
    }
    this.player.setVelocity(vx * PLAYER_SPEED, vy * PLAYER_SPEED);
    this.playerLabel.setPosition(this.player.x, this.player.y - 13);

    if (this.clock.ready) this.hud.setTime(this.clock.format());

    this.poiTimer -= delta;
    if (this.poiTimer <= 0) {
      this.poiTimer = 200;
      const name = this.currentPoi();
      if (name !== this.poi) {
        this.poi = name;
        this.hud.setPoi(name ? `地点 ${name}` : "地点 野外");
      }
    }
  }

  private openPanel(make: (onClose: () => void) => { close(): void }): void {
    if (this.panel) return;
    store.uiBlocked = true;
    this.panel = make(() => {
      this.panel = null;
      store.uiBlocked = false;
    });
  }

  private async openShop(shopId: string): Promise<void> {
    if (this.panel) return;
    store.uiBlocked = true;
    try {
      const info = await api.shop(shopId);
      this.panel = new ShopPanel(
        this,
        info,
        () => store.player?.gold ?? 0,
        async (itemId) => {
          try {
            const resp = await api.buy(shopId, itemId, 1, {
              x: this.player.x,
              y: this.player.y,
            });
            store.player = resp.player;
            this.hud.update(resp.player);
            return null;
          } catch (e) {
            return e instanceof Error ? e.message : "购买失败";
          }
        },
        async (itemId) => {
          try {
            const resp = await api.sell(shopId, itemId, 1);
            store.player = resp.player;
            this.hud.update(resp.player);
            return null;
          } catch (e) {
            return e instanceof Error ? e.message : "出售失败";
          }
        },
        () => {
          this.panel = null;
          store.uiBlocked = false;
        },
      );
    } catch (e) {
      store.uiBlocked = false;
      this.hud.setNet(`商店加载失败：${e instanceof Error ? e.message : "未知错误"}`);
    }
  }

  private sectNearby(): { sect: string; label: string } | null {
    const tx = Math.floor(this.player.x / TILE);
    const ty = Math.floor(this.player.y / TILE);
    for (const a of SECT_AREAS) {
      if (tx >= a.x && tx < a.x + a.w && ty >= a.y && ty < a.y + a.h) {
        return { sect: a.sect, label: a.label };
      }
    }
    return null;
  }

  private shopNearby(): { shop: string; label: string } | null {
    const tx = Math.floor(this.player.x / TILE);
    const ty = Math.floor(this.player.y / TILE);
    for (const a of SHOP_AREAS) {
      if (tx >= a.x && tx < a.x + a.w && ty >= a.y && ty < a.y + a.h) {
        return { shop: a.shop, label: a.label };
      }
    }
    return null;
  }

  private currentPoi(): string {
    const tx = Math.floor(this.player.x / TILE);
    const ty = Math.floor(this.player.y / TILE);
    for (const r of POI_RECTS) {
      if (tx >= r.x && tx < r.x + r.w && ty >= r.y && ty < r.y + r.h) return r.name;
    }
    return "";
  }
}
