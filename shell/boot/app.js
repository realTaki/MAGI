const startupStep = document.getElementById("startup-step");
const startupProgress = document.getElementById("startup-progress");
const startupPercent = document.getElementById("startup-percent");
const progressTrack = document.querySelector(".progress-track");
const errorPanel = document.getElementById("startup-error-panel");
const errorText = document.getElementById("startup-error");
const copyError = document.getElementById("copy-startup-error");
const copyStatus = document.getElementById("copy-status");
const retry = document.getElementById("retry-startup");

function resetCopy() {
  copyError.dataset.copied = "false";
  copyError.setAttribute("aria-label", "Copy error");
  copyError.title = "Copy error";
  copyStatus.textContent = "";
}

// The shell reports its own steps and forwards the app's; both are absolute
// percentages, so the page needs no vocabulary of its own.
function showProgress({ message, percent }) {
  const shown = Math.round(Math.max(0, Math.min(1, Number(percent) || 0)) * 100);
  startupStep.textContent = message;
  startupPercent.textContent = `${shown}%`;
  startupProgress.style.width = `${shown}%`;
  progressTrack.setAttribute("aria-valuenow", String(shown));
  errorPanel.hidden = true;
  resetCopy();
}

window.magiDesktop.onStartupProgress(showProgress);
window.magiDesktop.onStartupError((message) => {
  startupStep.textContent = "Startup failed.";
  errorText.value = String(message || "Unknown startup error.");
  resetCopy();
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
    copyError.dataset.copied = "true";
    copyError.setAttribute("aria-label", "Error copied");
    copyError.title = "Error copied";
    copyStatus.textContent = "Error copied";
  } catch {
    errorText.focus();
    errorText.select();
    copyError.dataset.copied = "false";
    copyError.setAttribute("aria-label", "Error selected; press Ctrl or Command C to copy");
    copyError.title = "Error selected; press Ctrl/Cmd+C to copy";
    copyStatus.textContent = "Error selected; press Ctrl or Command C to copy";
  }
});
