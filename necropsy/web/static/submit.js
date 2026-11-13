// Submit-and-poll for the live analysis flow (design 8.3).
//
// No-JS users still get a working form: it posts and the server could redirect.
// With JS, we POST, receive a job id, and poll /api/jobs/{id} every 750 ms,
// updating an aria-live status line with the stage the pipeline reports — honest
// progress, not a fake timer. On done we navigate to the exhibit; on failure we
// show the error message inline rather than leaving the page.

const POLL_MS = 750;
const MAX_WAIT_MS = 40000; // client-side cap (design 2.4)

const form = document.getElementById("analyze-form");
const input = document.getElementById("url");
const statusLine = document.getElementById("status-line");
const stage = document.getElementById("status-stage");
const button = form ? form.querySelector("button") : null;

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = input.value.trim();
    if (!url) return;

    setBusy(true);
    say("Preparing the examination table…");
    stage.classList.add("active");

    try {
      const res = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        fail(data.error);
        return;
      }
      await poll(data.job_id);
    } catch (err) {
      say("The examination could not reach the server. Try again.");
      setBusy(false);
      stage.classList.remove("active");
    }
  });
}

async function poll(jobId) {
  const started = Date.now();
  while (Date.now() - started < MAX_WAIT_MS) {
    await sleep(POLL_MS);
    let data;
    try {
      const res = await fetch(`/api/jobs/${jobId}`, { headers: { Accept: "application/json" } });
      data = await res.json();
    } catch (err) {
      continue; // transient; keep polling until the cap
    }
    if (!data.ok) {
      say("The job could not be found.");
      break;
    }
    say(data.status_line || "Working…");
    if (data.state === "done" && data.exhibit_url) {
      window.location.assign(data.exhibit_url);
      return;
    }
    if (data.state === "failed") {
      fail(data.error);
      return;
    }
  }
  say("This is taking longer than expected. The analysis may still complete; try the wall shortly.");
  setBusy(false);
  stage.classList.remove("active");
}

function fail(error) {
  const code = error && error.code ? `[${error.code}] ` : "";
  const message = (error && error.message) || "The examination could not be completed.";
  say(code + message);
  setBusy(false);
  stage.classList.remove("active");
}

function say(text) {
  if (statusLine) statusLine.textContent = text;
}

function setBusy(busy) {
  if (button) {
    button.disabled = busy;
    button.textContent = busy ? "Examining…" : "Begin post-mortem";
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
