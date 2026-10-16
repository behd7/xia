const BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"];

/**
 * Client-side mirror of the server game clock: anchored to a server
 * snapshot, then advanced locally with the same time_scale so HUD
 * updates need no polling (docs/DESIGN.md §5.7).
 */
export class LocalGameClock {
  private gameSec = 0;
  private realTs = 0;
  private scale = 60;
  private anchored = false;

  get ready(): boolean {
    return this.anchored;
  }

  anchor(gameSec: number, realTs: number, scale: number): void {
    this.gameSec = gameSec;
    this.realTs = realTs;
    this.scale = scale;
    this.anchored = true;
  }

  nowGameSec(): number {
    return this.gameSec + (Date.now() / 1000 - this.realTs) * this.scale;
  }

  format(): string {
    const g = this.nowGameSec();
    const day = Math.floor(g / 86400) + 1;
    const secOfDay = g % 86400;
    const hour = Math.floor(secOfDay / 3600);
    const minute = Math.floor((secOfDay % 3600) / 60);
    const branch = BRANCHES[Math.floor(((hour + 1) % 24) / 2)];
    const hh = String(hour).padStart(2, "0");
    const mm = String(minute).padStart(2, "0");
    return `第${day}日 ${branch}时 ${hh}:${mm}`;
  }
}
