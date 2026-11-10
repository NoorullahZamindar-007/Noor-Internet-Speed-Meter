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
// Consecutive non-OK status responses (404 fails fast above, so this is
// 500s and friends): a deterministic server error used to be swallowed
// on every 1 s tick, leaving the user staring at a frozen "running" bar
// for the full 10-minute poll deadline. Fail after a few in a row so
// the existing .catch shows a clear message instead.
let consecutivePollErrors = 0;
const MAX_CONSECUTIVE_POLL_ERRORS = 5;

function setProgress(progress) {
  // Keep the visual bar and the ARIA value in sync so assistive technology
  // can report progress; clamp to guard against out-of-range values, and
  // fall back to 0 for a missing/non-numeric value — without this,
  // Math.round(undefined) is NaN and the bar's width becomes "NaN%".
  const value = Number(progress);
  const clamped = Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : 0;
  progressBar.style.width = `${clamped}%`;
  progressWrap.setAttribute("aria-valuenow", String(clamped));
}

// Format one result metric: a missing or non-numeric value (e.g. a
// malformed status payload) used to throw on .toFixed below, aborting
// the poll with a generic error even though the test had completed.
function fmtMetric(value) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(2) : "--";
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
  // Count one transient poll failure; give up after several in a row so
  // a deterministic server error still fails fast with a clear message.
  function noteTransientPollError() {
    consecutivePollErrors += 1;
    if (consecutivePollErrors >= MAX_CONSECUTIVE_POLL_ERRORS) {
      throw new Error("The server keeps reporting an error for this test. Please try again.");
    }
  }
  let response;
  try {
    response = await fetch(`/api/speedtest/status/${testId}`);
  } catch (_) {
    // A momentary network blip (a dropped Wi-Fi second, a 502/503)
    // must not kill the whole test: the server keeps running, so
    // just let the next 1 s tick retry. The 10-minute poll deadline
    // still bounds a wedged test.
    return;
  }
  if (!pollingActive) return;
  if (response.status === 404) {
    // The record is gone (a server restart wipes the in-memory
    // store, or it was evicted past the history cap): retrying
    // cannot help, so stop with a specific message.
    throw new Error("That speed test is no longer available — the server may have restarted. Please start a new test.");
  }
  if (!response.ok) {
    // A single failure may be transient (the server is mid-restart, a
    // proxy hiccup), so keep retrying; several in a row mean the error
    // is deterministic and the next ticks cannot help either.
    noteTransientPollError();
    return;
  }
  consecutivePollErrors = 0;

  let data;
  try {
    data = await response.json();
  } catch (_) {
    // A malformed body (a proxy error page, a truncated response) used
    // to throw out of here and kill the whole poll via the interval's
    // .catch, even though the test was still running fine server-side.
    // Treat it like any other transient poll failure instead.
    noteTransientPollError();
    return;
  }
  if (!data || typeof data !== "object" || !["pending", "running", "completed", "failed"].includes(data.status)) {
    // A well-formed body with an unusable shape (a JSON string from a
    // proxy, or a status this client does not know): same treatment —
    // one bad tick must not end a healthy test.
    noteTransientPollError();
    return;
  }
  setProgress(data.progress);
  statusText.textContent = `Status: ${data.status}`;

  if (data.status === "completed") {
    // A completed record must carry a result; without this guard a
    // null result throws on .toFixed below, finish() never runs, and
    // the poll timer keeps crashing on the same status until timeout.
    if (!data.result) {
      errorBox.textContent = "Speed test completed but returned no result. Please try again.";
      errorBox.hidden = false;
      statusText.textContent = "Speed test failed.";
      finish();
    } else {
      downloadResult.textContent = fmtMetric(data.result.download_mbps);
      uploadResult.textContent = fmtMetric(data.result.upload_mbps);
      pingResult.textContent = fmtMetric(data.result.ping_ms);
      // The metrics above already guard missing values, but the server
      // fields did not: a completed payload without them rendered as
      // "undefined (undefined)". Fall back to "Unknown" like the API does.
      const serverName = data.result.server_name || "Unknown";
      const serverLocation = data.result.server_location || "Unknown";
      serverResult.textContent = `${serverName} (${serverLocation})`;
      statusText.textContent = "Speed test completed.";
      finish();
    }
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
    // A 200 with a malformed body (a proxy error page parsed as JSON,
    // or a payload without a test id) used to send the poll to
    // /api/speedtest/status/undefined, which 404s with the misleading
    // "server may have restarted" message. Fail here instead.
    if (!data || typeof data.test_id !== "string" || !data.test_id) {
      throw new Error("Could not start speed test.");
    }
    statusText.textContent = "Speed test running...";
    // Guard against a wedged worker or a stale test id: stop polling after
    // ten minutes instead of hitting the status endpoint forever.
    const POLL_TIMEOUT_MS = 10 * 60 * 1000;
    const pollDeadline = Date.now() + POLL_TIMEOUT_MS;
    pollingActive = true;
    consecutivePollErrors = 0;
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
