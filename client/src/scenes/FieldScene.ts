import Phaser from "phaser";
import { api } from "../api/client";
import { LocalGameClock } from "../systems/clock";
import { Hud, UI_FONT } from "../ui/Hud";

export const FIELD_KEY = "field";

const TILE = 16;
const MAP_COLS = 100;
const MAP_ROWS = 60;
const WORLD_W = MAP_COLS * TILE;
const WORLD_H = MAP_ROWS * TILE;
const PLAYER_SPEED = 150;

// Tile ids are 1-based; order matches tools/gen_placeholder_assets.mjs.
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
}

// Town layout per docs/DESIGN.md §4 sketch (tile coords).
const BUILDINGS: Building[] = [
  { x: 4, y: 4, w: 10, h: 6, label: "剑阳道馆" },
  { x: 18, y: 4, w: 10, h: 6, label: "玄铁堂" },
  { x: 32, y: 4, w: 10, h: 6, label: "烟雨小筑" },
  { x: 4, y: 16, w: 8, h: 6, label: "药铺" },
  { x: 15, y: 16, w: 8, h: 6, label: "铁匠铺" },
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
  // Gate avenue: from the main street through the gate into the wilds.
  for (let y = 12; y <= 38; y++) for (let x = 48; x <= 51; x++) grid[y][x] = T.ROAD;
  // Wilderness trail below the wall.
  for (let x = 8; x <= 90; x++) {
    grid[38][x] = T.ROAD;
    grid[39][x] = T.ROAD;
  }

  for (const b of BUILDINGS) {
    for (let y = b.y; y < b.y + b.h; y++) {
      for (let x = b.x; x < b.x + b.w; x++) grid[y][x] = T.BUILDING;
    }
  }

  // 练武场: open sand ground.
  for (let y = 5; y <= 16; y++) for (let x = 52; x <= 69; x++) grid[y][x] = T.SAND;
  // Pond.
  for (let y = 24; y <= 27; y++) for (let x = 72; x <= 78; x++) grid[y][x] = T.WATER;
  // Well by the gate.
  grid[25][44] = T.WELL;
  grid[25][45] = T.WELL;
  grid[26][44] = T.WELL;
  grid[26][45] = T.WELL;

  // Town wall, gate kept open at the avenue.
  for (let y = 30; y <= 31; y++) for (let x = 0; x <= 99; x++) grid[y][x] = T.WALL;
  for (let y = 30; y <= 31; y++) for (let x = 48; x <= 51; x++) grid[y][x] = T.ROAD;

  // 东山林地 trees (fixed seed → reproducible on both ends later).
  const treeRnd = mulberry32(20261016);
  for (let y = 34; y <= 58; y++) {
    for (let x = 1; x <= 45; x++) {
      if (grid[y][x] === T.GRASS && treeRnd() < 0.13) grid[y][x] = T.TREE;
    }
  }
  // 后山崖 rocks with a solid cliff ridge along the bottom edge.
  const rockRnd = mulberry32(4396);
  for (let y = 34; y <= 58; y++) {
    for (let x = 54; x <= 98; x++) {
      if (grid[y][x] === T.GRASS && (y >= 51 || rockRnd() < 0.09)) grid[y][x] = T.CLIFF;
    }
  }
  return grid;
}

export class FieldScene extends Phaser.Scene {
  private player!: Phaser.Physics.Arcade.Sprite;
  private playerLabel!: Phaser.GameObjects.Text;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasd!: Record<"W" | "A" | "S" | "D", Phaser.Input.Keyboard.Key>;
  private hud!: Hud;
  private clock = new LocalGameClock();
  private poi = "";
  private poiTimer = 0;

  constructor() {
    super(FIELD_KEY);
  }

  create(): void {
    const map = this.make.tilemap({ data: buildMap(), tileWidth: TILE, tileHeight: TILE });
    const tileset = map.addTilesetImage("tiles", "tiles", TILE, TILE, 0, 0)!;
    const layer = map.createLayer(0, tileset, 0, 0)!;
    layer.setCollision(SOLID_TILES);

    this.physics.world.setBounds(0, 0, WORLD_W, WORLD_H);

    this.player = this.physics.add.sprite(49.5 * TILE, 27.5 * TILE, "player");
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

    this.hud = new Hud(this);

    const kb = this.input.keyboard!;
    this.cursors = kb.createCursorKeys();
    this.wasd = kb.addKeys("W,A,S,D") as Record<
      "W" | "A" | "S" | "D",
      Phaser.Input.Keyboard.Key
    >;

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
    try {
      const state = await api.gameState();
      this.clock.anchor(
        state.clock.game_sec,
        state.clock.real_ts,
        state.clock.time_scale,
      );
    } catch {
      this.hud.setNet("服务器 未连接");
      return;
    }

    let net = "服务器 已连接";
    try {
      const health = await api.health();
      const o = health.ollama;
      if (!o.reachable) net += " · Ollama 不可达";
      else if (!o.chat_model_ok || !o.embed_model_ok) net += " · Ollama 模型缺失";
      else net += " · Ollama 就绪";
    } catch {
      // Keep the plain connected state; health is a secondary signal.
    }
    this.hud.setNet(net);
  }

  update(_time: number, delta: number): void {
    let vx = 0;
    let vy = 0;
    if (this.cursors.left.isDown || this.wasd.A.isDown) vx -= 1;
    if (this.cursors.right.isDown || this.wasd.D.isDown) vx += 1;
    if (this.cursors.up.isDown || this.wasd.W.isDown) vy -= 1;
    if (this.cursors.down.isDown || this.wasd.S.isDown) vy += 1;
    if (vx !== 0 && vy !== 0) {
      vx *= Math.SQRT1_2;
      vy *= Math.SQRT1_2;
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

  private currentPoi(): string {
    const tx = Math.floor(this.player.x / TILE);
    const ty = Math.floor(this.player.y / TILE);
    for (const r of POI_RECTS) {
      if (tx >= r.x && tx < r.x + r.w && ty >= r.y && ty < r.y + r.h) return r.name;
    }
    return "";
  }
}
