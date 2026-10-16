import Phaser from "phaser";

export class StatBar {
  private readonly g: Phaser.GameObjects.Graphics;
  private readonly text: Phaser.GameObjects.Text | null;
  private readonly w: number;
  private readonly h: number;
  private readonly color: number;
  private readonly label: string;

  constructor(
    scene: Phaser.Scene,
    x: number,
    y: number,
    w: number,
    h: number,
    color: number,
    opts: { text?: boolean; label?: string } = {},
  ) {
    this.g = scene.add.graphics().setPosition(x, y);
    this.w = w;
    this.h = h;
    this.color = color;
    this.label = opts.label ?? "";
    this.text = opts.text
      ? scene.add
          .text(x + w + 6, y - 3, "", {
            fontFamily: '"Noto Serif SC","Songti SC","SimSun",serif',
            fontSize: "11px",
            color: "#e8e6e0",
          })
          .setDepth(this.g.depth)
      : null;
    this.set(1, 1);
  }

  set(current: number, max: number): void {
    const ratio = max > 0 ? Phaser.Math.Clamp(current / max, 0, 1) : 0;
    const g = this.g;
    g.clear();
    g.fillStyle(0x171920, 0.9);
    g.fillRect(0, 0, this.w, this.h);
    g.lineStyle(1, 0x3a3f4a);
    g.strokeRect(0, 0, this.w, this.h);
    if (ratio > 0) {
      g.fillStyle(this.color);
      g.fillRect(1, 1, Math.round((this.w - 2) * ratio), this.h - 2);
    }
    if (this.text) {
      this.text.setText(`${this.label}${Math.round(current)}/${Math.round(max)}`);
    }
  }

  destroy(): void {
    this.g.destroy();
    this.text?.destroy();
  }
}
