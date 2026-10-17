import { api, PlayerState } from "../api/client";
import { store } from "../systems/store";
import { PanelChrome } from "./PanelChrome";

const STAT_NAMES: Record<string, string> = { str: "臂力", con: "根骨", agi: "身法", int: "悟性" };

export class CharacterPanel {
  private readonly chrome: PanelChrome;
  private readonly freeText: Phaser.GameObjects.Text;
  private readonly statTexts = new Map<string, Phaser.GameObjects.Text>();
  private readonly derivedText: Phaser.GameObjects.Text;
  private readonly plusButtons = new Map<string, Phaser.GameObjects.Text>();

  constructor(scene: Phaser.Scene, onClose: () => void) {
    this.chrome = new PanelChrome(scene, "角色 · 江湖历练", 430, () => {
      this.plusButtons.clear();
      onClose();
    });
    const c = this.chrome;

    this.freeText = c.label(240, 96, "", 14, "#c8a75a");
    c.label(300, 96, "", 13, "#8f96a3");

    let y = 128;
    for (const key of ["str", "con", "agi", "int"]) {
      c.label(240, y, STAT_NAMES[key], 15);
      const val = c.label(330, y, "", 15, "#e8e6e0");
      this.statTexts.set(key, val);
      const btn = c.button(370, y + 10, "+", () => void this.allocate(key), true);
      this.plusButtons.set(key, btn);
      y += 36;
    }

    this.derivedText = c.label(450, 128, "", 14, "#c9ccd4");

    this.refresh(store.player);
  }

  private refresh(p: PlayerState | null): void {
    if (!p) return;
    const sectLine = p.sect ? `${p.sect.name} · ${p.sect.rank}` : "未入派（等级 3 可至道馆拜师）";
    this.freeText.setText(
      `自由点 ${p.free_points} · 修炼点 ${p.cultivation} · 银两 ${p.gold}\n门派：${sectLine}`,
    );
    for (const [key, text] of this.statTexts) {
      text.setText(String(p.stats[key as keyof typeof p.stats]));
    }
    for (const [, btn] of this.plusButtons) {
      const has = p.free_points > 0;
      if (has) btn.setInteractive({ useHandCursor: true });
      else btn.disableInteractive();
      btn.setBackgroundColor(has ? "#8a7444" : "#1c1f26");
      btn.setColor(has ? "#23262e" : "#555a63");
    }
    this.derivedText.setText(
      [
        `攻击 ${p.atk}`,
        `防御 ${p.def}`,
        `速度 ${p.spd}`,
        `闪避 ${(p.dodge * 100).toFixed(1)}%`,
        `暴击 ${(p.crit * 100).toFixed(1)}%`,
        `气血 ${p.hp}/${p.hp_max}`,
        `内力 ${p.mp}/${p.mp_max}`,
        `经验 ${p.exp}/${p.exp_next}`,
      ].join("\n"),
    );
  }

  private async allocate(key: string): Promise<void> {
    try {
      const resp = await api.allocate({ [key]: 1 });
      store.player = resp.player;
      this.refresh(resp.player);
    } catch (e) {
      this.chrome.showToast(e instanceof Error ? e.message : "分配失败", false);
    }
  }

  close(): void {
    this.chrome.close();
  }
}
