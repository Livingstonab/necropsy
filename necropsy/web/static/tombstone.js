// The tombstone: inscribe a line, press the wax seal, share the stone.
//
// The inscription is the one thing a visitor writes. It is a line on the stone,
// not a form: an input with a brass underline, sealed with a wax stamp. Pressing
// the seal POSTs the line (stored per exhibit) and stamps it into place. The
// share button renders a 1200x630 PNG client-side — no server round-trip.
//
// Under prefers-reduced-motion the stamp is just a colour settle, no scale.

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const tomb = document.querySelector(".tombstone");

if (tomb) {
  const slug = tomb.getAttribute("data-slug");
  const input = document.getElementById("inscribe-input");
  const seal = document.getElementById("seal-btn");
  const row = document.getElementById("inscribe-row");
  const line = document.getElementById("tomb-inscription");
  const signature = document.getElementById("tomb-signature");
  const editBtn = document.getElementById("edit-inscription");

  // The seal is disabled until there is a non-empty line (R edge case).
  if (input && seal) {
    input.addEventListener("input", () => {
      seal.disabled = input.value.trim().length === 0;
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !seal.disabled) { e.preventDefault(); press(); }
    });
    seal.addEventListener("click", press);
  }

  if (editBtn) {
    editBtn.addEventListener("click", () => {
      if (row) row.classList.remove("hidden");
      if (input) { input.value = line ? line.textContent.trim() : ""; input.focus(); if (seal) seal.disabled = input.value.trim().length === 0; }
      editBtn.classList.add("hidden");
    });
  }

  async function press() {
    const text = input.value.trim().slice(0, 100);
    if (!text) return;
    seal.disabled = true;

    // Fade the old line out, if any, then POST and stamp the new one in.
    if (line && line.textContent.trim() && !reduced) {
      line.style.transition = "opacity 200ms ease";
      line.style.opacity = "0";
      await wait(200);
    }

    let ok = false;
    try {
      const res = await fetch(`/api/exhibit/${slug}/inscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inscription: text }),
      });
      ok = res.ok;
    } catch (e) { ok = false; }

    if (!ok) { seal.disabled = false; if (line) line.style.opacity = "1"; return; }

    if (line) {
      line.textContent = text;
      line.classList.add("inscribed");
      line.classList.remove("empty");
      line.style.opacity = "1";
      stamp(line);
    }
    if (signature) signature.classList.remove("hidden");
    if (row) row.classList.add("hidden");
    if (editBtn) editBtn.classList.remove("hidden");
    ensureEditButton();
  }

  function stamp(el) {
    // Ink settles: colour shift always; a small scale press unless reduced.
    el.style.color = "var(--brass-highlight)";
    if (reduced) return;
    el.animate(
      [{ transform: "scale(1)" }, { transform: "scale(0.96)" }, { transform: "scale(1)" }],
      { duration: 200, easing: "cubic-bezier(0.16,1,0.3,1)" }
    );
  }

  function ensureEditButton() {
    if (document.getElementById("edit-inscription")) return;
    const btn = document.createElement("button");
    btn.id = "edit-inscription";
    btn.className = "edit-inscription";
    btn.type = "button";
    btn.textContent = "✎ edit";
    btn.addEventListener("click", () => {
      if (row) row.classList.remove("hidden");
      if (input) { input.value = line.textContent.trim(); input.focus(); seal.disabled = false; }
      btn.classList.add("hidden");
    });
    tomb.appendChild(btn);
  }
}

// -- share card -----------------------------------------------------------
const shareBtn = document.getElementById("share-btn");
if (shareBtn) {
  shareBtn.addEventListener("click", () => {
    const repo = shareBtn.getAttribute("data-repo") || "a repository";
    const epitaph = shareBtn.getAttribute("data-epitaph") || "";
    const line = document.getElementById("tomb-inscription");
    const inscription = line && line.textContent.trim() ? line.textContent.trim() : "";
    try {
      renderShareCard(repo, epitaph, inscription);
    } catch (e) {
      // Canvas unavailable: fall back to copying a text version.
      const text = `${repo}\n\n“${epitaph}”${inscription ? `\n\n${inscription}\n— you` : ""}\n\nNECROPSY — Kiroween 2026`;
      navigator.clipboard?.writeText(text);
      toast("Share image unavailable; copied the text instead.");
    }
  });
}

function renderShareCard(repo, epitaph, inscription) {
  const W = 1200, H = 630;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("no 2d context");

  // Background + spotlight.
  ctx.fillStyle = "#0a0a0c";
  ctx.fillRect(0, 0, W, H);
  const grad = ctx.createRadialGradient(W / 2, 0, 0, W / 2, 0, H);
  grad.addColorStop(0, "rgba(255,217,160,0.16)");
  grad.addColorStop(1, "rgba(255,217,160,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);

  ctx.textAlign = "center";

  // Repo name (Playfair).
  ctx.fillStyle = "#b08d57";
  ctx.font = "500 38px 'Playfair Display', serif";
  ctx.fillText(repo, W / 2, 110);

  // Epitaph, wrapped, serif.
  ctx.fillStyle = "#e8e2d4";
  ctx.font = "italic 500 44px 'Playfair Display', serif";
  wrap(ctx, `“${epitaph}”`, W / 2, 250, W - 200, 56);

  // Inscription + signature.
  if (inscription) {
    ctx.fillStyle = "#d4b87a";
    ctx.font = "italic 30px 'Cormorant Garamond', serif";
    ctx.fillText(inscription, W / 2, 470);
    ctx.fillStyle = "#8a8578";
    ctx.font = "28px 'Caveat', cursive";
    ctx.fillText("— you", W / 2, 510);
  } else {
    ctx.fillStyle = "#8a8578";
    ctx.font = "22px 'JetBrains Mono', monospace";
    ctx.fillText("Inscribe a line at necropsy", W / 2, 480);
  }

  // Candle glyph (a small flame) + footer.
  ctx.fillStyle = "#c4553a";
  ctx.beginPath();
  ctx.ellipse(70, H - 60, 7, 14, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#8a8578";
  ctx.font = "20px 'JetBrains Mono', monospace";
  ctx.fillText("NECROPSY — Kiroween 2026", W / 2, H - 50);

  const safe = repo.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  c.toBlob((blob) => {
    if (!blob) { toast("Could not render the image."); return; }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `necropsy-${safe}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast("Tombstone image downloaded.");
  }, "image/png");
}

function wrap(ctx, text, x, y, maxWidth, lineHeight) {
  const words = text.split(" ");
  let line = "";
  const lines = [];
  for (const word of words) {
    const test = line ? `${line} ${word}` : word;
    if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = word; }
    else { line = test; }
  }
  if (line) lines.push(line);
  const startY = y - ((lines.length - 1) * lineHeight) / 2;
  lines.forEach((l, i) => ctx.fillText(l, x, startY + i * lineHeight));
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

let toastTimer = null;
function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove("show"); setTimeout(() => { el.hidden = true; }, 300); }, 2600);
}
