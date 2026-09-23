const startupMessage = document.getElementById("startup-message");
const startupProgress = document.getElementById("startup-progress");
const startupStep = document.getElementById("startup-step");
const progressTrack = document.querySelector(".progress-track");
const retry = document.getElementById("retry-startup");

// The shell reports its own steps and forwards the app's; both are absolute
// percentages, so the page needs no vocabulary of its own.
function showProgress({ message, percent }) {
  const value = Math.max(0, Math.min(1, Number(percent) || 0));
  startupMessage.textContent = message;
  startupStep.textContent = `${Math.round(value * 100)}%`;
  startupProgress.style.width = `${value * 100}%`;
  progressTrack.setAttribute("aria-valuenow", String(Math.round(value * 100)));
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
