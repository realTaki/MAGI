const startupMessage = document.getElementById("startup-message");
const startupProgress = document.getElementById("startup-progress");
const startupPercent = document.getElementById("startup-percent");
const startupStep = document.getElementById("startup-step");
const progressTrack = document.querySelector(".progress-track");
const retry = document.getElementById("retry-startup");

const HINT = "First launch installs dependencies — this can take a few minutes.";

// The shell reports its own steps and forwards the app's; both are absolute
// percentages, so the page needs no vocabulary of its own.
function showProgress({ message, percent }) {
  const shown = Math.round(Math.max(0, Math.min(1, Number(percent) || 0)) * 100);
  startupMessage.textContent = message;
  startupMessage.classList.remove("is-error");
  startupPercent.textContent = `${shown}%`;
  startupProgress.style.width = `${shown}%`;
  progressTrack.setAttribute("aria-valuenow", String(shown));
  startupStep.textContent = HINT;
  startupStep.classList.remove("is-error");
  retry.hidden = true;
}

window.magiDesktop.onStartupProgress(showProgress);
window.magiDesktop.onStartupError((message) => {
  startupMessage.textContent = message;
  startupMessage.classList.add("is-error");
  startupStep.textContent = "Local startup failed.";
  startupStep.classList.add("is-error");
  retry.hidden = false;
});

retry.addEventListener("click", () => {
  retry.hidden = true;
  startupMessage.classList.remove("is-error");
  startupStep.classList.remove("is-error");
  startupMessage.textContent = "Retrying local MAGI…";
  void window.magiDesktop.retryStartup();
});
