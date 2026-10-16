import Phaser from "phaser";
import { FIELD_KEY } from "./FieldScene";

export const BOOT_KEY = "boot";

export class BootScene extends Phaser.Scene {
  constructor() {
    super(BOOT_KEY);
  }

  preload(): void {
    this.load.image("tiles", "assets/tiles.png");
    this.load.image("player", "assets/player.png");
    this.load.image("wolf", "assets/wolf.png");
    this.load.image("bandit", "assets/bandit.png");
    this.load.image("boss", "assets/boss.png");
  }

  create(): void {
    this.scene.start(FIELD_KEY);
  }
}
