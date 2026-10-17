import { api, InventoryItem, PlayerState } from "../api/client";
import { store } from "../systems/store";
import { PanelChrome } from "./PanelChrome";

const SLOT_NAMES: Record<string, string> = { weapon: "武器", armor: "衣服", accessory: "饰品" };

export class InventoryPanel {
  private readonly chrome: PanelChrome;

  constructor(scene: Phaser.Scene, onClose: () => void) {
    this.chrome = new PanelChrome(scene, "背包", 430, onClose);
    this.chrome.label(240, 92, "", 13, "#8f96a3");
    this.refresh(store.player);
  }

  private refresh(p: PlayerState | null): void {
    if (!p) return;
    this.buildRows(p);
  }

  private rowObjs: Phaser.GameObjects.GameObject[] = [];

  private buildRows(p: PlayerState): void {
    for (const o of this.rowObjs) o.destroy();
    this.rowObjs = [];
    const c = this.chrome;
    const add = <T extends Phaser.GameObjects.GameObject>(o: T, row = true): T => {
      if (row) this.rowObjs.push(o);
      return o;
    };
    let y = 118;
    if (p.inventory.length === 0) {
      add(c.label(240, y, "行囊空空。", 14, "#8f96a3"));
    }
    for (const it of p.inventory) {
      const equipped = it.slot !== null;
      add(c.label(240, y, `${it.name} ×${it.qty}`, 15));
      add(c.label(240, y + 20, it.desc + (equipped && it.slot ? `（装备中 · ${SLOT_NAMES[it.slot] ?? it.slot}）` : ""), 11, "#8f96a3"));
      const equippable = it.type in SLOT_NAMES;
      if (equippable) {
        const btn = add(
          c.button(690, y + 12, equipped ? "卸下" : "装备", () => void this.toggle(it, equipped)),
        ) as Phaser.GameObjects.Text;
        btn.setData("row", true);
      }
      y += 48;
    }
    if (p.inventory.length > 0) {
      add(c.label(240, y + 6, "药剂于战斗中使用；多余物品可在商店出售（半价）。", 12, "#8f96a3"));
    }
  }

  private async toggle(it: InventoryItem, equipped: boolean): Promise<void> {
    try {
      const resp = equipped && it.slot ? await api.unequipItem(it.slot) : await api.equipItem(it.item_id);
      store.player = resp.player;
      this.buildRows(resp.player);
    } catch (e) {
      this.chrome.showToast(e instanceof Error ? e.message : "操作失败", false);
    }
  }

  close(): void {
    this.chrome.close();
  }
}
