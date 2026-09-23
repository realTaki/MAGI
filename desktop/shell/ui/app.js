const startupMessage = document.getElementById("startup-message");
const startupProgress = document.getElementById("startup-progress");
const startupStep = document.getElementById("startup-step");
const progressTrack = document.querySelector(".progress-track");
const retry = document.getElementById("retry-startup");

const steps = {
  checking: 0,
  clone: 1,
  asp: 2,
  magi: 3,
  "ui-dependencies": 4,
  "ui-build": 5,
  starting: 6,
};

for (const button of document.querySelectorAll("[data-window]")) {
  button.addEventListener("click", () => {
    window.magiDesktop.windowControl(button.dataset.window);
  });
}

function showProgress({ step, message }) {
  const value = steps[step] ?? 0;
  startupMessage.textContent = message;
  startupStep.textContent = `Step ${Math.min(value + 1, 6)} of 6`;
  startupProgress.style.width = `${(value / 6) * 100}%`;
  progressTrack.setAttribute("aria-valuenow", String(value));
  startupStep.classList.remove("is-error");
  retry.hidden = true;
}

window.magiDesktop.onStartupProgress(showProgress);
window.magiDesktop.onStartupError((message) => {
  startupMessage.textContent = message;
  startupStep.textContent = "Local startup failed.";
  startupStep.classList.add("is-error");
  retry.hidden = false;
});

retry.addEventListener("click", () => {
  retry.hidden = true;
  startupStep.classList.remove("is-error");
  startupMessage.textContent = "Retrying local MAGI…";
  void window.magiDesktop.retryStartup();
});
