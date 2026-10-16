import Phaser from "phaser";
import { PlayerState } from "../api/client";
import { StatBar } from "./Bars";

export const UI_FONT = '"Noto Serif SC","Songti SC","SimSun",serif';

export class Hud {
  private readonly timeText: Phaser.GameObjects.Text;
  private readonly poiText: Phaser.GameObjects.Text;
  private readonly netText: Phaser.GameObjects.Text;
  private readonly hint: Phaser.GameObjects.Text;
  private readonly lvText: Phaser.GameObjects.Text;
  private readonly goldText: Phaser.GameObjects.Text;
  private readonly hpBar: StatBar;
  private readonly mpBar: StatBar;
  private readonly expBar: StatBar;

  constructor(scene: Phaser.Scene) {
    const style: Phaser.Types.GameObjects.Text.TextStyle = {
      fontFamily: UI_FONT,
      fontSize: "14px",
      color: "#e8e6e0",
      stroke: "#16181d",
      strokeThickness: 4,
    };
    this.timeText = scene.add.text(12, 10, "", style).setScrollFactor(0).setDepth(100);
    this.poiText = scene.add.text(12, 32, "", style).setScrollFactor(0).setDepth(100);
    this.netText = scene.add.text(12, 54, "连接中…", style).setScrollFactor(0).setDepth(100);
    this.hint = scene.add
      .text(12, 516, "WASD / 方向键 移动", {
        fontFamily: UI_FONT,
        fontSize: "12px",
        color: "#9aa0ab",
        stroke: "#16181d",
        strokeThickness: 3,
      })
      .setScrollFactor(0)
      .setDepth(100);

    this.lvText = scene.add
      .text(838, 10, "", style)
      .setScrollFactor(0)
      .setDepth(100);
    this.expBar = new StatBar(scene, 838, 32, 110, 6, 0x8a7444);
    this.hpBar = new StatBar(scene, 838, 46, 110, 12, 0x9c3a35, { text: true, label: "气血 " });
    this.mpBar = new StatBar(scene, 838, 66, 110, 12, 0x3a5c7c, { text: true, label: "内力 " });
    this.goldText = scene.add
      .text(838, 90, "", style)
      .setScrollFactor(0)
      .setDepth(100);
  }

  setTime(text: string): void {
    this.timeText.setText(text);
  }

  setPoi(text: string): void {
    this.poiText.setText(text);
  }

  setNet(text: string): void {
    this.netText.setText(text);
  }

  setHint(text: string): void {
    this.hint.setText(text);
  }

  update(player: PlayerState | null): void {
    if (!player) return;
    this.lvText.setText(`${player.name} · ${player.level} 级`);
    this.expBar.set(player.exp, player.exp_next);
    this.hpBar.set(player.hp, player.hp_max);
    this.mpBar.set(player.mp, player.mp_max);
    this.goldText.setText(`银两 ${player.gold} · 修炼 ${player.cultivation}`);
  }
}
