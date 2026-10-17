import Phaser from "phaser";
import { UI_FONT } from "./Hud";

const D = 150;

export class PanelChrome {
  readonly objs: Phaser.GameObjects.GameObject[] = [];
  private readonly toastText: Phaser.GameObjects.Text;

  constructor(
    private scene: Phaser.Scene,
    title: string,
    height: number,
    private onClose: () => void,
  ) {
    this.add(scene.add.rectangle(480, 270, 960, 540, 0x0a0b0e, 0.72).setInteractive());
    this.add(scene.add.rectangle(480, 270, 540, height, 0x23262e).setStrokeStyle(1, 0x3a3f4a));
    this.add(
      scene.add
        .text(480, 58, title, { fontFamily: UI_FONT, fontSize: "20px", color: "#e8e6e0" })
        .setOrigin(0.5),
    );

    const closeBtn = this.add(
      scene.add
        .text(480, 270 + height / 2 - 26, "关闭（Esc）", {
          fontFamily: UI_FONT,
          fontSize: "14px",
          color: "#9aa0ab",
        })
        .setOrigin(0.5),
    );
    closeBtn.setInteractive({ useHandCursor: true });
    closeBtn.on("pointerover", () => closeBtn.setColor("#e8e6e0"));
    closeBtn.on("pointerout", () => closeBtn.setColor("#9aa0ab"));
    closeBtn.on("pointerup", () => this.close());

    this.toastText = this.add(
      scene.add
        .text(480, 270 + height / 2 - 50, "", { fontFamily: UI_FONT, fontSize: "13px", color: "#a8c79a" })
        .setOrigin(0.5)
        .setAlpha(0),
    );
    this.toastText.setDepth(D + 2);
  }

  close(): void {
    for (const o of this.objs) o.destroy();
    this.objs.length = 0;
    this.onClose();
  }

  add<T extends Phaser.GameObjects.GameObject>(o: T): T {
    // Panels are screen-space HUD: pin them to the camera.
    const scrollable = o as unknown as { setScrollFactor?: (x: number, y?: number) => void };
    scrollable.setScrollFactor?.(0);
    this.objs.push(o);
    return o;
  }

  showToast(msg: string, ok = true): void {
    this.toastText.setText(msg).setAlpha(1).setColor(ok ? "#a8c79a" : "#c47a72");
    this.scene.tweens.killTweensOf(this.toastText);
    this.scene.tweens.add({ targets: this.toastText, alpha: 0, delay: 1500, duration: 500 });
  }

  button(x: number, y: number, label: string, cb: () => void, disabled = false): Phaser.GameObjects.Text {
    const btn = this.add(
      this.scene.add
        .text(x, y, label, {
          fontFamily: UI_FONT,
          fontSize: "13px",
          color: disabled ? "#555a63" : "#23262e",
          backgroundColor: disabled ? "#1c1f26" : "#8a7444",
          padding: { x: 10, y: 5 },
        })
        .setOrigin(0.5),
    );
    if (!disabled) {
      btn.setInteractive({ useHandCursor: true });
      btn.on("pointerover", () => btn.setBackgroundColor("#a08a50"));
      btn.on("pointerout", () => btn.setBackgroundColor("#8a7444"));
      btn.on("pointerup", () => cb());
    }
    return btn;
  }

  label(x: number, y: number, text: string, size = 14, color = "#e8e6e0", origin = 0): Phaser.GameObjects.Text {
    return this.add(
      this.scene.add
        .text(x, y, text, { fontFamily: UI_FONT, fontSize: `${size}px`, color })
        .setOrigin(origin, 0),
    );
  }
}
