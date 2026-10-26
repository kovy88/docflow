// Records real walkthroughs of the public Docflow demo into public/clips/*.mp4.
// Frames come from the CDP screencast (sharper than Playwright's recordVideo).
import { chromium } from "playwright";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const BASE = "https://docflow.logicflow.cz";
const VIEWPORT = { width: 1440, height: 810 };
const SCALE = 1920 / VIEWPORT.width;

// Visible cursor: headless Chrome draws none, viewers need to see what gets clicked.
const CURSOR = `
  addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "__cursor";
    c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M4 2l16 9.5-7 1.5-3.5 6.5z" fill="#1f242b" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: "fixed", left: "-50px", top: "-50px", zIndex: 99999, pointerEvents: "none", transition: "transform 120ms" });
    document.body.appendChild(c);
    addEventListener("mousemove", (e) => { c.style.left = e.clientX - 4 + "px"; c.style.top = e.clientY - 2 + "px"; });
    addEventListener("mousedown", () => (c.style.transform = "scale(0.82)"));
    addEventListener("mouseup", () => (c.style.transform = "scale(1)"));
  });`;

async function record(name, script) {
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: SCALE });
  await page.addInitScript(CURSOR);
  const cdp = await page.context().newCDPSession(page);
  const dir = `frames/${name}`;
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });

  const frames = [];
  let recording = false;
  cdp.on("Page.screencastFrame", async ({ data, sessionId, metadata }) => {
    if (recording) {
      const file = `${dir}/${String(frames.length).padStart(5, "0")}.jpg`;
      writeFileSync(file, Buffer.from(data, "base64"));
      frames.push({ file, t: metadata.timestamp });
    }
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });

  const pause = (ms) => page.waitForTimeout(ms);
  const start = async () => {
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, everyNthFrame: 1 });
    recording = true;
  };
  await script(page, { pause, start });
  // screencast only emits on repaint; a final frame pins the clip length
  frames.push({ file: frames.at(-1).file, t: Date.now() / 1000 });
  await cdp.send("Page.stopScreencast");
  await browser.close();

  // concat demuxer with real frame durations keeps the timing of the session
  const list = frames
    .slice(0, -1)
    .map((f, i) => `file '${f.file.split("/").at(-1)}'\nduration ${(frames[i + 1].t - f.t).toFixed(4)}`)
    .join("\n");
  writeFileSync(`${dir}/list.txt`, `${list}\nfile '${frames.at(-2).file.split("/").at(-1)}'\n`);
  mkdirSync("public/clips", { recursive: true });
  execFileSync("ffmpeg", [
    "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", `${dir}/list.txt`,
    "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
    "-c:v", "libx264", "-crf", "16", "-preset", "slow", `public/clips/${name}.mp4`,
  ]);
  const secs = frames.at(-1).t - frames[0].t;
  console.log(`${name}: ${frames.length} frames, ${secs.toFixed(1)} s`);
}

// Moves the cursor smoothly to an element and clicks it.
async function clickSmooth(page, locator, steps = 28) {
  await locator.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "center" }));
  await page.waitForTimeout(700);
  const box = await locator.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps });
  await page.waitForTimeout(250);
  await page.mouse.down();
  await page.waitForTimeout(90);
  await page.mouse.up();
}

await record("lease", async (page, { pause, start }) => {
  await page.goto(`${BASE}/documents/38`);
  await page.waitForSelector(".react-pdf__Page canvas", { timeout: 60000 });
  await pause(2500);
  await page.mouse.move(700, 420);
  await start();
  await pause(2200);
  const sources = page.getByText(/Zdroj · str/);
  await clickSmooth(page, sources.nth(5)); // Nájemné
  await pause(2600);
  await clickSmooth(page, sources.nth(9)); // Celková platba
  await pause(2600);
  await clickSmooth(page, sources.nth(10)); // Kauce, page 2
  await pause(3200);
});

await record("invoice", async (page, { pause, start }) => {
  await page.goto(`${BASE}/documents/4`);
  await page.waitForSelector(".react-pdf__Page canvas", { timeout: 60000 });
  await pause(2500);
  await page.mouse.move(600, 500);
  await start();
  await pause(1200);
  const finding = page.getByText(/ale Celkem k úhradě/);
  const box = await finding.boundingBox();
  await page.mouse.move(box.x + 60, box.y + box.height / 2, { steps: 30 });
  await pause(3500);
});
