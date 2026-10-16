import Phaser from "phaser";

export const UI_FONT = '"Noto Serif SC","Songti SC","SimSun",serif';

export class Hud {
  private readonly timeText: Phaser.GameObjects.Text;
  private readonly poiText: Phaser.GameObjects.Text;
  private readonly netText: Phaser.GameObjects.Text;

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

    scene.add
      .text(12, 516, "WASD / 方向键 移动", {
        fontFamily: UI_FONT,
        fontSize: "12px",
        color: "#9aa0ab",
        stroke: "#16181d",
        strokeThickness: 3,
      })
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
}
