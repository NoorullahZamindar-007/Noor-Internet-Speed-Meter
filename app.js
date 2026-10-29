const startButton = document.getElementById("startButton");
const progressWrap = document.getElementById("progressWrap");
const progressBar = document.getElementById("progressBar");
const statusText = document.getElementById("statusText");
const timeText = document.getElementById("timeText");
const errorBox = document.getElementById("errorBox");
const downloadResult = document.getElementById("downloadResult");
const uploadResult = document.getElementById("uploadResult");
const pingResult = document.getElementById("pingResult");
const serverResult = document.getElementById("serverResult");

let pollTimer = null;
let elapsedTimer = null;
let startedAt = null;
// Guards against overlapping polls: setInterval does not wait for the
// previous fetch, so without these a slow connection lets responses
// resolve out of order (rewinding the progress bar) and lets a poll
// already in flight overwrite the timeout/finish message when it lands.
let pollInFlight = false;
let pollingActive = false;

function setProgress(progress) {
  // Keep the visual bar and the ARIA value in sync so assistive technology
  // can report progress; clamp to guard against out-of-range values.
  const clamped = Math.max(0, Math.min(100, Math.round(progress)));
  progressBar.style.width = `${clamped}%`;
  progressWrap.setAttribute("aria-valuenow", String(clamped));
}

function resetResults() {
  downloadResult.textContent = "--";
  uploadResult.textContent = "--";
  pingResult.textContent = "--";
  serverResult.textContent = "--";
  errorBox.hidden = true;
  errorBox.textContent = "";
  timeText.textContent = "A full test usually takes 60-120 seconds.";
  setProgress(0);
}

function finish() {
  pollingActive = false;
  startButton.disabled = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

function startElapsedTimer() {
  startedAt = Date.now();
  elapsedTimer = setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    timeText.textContent = `Elapsed time: ${seconds} seconds. This can take about 1-2 minutes.`;
  }, 1000);
}

async function pollStatus(testId) {
  if (pollInFlight || !pollingActive) return;
  pollInFlight = true;
  try {
  const response = await fetch(`/api/speedtest/status/${testId}`);
  if (!pollingActive) return;
  if (!response.ok) {
    throw new Error("Could not read speed test status.");
  }

  const data = await response.json();
  setProgress(data.progress);
  statusText.textContent = `Status: ${data.status}`;

  if (data.status === "completed") {
    downloadResult.textContent = data.result.download_mbps.toFixed(2);
    uploadResult.textContent = data.result.upload_mbps.toFixed(2);
    pingResult.textContent = data.result.ping_ms.toFixed(2);
    serverResult.textContent = `${data.result.server_name} (${data.result.server_location})`;
    statusText.textContent = "Speed test completed.";
    finish();
  }

  if (data.status === "failed") {
    errorBox.textContent = data.error || "Speed test failed.";
    errorBox.hidden = false;
    statusText.textContent = "Speed test failed.";
    finish();
  }
  } finally {
    pollInFlight = false;
  }
}

startButton.addEventListener("click", async () => {
  resetResults();
  startButton.disabled = true;
  statusText.textContent = "Starting speed test...";
  startElapsedTimer();

  try {
    const response = await fetch("/api/speedtest/start", { method: "POST" });
    if (!response.ok) {
      // Surface the server's reason (e.g. the 429 "too many active tests"
      // message) instead of a generic failure notice.
      let message = "Could not start speed test.";
      try {
        const errBody = await response.json();
        if (errBody && typeof errBody.detail === "string" && errBody.detail) {
          message = errBody.detail;
        }
      } catch (_) {
        // Non-JSON error body: keep the generic message.
      }
      // The server may send a Retry-After hint (RFC 7231 §7.1.3): tell the
      // user when it is worth trying again instead of retrying blindly.
      const retrySeconds = Number.parseInt(response.headers.get("retry-after") ?? "", 10);
      if (Number.isFinite(retrySeconds) && retrySeconds > 0) {
        message += ` Please try again in about ${retrySeconds} seconds.`;
      }
      throw new Error(message);
    }

    const data = await response.json();
    statusText.textContent = "Speed test running...";
    // Guard against a wedged worker or a stale test id: stop polling after
    // ten minutes instead of hitting the status endpoint forever.
    const POLL_TIMEOUT_MS = 10 * 60 * 1000;
    const pollDeadline = Date.now() + POLL_TIMEOUT_MS;
    pollingActive = true;
    pollTimer = setInterval(() => {
      if (Date.now() > pollDeadline) {
        errorBox.textContent = "The speed test is taking too long. Please try again.";
        errorBox.hidden = false;
        statusText.textContent = "Speed test timed out.";
        finish();
        return;
      }
      pollStatus(data.test_id).catch((error) => {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
        finish();
      });
    }, 1000);
    await pollStatus(data.test_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    statusText.textContent = "Unable to start speed test.";
    finish();
  }
});
