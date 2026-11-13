// Gallery wall: subtle parallax and the focus-brighten on the submit input.
//
// Parallax shifts each card a few pixels toward the cursor — enough to feel like
// the wall has depth, not enough to distract. Disabled on touch and under
// prefers-reduced-motion, where it would be noise or motion sickness (R-6.5).

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const coarse = window.matchMedia("(pointer: coarse)").matches;
const wall = document.querySelector(".graveyard-grid");

if (wall && !reduced && !coarse) {
  const cards = Array.from(wall.querySelectorAll("[data-parallax]"));
  let raf = null;

  wall.addEventListener("mousemove", (e) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      for (const card of cards) {
        const rect = card.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        // Up to ±8px, scaled by distance from the card centre.
        const dx = Math.max(-8, Math.min(8, (e.clientX - cx) / 40));
        const dy = Math.max(-8, Math.min(8, (e.clientY - cy) / 40));
        card.style.transform = `translate(${dx}px, ${dy}px)`;
      }
    });
  });

  wall.addEventListener("mouseleave", () => {
    for (const card of cards) card.style.transform = "";
  });
}

// Focus-brighten: the spotlight over the submit placard warms when the input
// gains focus (design step 4.2). Pure class toggle; CSS does the transition.
const input = document.getElementById("url");
const panel = document.querySelector(".submit-panel");
if (input && panel) {
  input.addEventListener("focus", () => panel.classList.add("focused"));
  input.addEventListener("blur", () => panel.classList.remove("focused"));
}
