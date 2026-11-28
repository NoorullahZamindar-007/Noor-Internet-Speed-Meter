const startButton = document.getElementById("startButton");
const cancelButton = document.getElementById("cancelButton");
const progressWrap = document.getElementById("progressWrap");
const progressBar = document.getElementById("progressBar");
const statusText = document.getElementById("statusText");
const timeText = document.getElementById("timeText");
const errorBox = document.getElementById("errorBox");
const downloadResult = document.getElementById("downloadResult");
const uploadResult = document.getElementById("uploadResult");
const pingResult = document.getElementById("pingResult");
const serverResult = document.getElementById("serverResult");
const historyList = document.getElementById("historyList");
const historyEmpty = document.getElementById("historyEmpty");
const historyChart = document.getElementById("historyChart");
const historyChartLegend = document.getElementById("historyChartLegend");

let pollTimer = null;
let elapsedTimer = null;
let startedAt = null;
// The test currently being polled, so the Cancel button knows which
// test to cancel; cleared by finish() alongside the timers.
let currentTestId = null;
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
// Consecutive network-level fetch failures (connection refused, dropped
// Wi-Fi): unlike HTTP 500s these never counted toward the error limit
// above, so a dead server left the user staring at a frozen "running"
// bar for the full 10-minute poll deadline — even though a server
// restart also wipes the in-memory record, so the test can never
// recover. The limit is deliberately much larger than 5: a brief
// Wi-Fi blip must not kill a healthy 2-minute test.
let consecutiveFetchFailures = 0;
const MAX_CONSECUTIVE_FETCH_FAILURES = 30;

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

function finish(completed = false) {
  pollingActive = false;
  startButton.disabled = false;
  cancelButton.disabled = true;
  currentTestId = null;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
  // The elapsed timer is gone but its last "Elapsed time: N seconds"
  // line is not: without this it sat under "Speed test completed."
  // forever. A finished run reports its duration; any other ending
  // restores the default hint.
  if (startedAt !== null) {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    timeText.textContent = completed
      ? `Completed in ${seconds} seconds.`
      : "A full test usually takes 60-120 seconds.";
    startedAt = null;
  }
  // A completed run belongs in the past-tests list below the results:
  // refresh it so the list shows the run that just finished without
  // needing a page reload.
  if (completed) {
    loadHistory();
  }
}

// Format a record's timestamp for the past-tests list: an ISO string
// from the API, shown in the browser's local time. A missing or
// unparseable value (an older record, a hand-edited history file)
// yields no time instead of "Invalid Date".
function formatHistoryTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function renderHistoryChart(tests) {
  // A simple bar chart of past speeds, drawn on a canvas with no
  // dependencies: one pair of bars (download, upload) per completed
  // test, oldest to newest, so a trend across runs is visible at a
  // glance instead of only in the list below. The list stays the
  // accessible record — the canvas is labelled, not read aloud.
  if (!historyChart || !historyChartLegend) return;
  const context = historyChart.getContext("2d");
  if (!context) return;
  const completed = tests
    .filter((test) => test && test.status === "completed" && test.result)
    .slice(0, 10)
    .reverse();
  const width = historyChart.width;
  const height = historyChart.height;
  context.clearRect(0, 0, width, height);
  if (completed.length === 0) {
    historyChart.hidden = true;
    historyChartLegend.hidden = true;
    return;
  }
  historyChart.hidden = false;
  historyChartLegend.hidden = false;
  const values = completed.flatMap((test) => [
    Number(test.result.download_mbps) || 0,
    Number(test.result.upload_mbps) || 0,
  ]);
  // Guard the scale against a non-numeric or all-zero set: without the
  // Math.max(1, ...) floor a 0 Mbps run would divide by zero and draw
  // nothing (or NaN heights).
  const maxValue = Math.max(1, ...values);
  const plotHeight = height - 8;
  const groupWidth = width / completed.length;
  const barWidth = Math.max(4, groupWidth / 4);
  completed.forEach((test, index) => {
    const groupX = index * groupWidth;
    const downloadHeight = ((Number(test.result.download_mbps) || 0) / maxValue) * plotHeight;
    const uploadHeight = ((Number(test.result.upload_mbps) || 0) / maxValue) * plotHeight;
    context.fillStyle = "#0f8b8d";
    context.fillRect(groupX + groupWidth / 2 - barWidth - 2, height - downloadHeight, barWidth, downloadHeight);
    context.fillStyle = "#5aa469";
    context.fillRect(groupX + groupWidth / 2 + 2, height - uploadHeight, barWidth, uploadHeight);
  });
  // Baseline so a flat (all-zero) chart still reads as a chart, not a blank canvas.
  context.fillStyle = "#dfe5ef";
  context.fillRect(0, height - 1, width, 1);
}

function renderHistory(tests) {
  // Only completed tests with a result are worth listing; a failed or
  // cancelled run has no metrics to show. The endpoint already returns
  // newest first, so the first entries are the most recent runs.
  const completed = tests.filter(
    (test) => test && test.status === "completed" && test.result,
  );
  historyList.replaceChildren();
  renderHistoryChart(tests);
  if (completed.length === 0) {
    historyEmpty.hidden = false;
    return;
  }
  historyEmpty.hidden = true;
  for (const test of completed.slice(0, 10)) {
    const item = document.createElement("li");
    const result = test.result;
    // Build the line with textContent, not innerHTML: the server name
    // comes from speedtest server data, so it must never be parsed as
    // markup.
    const serverName = result.server_name || "Unknown";
    const serverLocation = result.server_location || "Unknown";
    const when = formatHistoryTime(test.finished_at || test.started_at);
    item.textContent =
      `${fmtMetric(result.download_mbps)} Mbps down · ` +
      `${fmtMetric(result.upload_mbps)} Mbps up · ` +
      `${fmtMetric(result.ping_ms)} ms ping — ` +
      `${serverName} (${serverLocation})` +
      (when ? ` · ${when}` : "");
    historyList.appendChild(item);
  }
}

async function loadHistory() {
  // Past results live behind /api/speedtest/history, but the page
  // never showed them: a completed test vanished from the page as
  // soon as a new one started. A failed load leaves the previous
  // list (or the empty note) in place instead of wiping it.
  let response;
  try {
    response = await fetch("/api/speedtest/history");
  } catch (_) {
    return;
  }
  if (!response.ok) return;
  let tests;
  try {
    tests = await response.json();
  } catch (_) {
    return;
  }
  if (!Array.isArray(tests)) return;
  renderHistory(tests);
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
    // just let the next 1 s tick retry. But a server that is gone
    // for good (restart, network down) fails fast after ~30 s
    // instead of hanging until the 10-minute poll deadline.
    // Say the server is unreachable here, not that it "keeps
    // reporting an error": the fetch itself failed, so the server
    // reported nothing — the old wording sent users looking for a
    // server-side problem when their connection was the issue.
    consecutiveFetchFailures += 1;
    if (consecutiveFetchFailures >= MAX_CONSECUTIVE_FETCH_FAILURES) {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    return;
  }
  // Any response at all means the server is reachable again.
  consecutiveFetchFailures = 0;
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
  if (!data || typeof data !== "object" || !["pending", "running", "completed", "failed", "cancelled"].includes(data.status)) {
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
      finish(true);
    }
  }

  if (data.status === "failed") {
    errorBox.textContent = data.error || "Speed test failed.";
    errorBox.hidden = false;
    statusText.textContent = "Speed test failed.";
    finish();
  }

  if (data.status === "cancelled") {
    // The user cancelled from another tab, or the cancel request
    // landed after this poll started: stop polling with the same
    // message the cancel button shows, not an error.
    statusText.textContent = "Speed test cancelled.";
    finish();
  }
  } finally {
    pollInFlight = false;
  }
}

cancelButton.addEventListener("click", async () => {
  if (!currentTestId) return;
  try {
    const response = await fetch(`/api/speedtest/cancel/${currentTestId}`, { method: "POST" });
    if (!response.ok) {
      // Already finished, failed, or gone: the poll resolves the real
      // state on its next tick, so there is nothing to show here.
      return;
    }
    statusText.textContent = "Speed test cancelled.";
    finish();
  } catch (_) {
    // The cancel request itself failed (network down): leave the poll
    // running — the test finishes on its own and the poll reports it.
  }
});

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
    currentTestId = data.test_id;
    cancelButton.disabled = false;
    // Guard against a wedged worker or a stale test id: stop polling after
    // ten minutes instead of hitting the status endpoint forever.
    const POLL_TIMEOUT_MS = 10 * 60 * 1000;
    const pollDeadline = Date.now() + POLL_TIMEOUT_MS;
    pollingActive = true;
    consecutivePollErrors = 0;
    consecutiveFetchFailures = 0;
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
    // The elapsed timer started before the POST; without this the stale
    // "Elapsed time: N seconds..." line stays under the error message.
    timeText.textContent = "A full test usually takes 60-120 seconds.";
    finish();
  }
});

// Show past tests as soon as the page loads, so results from earlier
// runs are visible without starting a new test.
loadHistory();
