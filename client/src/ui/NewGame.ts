export function promptNewGame(): Promise<string> {
  return new Promise((resolve) => {
    const root = document.getElementById("newgame-overlay")!;
    const input = document.getElementById("ng-name") as HTMLInputElement;
    const btn = document.getElementById("ng-start") as HTMLButtonElement;
    const err = document.getElementById("ng-error")!;
    root.style.display = "flex";
    input.value = "";
    err.textContent = "";
    setTimeout(() => input.focus(), 50);

    const done = (name: string) => {
      root.style.display = "none";
      btn.removeEventListener("click", onOk);
      input.removeEventListener("keydown", onKey);
      resolve(name);
    };
    const onOk = () => {
      const name = input.value.trim();
      if (!name) {
        err.textContent = "总得有个名号。";
        return;
      }
      done(name.slice(0, 12));
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Enter") onOk();
      ev.stopPropagation();
    };
    btn.addEventListener("click", onOk);
    input.addEventListener("keydown", onKey);
  });
}
