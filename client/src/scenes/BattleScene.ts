import Phaser from "phaser";
import { api, BattleSnapshot, LogEntry, PlayerState, Rewards } from "../api/client";
import { store } from "../systems/store";
import { StatBar } from "../ui/Bars";
import { FIELD_KEY } from "./FieldScene";
import { UI_FONT } from "../ui/Hud";

export const BATTLE_KEY = "battle";

const STEP_MS = 700;

export class BattleScene extends Phaser.Scene {
  private battle!: BattleSnapshot;
  private playerState!: PlayerState;
  private busy = false;

  private enemySprite!: Phaser.GameObjects.Image;
  private playerSprite!: Phaser.GameObjects.Image;
  private enemyHpBar!: StatBar;
  private playerHpBar!: StatBar;
  private playerMpBar!: StatBar;
  private roundText!: Phaser.GameObjects.Text;
  private logTexts: Phaser.GameObjects.Text[] = [];
  private commandLayer!: Phaser.GameObjects.Container;
  private potionLayer!: Phaser.GameObjects.Container;
  private floatPool: Phaser.GameObjects.Text[] = [];
  private logLines: string[] = [];

  constructor() {
    super(BATTLE_KEY);
  }

  init(data: { battle: BattleSnapshot; player: PlayerState }): void {
    // The scene instance is reused across battles; `busy` stays true
    // after a finished fight, so it must reset here or every command
    // in the next battle is silently dropped.
    this.busy = false;
    this.battle = data.battle;
    this.playerState = data.player;
    this.logLines = [];
  }

  create(): void {
    // Scene restart destroys every game object but class fields survive,
    // so pooled/log text refs from the previous battle must be dropped here.
    this.floatPool = [];
    this.logTexts = [];
    this.cameras.main.setBackgroundColor("#0c0d10");

    this.add
      .text(480, 28, `对阵 · ${this.battle.enemy.name}（${this.battle.enemy.level} 级）`, {
        fontFamily: UI_FONT,
        fontSize: "22px",
        color: "#e8e6e0",
      })
      .setOrigin(0.5);
    this.roundText = this.add.text(480, 54, "", {
      fontFamily: UI_FONT,
      fontSize: "12px",
      color: "#8f96a3",
    }).setOrigin(0.5);

    this.enemySprite = this.add.image(700, 190, this.battle.enemy.type).setScale(3);
    this.add
      .text(700, 232, `${this.battle.enemy.name} · ${this.battle.enemy.level} 级`, {
        fontFamily: UI_FONT,
        fontSize: "15px",
        color: "#e8e6e0",
      })
      .setOrigin(0.5);
    this.enemyHpBar = new StatBar(this, 630, 140, 140, 12, 0x9c3a35, { text: true });

    this.playerSprite = this.add.image(260, 190, "player").setScale(3);
    this.add
      .text(260, 232, `${this.battle.player.name} · ${this.battle.player.level} 级`, {
        fontFamily: UI_FONT,
        fontSize: "15px",
        color: "#e8e6e0",
      })
      .setOrigin(0.5);
    this.playerHpBar = new StatBar(this, 190, 140, 140, 12, 0x9c3a35, { text: true });
    this.playerMpBar = new StatBar(this, 190, 118, 140, 12, 0x3a5c7c, { text: true });

    for (let i = 0; i < 6; i++) {
      this.logTexts.push(
        this.add.text(200, 296 + i * 22, "", {
          fontFamily: UI_FONT,
          fontSize: "14px",
          color: "#c9ccd4",
        }),
      );
    }

    this.buildCommands();
    this.syncBars();

    if (this.battle.result) {
      this.showResult(this.battle.result, null);
    }
  }

  private buildCommands(): void {
    this.commandLayer = this.add.container(0, 0);
    const mk = (x: number, label: string, cb: () => void, disabled = false) => {
      const text = this.add
        .text(x, 480, label, {
          fontFamily: UI_FONT,
          fontSize: "17px",
          color: disabled ? "#555a63" : "#e8e6e0",
          backgroundColor: disabled ? "#1c1f26" : "#2c3038",
          padding: { x: 26, y: 10 },
        })
        .setOrigin(0.5);
      if (!disabled) {
        text.setInteractive({ useHandCursor: true });
        text.on("pointerover", () => text.setBackgroundColor("#3a3f4a"));
        text.on("pointerout", () => text.setBackgroundColor("#2c3038"));
        text.on("pointerup", () => {
          if (!this.busy) cb();
        });
      }
      this.commandLayer.add(text);
    };
    mk(200, "攻 击", () => void this.doAction("attack"));
    mk(360, "技 能", () => undefined, true);
    mk(520, "药 剂", () => this.openPotions());
    mk(680, "逃 走", () => void this.doAction("flee"));

    this.potionLayer = this.add.container(0, 0).setVisible(false);
  }

  private openPotions(): void {
    const potions = this.playerState.inventory.filter((i) => i.type === "potion");
    this.potionLayer.removeAll(true);
    const used = this.battle.potions_used >= 3;
    if (potions.length === 0) {
      this.potionLayer.add(
        this.add.text(200, 470, "身上没有药剂（药铺有售）", {
          fontFamily: UI_FONT,
          fontSize: "15px",
          color: "#8f96a3",
        }),
      );
    } else {
      potions.slice(0, 4).forEach((p, i) => {
        const x = 130 + i * 190;
        const t = this.add
          .text(x, 480, `${p.name} ×${p.qty}`, {
            fontFamily: UI_FONT,
            fontSize: "14px",
            color: used ? "#555a63" : "#e8e6e0",
            backgroundColor: used ? "#1c1f26" : "#2c3038",
            padding: { x: 12, y: 8 },
          })
          .setOrigin(0.5);
        if (!used) {
          t.setInteractive({ useHandCursor: true });
          t.on("pointerover", () => t.setBackgroundColor("#3a3f4a"));
          t.on("pointerout", () => t.setBackgroundColor("#2c3038"));
          t.on("pointerup", () => {
            if (!this.busy) {
              this.closePotions();
              void this.doAction("item", p.item_id);
            }
          });
        }
        this.potionLayer.add(t);
      });
    }
    if (used) {
      this.potionLayer.add(
        this.add.text(480, 448, "本场药剂已用尽（上限 3 次）", {
          fontFamily: UI_FONT,
          fontSize: "12px",
          color: "#c47a72",
        }).setOrigin(0.5),
      );
    }
    const back = this.add
      .text(800, 480, "返 回", {
        fontFamily: UI_FONT,
        fontSize: "14px",
        color: "#9aa0ab",
        backgroundColor: "#2c3038",
        padding: { x: 14, y: 8 },
      })
      .setOrigin(0.5);
    back.setInteractive({ useHandCursor: true });
    back.on("pointerup", () => this.closePotions());
    this.potionLayer.add(back);

    this.commandLayer.setVisible(false);
    this.potionLayer.setVisible(true);
  }

  private closePotions(): void {
    this.potionLayer.setVisible(false);
    this.commandLayer.setVisible(true);
  }

  private async doAction(action: string, itemId?: number): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.commandLayer.setAlpha(0.4);
    try {
      const resp = await api.battleAction(action, itemId);
      this.playerState = resp.player;
      this.battle = resp.battle;
      await this.playLog(resp.log);
      this.syncBars();
      if (resp.result) {
        this.showResult(resp.result, resp.rewards);
      } else {
        this.busy = false;
        this.commandLayer.setAlpha(1);
      }
    } catch (e) {
      this.addLog(`（${e instanceof Error ? e.message : "出错了"}）`);
      this.busy = false;
      this.commandLayer.setAlpha(1);
    }
  }

  private async playLog(entries: LogEntry[]): Promise<void> {
    for (const e of entries) {
      this.addLog(e.text);
      if (e.kind === "player_attack") {
        this.float(700, 120, e.dodged ? "闪避" : `-${e.value}${e.crit ? " 会心" : ""}`,
          e.crit ? "#e0913f" : "#d8524a", e.crit ? 20 : 16);
        if (!e.dodged) this.hitShake(this.enemySprite);
      } else if (e.kind === "enemy_attack") {
        this.float(260, 120, e.dodged ? "闪避" : `-${e.value}`, e.dodged ? "#8f96a3" : "#d8524a", 16);
        if (!e.dodged) this.hitShake(this.playerSprite);
      } else if (e.kind === "player_item" || e.kind === "enemy_item") {
        const [x, hp] = e.kind === "player_item" ? [260, e.player_hp] : [700, e.enemy_hp];
        void hp;
        this.float(x, 120, `+${e.value ?? 0}`, "#8fbf7a", 16);
      } else if (e.kind === "levelup") {
        this.float(480, 90, "境界提升", "#e0c28a", 20);
      }
      if (e.enemy_hp !== undefined) this.enemyHpBar.set(e.enemy_hp, this.battle.enemy.hp_max);
      if (e.player_hp !== undefined) this.playerHpBar.set(e.player_hp, this.battle.player.hp_max);
      await this.wait(STEP_MS);
    }
  }

  private hitShake(target: Phaser.GameObjects.Image): void {
    target.setX(target === this.enemySprite ? 700 : 260);
    this.tweens.add({
      targets: target,
      x: target.x - 6,
      duration: 50,
      yoyo: true,
      repeat: 2,
    });
  }

  private float(x: number, y: number, text: string, color: string, size: number): void {
    // Pooled instead of created per hit: destroying Text objects mid-frame
    // can crash the renderer under software WebGL.
    let t = this.floatPool.find((c) => !c.active);
    if (!t) {
      t = this.add
        .text(x, y, "", {
          fontFamily: UI_FONT,
          stroke: "#16181d",
          strokeThickness: 4,
        })
        .setOrigin(0.5)
        .setDepth(50);
      this.floatPool.push(t);
    }
    t.setActive(true).setVisible(true).setAlpha(1).setPosition(x, y);
    t.setStyle({ fontSize: `${size}px`, color });
    t.setText(text);
    this.tweens.add({
      targets: t,
      y: y - 44,
      alpha: 0,
      duration: 900,
      ease: "Cubic.easeOut",
      onComplete: () => t.setActive(false).setVisible(false),
    });
  }

  private addLog(line: string): void {
    this.logLines.push(line);
    if (this.logLines.length > 6) this.logLines.shift();
    this.logTexts.forEach((t, i) => t.setText(this.logLines[i] ?? ""));
  }

  private syncBars(): void {
    this.enemyHpBar.set(this.battle.enemy.hp, this.battle.enemy.hp_max);
    this.playerHpBar.set(this.battle.player.hp, this.battle.player.hp_max);
    this.playerMpBar.set(this.battle.player.mp, this.battle.player.mp_max);
    this.roundText.setText(`第 ${this.battle.round} 回合`);
  }

  private showResult(result: string, rewards: Rewards | null): void {
    this.busy = true;
    this.commandLayer.setAlpha(0.2);
    this.roundText.setText("战斗结束");

    const layer = this.add.container(0, 0).setDepth(60);
    this.add.rectangle(480, 270, 960, 540, 0x0a0b0e, 0.6).setDepth(59);
    layer.add(this.add.rectangle(480, 250, 420, 260, 0x23262e).setStrokeStyle(1, 0x3a3f4a));

    const title =
      result === "win" ? "战 斗 胜 利" : result === "lose" ? "力 战 不 敌" : "全 身 而 退";
    const titleColor =
      result === "win" ? "#e0c28a" : result === "lose" ? "#c47a72" : "#a8c79a";
    layer.add(
      this.add
        .text(480, 176, title, { fontFamily: UI_FONT, fontSize: "26px", color: titleColor })
        .setOrigin(0.5),
    );

    const lines: string[] = [];
    if (result === "win" && rewards) {
      lines.push(`获得 经验 ${rewards.exp} · 银两 ${rewards.gold} · 修炼点 ${rewards.cultivation}`);
      for (const d of rewards.drops) lines.push(`拾获 ${d.name} ×${d.qty}`);
      for (const lv of rewards.level_ups) lines.push(`境界提升！升至 ${lv} 级`);
    } else if (result === "lose") {
      lines.push("你被人抬回了客栈，气血内力折半，散去一成银两。");
    } else {
      lines.push("你摆脱了纠缠，得以脱身。");
    }
    lines.forEach((l, i) => {
      layer.add(
        this.add
          .text(480, 216 + i * 26, l, { fontFamily: UI_FONT, fontSize: "14px", color: "#c9ccd4" })
          .setOrigin(0.5),
      );
    });

    const btn = this.add
      .text(480, 368, "继 续", {
        fontFamily: UI_FONT,
        fontSize: "16px",
        color: "#23262e",
        backgroundColor: "#8a7444",
        padding: { x: 24, y: 10 },
      })
      .setOrigin(0.5);
    btn.setInteractive({ useHandCursor: true });
    btn.on("pointerover", () => btn.setBackgroundColor("#a08a50"));
    btn.on("pointerout", () => btn.setBackgroundColor("#8a7444"));
    btn.on("pointerup", () => {
      void (async () => {
        try {
          await store.refresh();
        } catch {
          /* keep going with stale state */
        }
        this.scene.start(FIELD_KEY, {
          fledFrom: result === "fled" ? this.battle.enemy.id : undefined,
        });
      })();
    });
    layer.add(btn);
  }

  private wait(ms: number): Promise<void> {
    return new Promise((resolve) => this.time.delayedCall(ms, resolve));
  }
}
