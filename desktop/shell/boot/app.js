const startupStep = document.getElementById("startup-step");
const startupProgress = document.getElementById("startup-progress");
const startupPercent = document.getElementById("startup-percent");
const progressTrack = document.querySelector(".progress-track");
const retry = document.getElementById("retry-startup");

// The shell reports its own steps and forwards the app's; both are absolute
// percentages, so the page needs no vocabulary of its own.
function showProgress({ message, percent }) {
  const shown = Math.round(Math.max(0, Math.min(1, Number(percent) || 0)) * 100);
  startupStep.textContent = message;
  startupStep.classList.remove("is-error");
  startupPercent.textContent = `${shown}%`;
  startupProgress.style.width = `${shown}%`;
  progressTrack.setAttribute("aria-valuenow", String(shown));
  retry.hidden = true;
}

window.magiDesktop.onStartupProgress(showProgress);
window.magiDesktop.onStartupError((message) => {
  startupStep.textContent = message;
  startupStep.classList.add("is-error");
  retry.hidden = false;
});

retry.addEventListener("click", () => {
  retry.hidden = true;
  startupStep.classList.remove("is-error");
  startupStep.textContent = "Retrying…";
  void window.magiDesktop.retryStartup();
});
