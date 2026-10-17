import { api, PlayerState } from "../api/client";
import { store } from "../systems/store";
import { PanelChrome } from "./PanelChrome";

const SECT_CARDS: Record<string, { name: string; role: string; bonus: string }> = {
  jianyang: { name: "剑阳派", role: "外功强攻", bonus: "入门加成：臂力+3、悟性+2" },
  xuantie: { name: "玄铁堡", role: "防御反制", bonus: "入门加成：根骨+3、悟性+2" },
  yanyu: { name: "烟雨楼", role: "迅捷闪避", bonus: "入门加成：身法+3、悟性+2" },
};

export class JoinSectPanel {
  private readonly chrome: PanelChrome;

  constructor(scene: Phaser.Scene, sectId: string, onClose: () => void) {
    this.chrome = new PanelChrome(scene, "拜师入门", 360, onClose);
    const card = SECT_CARDS[sectId];
    const p = store.player;
    const c = this.chrome;

    c.label(240, 96, `${card.name} · ${card.role}`, 17);
    c.label(240, 128, card.bonus, 13, "#c8a75a");
    c.label(240, 154, "入门即授内功与一档武学，一生只入一派。", 13, "#9aa0ab");

    const reason = this.blockReason(p);
    c.label(240, 190, reason ? `暂时无法拜师：${reason}` : "掌门颔首：你已具备入门的资格。", 14, reason ? "#c47a72" : "#a8c79a");

    const btn = c.button(350, 268, "拜入门下", () => void this.join(sectId), reason !== null);
    btn.setData("sect", sectId);
    c.label(560, 268, reason ?? "", 12, "#8f96a3", 0.5);
  }

  private blockReason(p: PlayerState | null): string | null {
    if (!p) return "无存档";
    if (p.sect) return `你已是${p.sect.name}弟子`;
    if (p.level < 3) return `入门需等级 ≥3（当前 ${p.level}）`;
    return null;
  }

  private async join(sectId: string): Promise<void> {
    try {
      const resp = await api.joinSect(sectId);
      store.player = resp.player;
      this.chrome.showToast(`你已拜入${resp.player.sect?.name ?? "门派"}！`);
    } catch (e) {
      this.chrome.showToast(e instanceof Error ? e.message : "拜师失败", false);
    }
  }

  close(): void {
    this.chrome.close();
  }
}
