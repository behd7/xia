import Phaser from "phaser";
import { api, PlayerState } from "../api/client";
import { store } from "../systems/store";
import { PanelChrome } from "./PanelChrome";

const TIER_NAMES = ["", "一档", "二档", "三档"];

export class SkillsPanel {
  private readonly chrome: PanelChrome;

  constructor(scene: Phaser.Scene, onClose: () => void) {
    this.chrome = new PanelChrome(scene, "武学", 430, onClose);
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
    const add = <T extends Phaser.GameObjects.GameObject>(o: T): T => {
      this.rowObjs.push(o);
      return o;
    };

    if (!p.sect) {
      add(c.label(240, 120, "尚未拜入门派。", 14, "#8f96a3"));
      add(c.label(240, 146, "等级 3 后可至剑阳道馆 / 玄铁堂 / 烟雨小筑拜师。", 13, "#8f96a3"));
      return;
    }

    add(c.label(240, 94, `门派 ${p.sect.name} · ${p.sect.rank} · 修炼点 ${p.cultivation}`, 14, "#c8a75a"));

    let y = 130;
    for (const s of p.skills) {
      const color = s.locked ? "#555a63" : s.equipped ? "#e0c28a" : "#e8e6e0";
      const meta = s.kind === "active" ? `${TIER_NAMES[s.tier]} · MP ${s.mp_cost} · 倍率 ×${s.multiplier.toFixed(2)}` : `${TIER_NAMES[s.tier]} · 被动`;
      add(c.label(240, y, `${s.name}  Lv.${s.level}`, 15, color));
      add(c.label(240, y + 22, `${meta} · ${s.effect_desc}`, 11, s.locked ? "#555a63" : "#8f96a3"));
      if (s.locked) {
        add(c.label(620, y + 10, s.tier === 2 ? "需亲传弟子（M3）" : "需关门弟子（M3）", 12, "#555a63"));
      } else {
        if (s.kind === "active") {
          add(c.button(600, y + 12, s.equipped ? "卸下" : "装备", () => void this.toggleEquip(s.skill_id, !s.equipped)));
        }
        add(
          c.button(676, y + 12, s.upgradable ? `升级·${s.upgrade_cost}修` : "圆满", () => void this.upgrade(s.skill_id), !s.upgradable),
        );
      }
      y += 52;
    }
    add(c.label(240, y + 8, "至多同时装备 3 门主动技；升级消耗修炼点。", 12, "#8f96a3"));
  }

  private async toggleEquip(skillId: number, equip: boolean): Promise<void> {
    try {
      const resp = await api.skillEquip(skillId, equip);
      store.player = resp.player;
      this.buildRows(resp.player);
    } catch (e) {
      this.chrome.showToast(e instanceof Error ? e.message : "操作失败", false);
    }
  }

  private async upgrade(skillId: number): Promise<void> {
    try {
      const resp = await api.skillUpgrade(skillId);
      store.player = resp.player;
      this.buildRows(resp.player);
      this.chrome.showToast("修为精进。");
    } catch (e) {
      this.chrome.showToast(e instanceof Error ? e.message : "升级失败", false);
    }
  }

  close(): void {
    this.chrome.close();
  }
}
