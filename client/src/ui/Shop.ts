import Phaser from "phaser";
import { ShopInfo } from "../api/client";
import { store } from "../systems/store";
import { UI_FONT } from "./Hud";

export class ShopPanel {
  private readonly objs: Phaser.GameObjects.GameObject[] = [];
  private readonly goldText: Phaser.GameObjects.Text;
  private readonly toast: Phaser.GameObjects.Text;
  private readonly onClose: () => void;

  constructor(
    private scene: Phaser.Scene,
    info: ShopInfo,
    getGold: () => number,
    onBuy: (itemId: number) => Promise<string | null>,
    onSell: (itemId: number) => Promise<string | null>,
    onClose: () => void,
  ) {
    this.onClose = onClose;
    const add = <T extends Phaser.GameObjects.GameObject>(o: T): T => {
      this.objs.push(o);
      return o;
    };
    const D = 150;

    add(
      scene.add
        .rectangle(480, 270, 960, 540, 0x0a0b0e, 0.72)
        .setScrollFactor(0)
        .setDepth(D)
        .setInteractive(),
    );
    add(
      scene.add
        .rectangle(480, 270, 520, 430, 0x23262e)
        .setStrokeStyle(1, 0x3a3f4a)
        .setScrollFactor(0)
        .setDepth(D + 1),
    );
    add(
      scene.add
        .text(480, 72, info.name, { fontFamily: UI_FONT, fontSize: "20px", color: "#e8e6e0" })
        .setOrigin(0.5)
        .setScrollFactor(0)
        .setDepth(D + 1),
    );
    this.goldText = add(
      scene.add
        .text(480, 100, `银两 ${getGold()}`, {
          fontFamily: UI_FONT,
          fontSize: "13px",
          color: "#c8a75a",
        })
        .setOrigin(0.5)
        .setScrollFactor(0)
        .setDepth(D + 1),
    );

    info.items.forEach((it, i) => {
      const y = 138 + i * 56;
      add(
        scene.add
          .text(240, y - 10, it.name, { fontFamily: UI_FONT, fontSize: "15px", color: "#e8e6e0" })
          .setScrollFactor(0)
          .setDepth(D + 1),
      );
      add(
        scene.add
          .text(240, y + 10, it.desc, { fontFamily: UI_FONT, fontSize: "11px", color: "#8f96a3" })
          .setScrollFactor(0)
          .setDepth(D + 1),
      );
      add(
        scene.add
          .text(560, y, `${it.price} 两`, {
            fontFamily: UI_FONT,
            fontSize: "14px",
            color: "#c8a75a",
          })
          .setScrollFactor(0)
          .setDepth(D + 1),
      );
      const btn = add(
        scene.add
          .text(668, y + 8, "买", {
            fontFamily: UI_FONT,
            fontSize: "14px",
            color: "#23262e",
            backgroundColor: "#8a7444",
            padding: { x: 10, y: 5 },
          })
          .setOrigin(0.5)
          .setScrollFactor(0)
          .setDepth(D + 1),
      );
      btn.setInteractive({ useHandCursor: true });
      btn.on("pointerover", () => btn.setBackgroundColor("#a08a50"));
      btn.on("pointerout", () => btn.setBackgroundColor("#8a7444"));
      btn.on("pointerup", async () => {
        if (btn.getData("busy")) return;
        btn.setData("busy", true);
        const err = await onBuy(it.item_id);
        btn.setData("busy", false);
        this.goldText.setText(`银两 ${getGold()}`);
        this.showToast(err ? err : `已购入 ${it.name}`);
      });
    });

    const closeBtn = add(
      scene.add
        .text(480, 452, "关闭（Esc）", {
          fontFamily: UI_FONT,
          fontSize: "14px",
          color: "#9aa0ab",
        })
        .setOrigin(0.5)
        .setScrollFactor(0)
        .setDepth(D + 1),
    );
    closeBtn.setInteractive({ useHandCursor: true });
    closeBtn.on("pointerover", () => closeBtn.setColor("#e8e6e0"));
    closeBtn.on("pointerout", () => closeBtn.setColor("#9aa0ab"));
    closeBtn.on("pointerup", () => this.close());

    this.toast = add(
      scene.add
        .text(480, 478, "", { fontFamily: UI_FONT, fontSize: "13px", color: "#a8c79a" })
        .setOrigin(0.5)
        .setScrollFactor(0)
        .setDepth(D + 2)
        .setAlpha(0),
    );

    this.sellSectionY = 138 + info.items.length * 56 + 16;
    this.buildSellSection(getGold, onSell);
    void this.scene;
  }

  private sellSectionY = 0;
  private sellRowObjs: Phaser.GameObjects.GameObject[] = [];

  private buildSellSection(getGold: () => number, onSell: (itemId: number) => Promise<string | null>): void {
    for (const o of this.sellRowObjs) o.destroy();
    this.sellRowObjs = [];
    const add = <T extends Phaser.GameObjects.GameObject>(o: T): T => {
      (o as unknown as { setScrollFactor: (x: number, y?: number) => void }).setScrollFactor(0);
      this.sellRowObjs.push(o);
      return o;
    };
    const p = store.player;
    add(this.scene.add.text(240, this.sellSectionY, "出售（半价回收）", { fontFamily: UI_FONT, fontSize: "13px", color: "#9aa0ab" }));
    const sellables = (p?.inventory ?? []).filter((it) => !it.slot).slice(0, 2);
    if (sellables.length === 0) {
      add(this.scene.add.text(240, this.sellSectionY + 24, "（无可售之物）", { fontFamily: UI_FONT, fontSize: "12px", color: "#555a63" }));
      return;
    }
    sellables.forEach((it, i) => {
      const y = this.sellSectionY + 24 + i * 40;
      add(this.scene.add.text(240, y, `${it.name} ×${it.qty}`, { fontFamily: UI_FONT, fontSize: "14px", color: "#e8e6e0" }));
      add(this.scene.add.text(560, y + 2, `售 ${Math.floor((it.price ?? 0) / 2)} 两`, { fontFamily: UI_FONT, fontSize: "12px", color: "#c8a75a" }));
      const btn = add(
        this.scene.add
          .text(690, y + 8, "卖", {
            fontFamily: UI_FONT,
            fontSize: "14px",
            color: "#23262e",
            backgroundColor: "#5f7a4a",
            padding: { x: 10, y: 5 },
          })
          .setOrigin(0.5),
      );
      btn.setInteractive({ useHandCursor: true });
      btn.on("pointerover", () => btn.setBackgroundColor("#75945c"));
      btn.on("pointerout", () => btn.setBackgroundColor("#5f7a4a"));
      btn.on("pointerup", async () => {
        if (btn.getData("busy")) return;
        btn.setData("busy", true);
        const err = await onSell(it.item_id);
        btn.setData("busy", false);
        this.goldText.setText(`银两 ${getGold()}`);
        this.showToast(err ? err : `已售出 ${it.name}`);
        this.buildSellSection(getGold, onSell);
      });
    });
  }

  private showToast(msg: string): void {
    this.toast.setText(msg).setAlpha(1).setColor(msg.startsWith("已") ? "#a8c79a" : "#c47a72");
    this.scene.tweens.killTweensOf(this.toast);
    this.scene.tweens.add({
      targets: this.toast,
      alpha: 0,
      delay: 1400,
      duration: 500,
    });
  }

  close(): void {
    for (const o of this.objs) o.destroy();
    this.objs.length = 0;
    this.onClose();
  }
}
