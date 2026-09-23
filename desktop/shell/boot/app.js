const startupStep = document.getElementById("startup-step");
const startupProgress = document.getElementById("startup-progress");
const startupPercent = document.getElementById("startup-percent");
const progressTrack = document.querySelector(".progress-track");
const errorPanel = document.getElementById("startup-error-panel");
const errorText = document.getElementById("startup-error");
const copyError = document.getElementById("copy-startup-error");
const retry = document.getElementById("retry-startup");

// The shell reports its own steps and forwards the app's; both are absolute
// percentages, so the page needs no vocabulary of its own.
function showProgress({ message, percent }) {
  const shown = Math.round(Math.max(0, Math.min(1, Number(percent) || 0)) * 100);
  startupStep.textContent = message;
  startupPercent.textContent = `${shown}%`;
  startupProgress.style.width = `${shown}%`;
  progressTrack.setAttribute("aria-valuenow", String(shown));
  errorPanel.hidden = true;
  copyError.textContent = "Copy error";
}

window.magiDesktop.onStartupProgress(showProgress);
window.magiDesktop.onStartupError((message) => {
  startupStep.textContent = "Startup failed.";
  errorText.value = String(message || "Unknown startup error.");
  copyError.textContent = "Copy error";
  errorPanel.hidden = false;
});

retry.addEventListener("click", () => {
  errorPanel.hidden = true;
  startupStep.textContent = "Retrying…";
  void window.magiDesktop.retryStartup();
});

copyError.addEventListener("click", async () => {
  try {
    await window.magiDesktop.copyText(errorText.value);
    copyError.textContent = "Copied";
  } catch {
    errorText.focus();
    errorText.select();
    copyError.textContent = "Selected — press Ctrl/Cmd+C";
  }
});
