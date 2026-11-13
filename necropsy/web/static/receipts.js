// The evidence system (step 2) + the Kiro bridge buttons (step 5).
//
// Clicking an evidence chip reveals the raw GitHub JSON that backs its claim —
// the receipt. This is the trust moment: a sceptic sees real API data, not
// generated prose (product principle P1/P2). The raw JSON was rendered
// server-side, escaped by Jinja2; this script only toggles its visibility.

// -- evidence chips <-> receipts ------------------------------------------
for (const chip of document.querySelectorAll(".chip[data-evidence-id]")) {
  const id = chip.getAttribute("data-evidence-id");
  const receipt = document.getElementById(`receipt-${id}`);

  const toggle = () => {
    if (!receipt) return;
    const open = !receipt.hidden;
    // Close every other receipt first so the page stays calm.
    document.querySelectorAll(".receipt").forEach((r) => { r.hidden = true; });
    document.querySelectorAll(".chip[aria-expanded]").forEach((c) => c.setAttribute("aria-expanded", "false"));
    receipt.hidden = open;
    chip.setAttribute("aria-expanded", String(!open));
    if (!open) receipt.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  chip.addEventListener("click", toggle);
  chip.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
  });
}

// -- the "Resurrect in Kiro" button ---------------------------------------
const resurrect = document.querySelector(".resurrect-btn");
if (resurrect) {
  resurrect.addEventListener("click", async () => {
    const spec = resurrect.getAttribute("data-full-spec") || "";
    const kiroUrl = resurrect.getAttribute("data-kiro-url");
    const copied = await copyText(spec);
    toast(copied
      ? "Full revival spec copied. Paste it into a Kiro spec session to begin."
      : "Could not copy — select the plan steps manually.");
    if (copied && kiroUrl) {
      setTimeout(() => { window.location.assign(kiroUrl); }, 1400);
    }
  });
}

// -- copy-spec on each plan card ------------------------------------------
for (const btn of document.querySelectorAll(".copy-spec")) {
  btn.addEventListener("click", async () => {
    const spec = btn.getAttribute("data-spec") || "";
    const ok = await copyText(spec);
    flash(btn, ok ? "Copied" : "Copy failed");
    if (ok) toast("Spec copied.");
  });
}

// -- helpers --------------------------------------------------------------
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
}

let toastTimer = null;
function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => { el.hidden = true; }, 300);
  }, 2600);
}

function flash(btn, label) {
  const original = btn.textContent;
  btn.textContent = label;
  btn.classList.add("copied");
  setTimeout(() => {
    btn.textContent = original;
    btn.classList.remove("copied");
  }, 1400);
}
