const TOTAL_STEPS = 7;

const views = {
  startup: document.getElementById("view-startup"),
  github: document.getElementById("view-github"),
};

const startupMessage = document.getElementById("startup-message");
const startupProgress = document.getElementById("startup-progress");
const startupStep = document.getElementById("startup-step");
const progressTrack = document.querySelector(".progress-track");
const retry = document.getElementById("retry-startup");

const githubUpstream = document.getElementById("github-upstream");
const githubDevice = document.getElementById("github-device");
const githubDevicePanel = document.getElementById("github-device-panel");
const githubUserCode = document.getElementById("github-user-code");
const githubOpen = document.getElementById("github-open");
const githubTokenPanel = document.getElementById("github-token-panel");
const githubTokenInput = document.getElementById("github-token-input");
const githubTokenSubmit = document.getElementById("github-token-submit");
const githubStatus = document.getElementById("github-status");
const githubRetry = document.getElementById("github-retry");

const steps = {
  checking: 0,
  clone: 1,
  github: 2,
  asp: 3,
  magi: 4,
  "ui-dependencies": 5,
  "ui-build": 6,
  starting: 7,
};

function showView(name) {
  for (const [key, node] of Object.entries(views)) {
    node.hidden = key !== name;
  }
}

function showProgress({ step, message }) {
  const value = steps[step] ?? 0;
  startupMessage.textContent = message;
  startupStep.textContent = `Step ${Math.min(value + 1, TOTAL_STEPS)} of ${TOTAL_STEPS}`;
  startupProgress.style.width = `${(value / TOTAL_STEPS) * 100}%`;
  progressTrack.setAttribute("aria-valuenow", String(value));
  startupStep.classList.remove("is-error");
  retry.hidden = true;
}

function setGitHubStatus(message, isError = false) {
  githubStatus.textContent = message;
  githubStatus.classList.toggle("is-error", isError);
}

function setGitHubBusy(busy) {
  githubDevice.disabled = busy;
  githubTokenInput.disabled = busy;
  githubTokenSubmit.disabled = busy;
}

// The device flow can be unavailable (app not configured for it, network
// blocked), so a failure also opens the token route instead of dead-ending.
function reportGitHubError(error) {
  setGitHubStatus(error instanceof Error ? error.message : String(error), true);
  setGitHubBusy(false);
  githubTokenPanel.hidden = false;
  githubRetry.hidden = false;
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

githubRetry.addEventListener("click", () => {
  githubRetry.hidden = true;
  setGitHubStatus("Retrying GitHub setup…");
  showView("startup");
  void window.magiDesktop.retryStartup();
});

window.magiDesktop.onGitHubRequired(({ clientId, upstream, expired }) => {
  githubUpstream.textContent = upstream;
  githubDevice.hidden = !clientId;
  githubTokenPanel.hidden = Boolean(clientId);
  githubDevicePanel.hidden = true;
  githubUserCode.textContent = "····-····";
  githubRetry.hidden = true;
  setGitHubBusy(false);
  if (expired) {
    setGitHubStatus("Your saved GitHub sign-in stopped working. Sign in again.", true);
  } else if (clientId) {
    setGitHubStatus("Sign in so MAGI can fork the repository into your account.");
  } else {
    setGitHubStatus("This build has no GitHub OAuth client ID, so sign in with a token.");
  }
  showView("github");
});

githubDevice.addEventListener("click", async () => {
  setGitHubBusy(true);
  setGitHubStatus("Requesting a one-time code…");
  try {
    await window.magiDesktop.startGitHubDeviceSignIn();
  } catch (error) {
    reportGitHubError(error);
  }
});

githubTokenSubmit.addEventListener("click", async () => {
  if (githubTokenInput.value.trim() === "") {
    setGitHubStatus("Paste a token first.", true);
    return;
  }
  setGitHubBusy(true);
  setGitHubStatus("Checking the token…");
  try {
    await window.magiDesktop.submitGitHubToken(githubTokenInput.value);
  } catch (error) {
    reportGitHubError(error);
  }
});

githubTokenInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    githubTokenSubmit.click();
  }
});

githubOpen.addEventListener("click", () => {
  void window.magiDesktop.openGitHubVerification();
});

window.magiDesktop.onGitHubStatus((status) => {
  switch (status.step) {
    case "waiting":
      githubUserCode.textContent = status.userCode;
      githubDevicePanel.hidden = false;
      setGitHubStatus(
        `Waiting for you to approve the code on GitHub — it expires in ${status.expiresInMinutes} minutes and is already on your clipboard.`,
      );
      break;
    case "signed-in":
      setGitHubStatus(`Signed in as @${status.login}. Looking for your fork…`);
      break;
    case "forking":
      setGitHubStatus(`Creating your fork ${status.fork}…`);
      break;
    case "forked":
      setGitHubStatus(
        status.created ? `Fork created: ${status.fork}` : `Using your existing fork ${status.fork}`,
      );
      break;
    case "remote":
      setGitHubStatus(`Pointing the checkout at ${status.remote}…`);
      break;
    case "done":
      setGitHubStatus(`~/.magi/MAGI now tracks ${status.fork}.`);
      showView("startup");
      break;
    case "error":
      setGitHubStatus(status.message, true);
      setGitHubBusy(false);
      githubTokenPanel.hidden = false;
      githubRetry.hidden = false;
      break;
    default:
      break;
  }
});
