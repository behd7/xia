import Phaser from "phaser";
import { BootScene } from "./scenes/BootScene";
import { FieldScene } from "./scenes/FieldScene";

const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game",
  width: 960,
  height: 540,
  backgroundColor: "#16181d",
  pixelArt: true,
  physics: { default: "arcade", arcade: { gravity: { x: 0, y: 0 } } },
  scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
  scene: [BootScene, FieldScene],
});

// Debug handle for browser console / automated smoke checks.
(window as unknown as Record<string, unknown>).__xia = game;
