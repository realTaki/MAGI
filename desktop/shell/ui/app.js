const chooser = document.getElementById("view-chooser");
const comingSoon = document.getElementById("view-coming-soon");
const comingSoonCopy = document.getElementById("coming-soon-copy");
const status = document.getElementById("status");
const startLocal = document.getElementById("start-local");

function showChooser() {
  comingSoon.hidden = true;
  chooser.hidden = false;
}

function showComingSoon(copy) {
  comingSoonCopy.textContent = copy;
  chooser.hidden = true;
  comingSoon.hidden = false;
}

for (const button of document.querySelectorAll("[data-window]")) {
  button.addEventListener("click", () => {
    window.magiDesktop.windowControl(button.dataset.window);
  });
}

document.getElementById("connect-existing").addEventListener("click", () => {
  showComingSoon("Connecting to an existing magi-asp is not available yet.");
});

document.getElementById("open-settings").addEventListener("click", () => {
  showComingSoon("Settings will land here.");
});

document.getElementById("back").addEventListener("click", () => {
  showChooser();
});

startLocal.addEventListener("click", async () => {
  startLocal.disabled = true;
  status.classList.remove("is-error");
  status.textContent = "Starting magi-asp…";
  try {
    await window.magiDesktop.startLocal();
  } catch (error) {
    status.classList.add("is-error");
    status.textContent = error instanceof Error ? error.message : "Could not start magi-asp.";
    startLocal.disabled = false;
  }
});
