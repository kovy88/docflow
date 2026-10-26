import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// Colours from logicflow-ui/tokens.css (Docflow accent).
const C = {
  accent: "#2f5bd3",
  accentSoft: "#eaf0fc",
  bg: "#fafaf9",
  surface: "#ffffff",
  subtle: "#f1f3f6",
  ink: "#1f242b",
  muted: "#5e6672",
  line: "#dde0e5",
  ok: "#1f7a4d",
  okBg: "#e8f5ee",
  err: "#b42318",
  errBg: "#fdecea",
  mark: "#fde68a",
};

// Satoshi woff2 files (400/500/700) from fontshare.com go to public/fonts/; not committed (licence).
const FONT = "Satoshi, ui-sans-serif, system-ui, sans-serif";
const fontFaces = [400, 500, 700]
  .map(
    (w) =>
      `@font-face{font-family:Satoshi;font-weight:${w};src:url(${staticFile(`fonts/Satoshi-${w}.woff2`)}) format("woff2");}`,
  )
  .join("");

// Scene lengths in frames (30 fps).
const S = { hook: 105, before: 345, bridge: 60, lease: 474, invoice: 150, sheet: 270, cta: 270 };
const starts = (() => {
  let t = 0;
  return Object.fromEntries(
    Object.entries(S).map(([k, v]) => {
      const s = t;
      t += v;
      return [k, s];
    }),
  ) as Record<keyof typeof S, number>;
})();
export const TOTAL_FRAMES = Object.values(S).reduce((a, b) => a + b, 0);

const fade = (frame: number, len: number, inF = 10, outF = 10) =>
  interpolate(frame, [0, inF, len - outF, len], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

const rise = (frame: number, delay = 0) => {
  const p = interpolate(frame - delay, [0, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  return { opacity: p, transform: `translateY(${(1 - p) * 24}px)` };
};

// The 4:5 composition reuses the scenes with a stacked layout.
const useVertical = () => {
  const { width, height } = useVideoConfig();
  return height > width;
};

export const DocflowDemo = () => (
  <AbsoluteFill style={{ background: C.bg, fontFamily: FONT, color: C.ink }}>
    <style>{fontFaces}</style>
    <Sequence from={starts.hook} durationInFrames={S.hook}>
      <Hook />
    </Sequence>
    <Sequence from={starts.before} durationInFrames={S.before}>
      <Before />
    </Sequence>
    <Sequence from={starts.bridge} durationInFrames={S.bridge}>
      <Bridge />
    </Sequence>
    <Sequence from={starts.lease} durationInFrames={S.lease}>
      <Clip
        src="clips/lease.mp4"
        len={S.lease}
        captions={[
          { from: 0, to: 70, text: "Docflow vytáhne údaje ze smlouvy" },
          { from: 70, to: 270, text: "U každého údaje vidíte, odkud v PDF je" },
          { from: 270, to: 474, text: "Platby, data, kauce. I z další strany." },
        ]}
        // vertical camera: alternate between the clicked field (panel) and its quote (PDF)
        pans={[
          [0, "pdf"],
          [90, "panel"],
          [138, "pdf"],
          [207, "panel"],
          [276, "pdf"],
          [342, "panel"],
          [411, "pdf"],
        ]}
      />
    </Sequence>
    <Sequence from={starts.invoice} durationInFrames={S.invoice}>
      <Clip
        src="clips/invoice.mp4"
        len={S.invoice}
        captions={[{ from: 0, to: 150, text: "Když něco nesedí, pravidla to označí ke kontrole" }]}
        focus={{ at: 30, scale: 1.9, origin: "88% 29%" }}
        pans={[[0, "panel"]]}
        verticalFocus={{ at: 30, scale: 1.6, origin: "80% 39%" }}
      />
    </Sequence>
    <Sequence from={starts.sheet} durationInFrames={S.sheet}>
      <Sheet />
    </Sequence>
    <Sequence from={starts.cta} durationInFrames={S.cta}>
      <Cta />
    </Sequence>
  </AbsoluteFill>
);

const Hook = () => {
  const f = useCurrentFrame();
  const vertical = useVertical();
  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        padding: vertical ? "0 80px" : "0 180px",
        opacity: fade(f, S.hook, 1, 12),
      }}
    >
      <div style={{ fontSize: 34, fontWeight: 500, color: C.accent, ...rise(f, 2) }}>
        Pro správce nájmů
      </div>
      <div
        style={{
          fontSize: vertical ? 92 : 96,
          fontWeight: 700,
          lineHeight: 1.08,
          letterSpacing: -2,
          marginTop: 24,
          ...rise(f, 8),
        }}
      >
        Přepisujete nájemní smlouvy
        {vertical ? " " : <br />}
        do Excelu ručně?
      </div>
    </AbsoluteFill>
  );
};

// Fictional contract from the public demo (najem_pilot.pdf).
const CONTRACT: { id: string; text: string }[] = [
  { id: "h", text: "SMLOUVA O NÁJMU BYTU" },
  { id: "p1", text: "Ing. Pavel Hrubý (dále jen „pronajímatel“)" },
  { id: "tenant", text: "Mgr. Kristýna Málková (dále jen „nájemce“)" },
  { id: "addr", text: "… byt č. 12 na adrese Štefánikova 1426/38, 612 00 Brno …" },
  { id: "p2", text: "2.1 Nájem se sjednává na dobu určitou od 1. listopadu 2026 do 31. října 2027." },
  { id: "rent", text: "3.1 Nájemné … činí 16 500 Kč měsíčně." },
  { id: "p3", text: "3.2 Zálohy na služby celkem 2 650 Kč." },
  { id: "total", text: "3.3 Celková měsíční platba … činí 19 150 Kč." },
];

const CELLS = [
  { src: "tenant", col: "Nájemce", value: "Mgr. Kristýna Málková" },
  { src: "addr", col: "Adresa", value: "Štefánikova 1426/38, Brno" },
  { src: "rent", col: "Nájemné", value: "16 500" },
  { src: "total", col: "Celkem", value: "19 510" }, // deliberate typo, real value 19 150
];

const CELL_START = 40;
const CELL_LEN = 55;

const Before = () => {
  const f = useCurrentFrame();
  const vertical = useVertical();
  const side = vertical ? 60 : 110;
  const active = Math.floor((f - CELL_START) / CELL_LEN);
  const typoAt = CELL_START + CELLS.length * CELL_LEN + 10;
  const typo = spring({ frame: f - typoAt, fps: 30, config: { damping: 14 } });
  const caption =
    f < typoAt ? "Údaj po údaji. Smlouva po smlouvě." : "A překlep v částce si nikdo nevšimne.";

  return (
    <AbsoluteFill style={{ opacity: fade(f, S.before, 10, 10) }}>
      <div
        style={{
          position: "absolute",
          top: 70,
          left: side,
          right: side,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <div style={{ fontSize: 44, fontWeight: 700 }}>Dnes</div>
        <div style={{ fontSize: 30, color: C.muted, fontVariantNumeric: "tabular-nums" }}>
          Smlouva 1 z 50
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          top: 170,
          left: side,
          right: side,
          bottom: 190,
          display: "flex",
          flexDirection: vertical ? "column" : "row",
          gap: vertical ? 32 : 48,
        }}
      >
        {/* PDF page */}
        <div
          style={{
            flex: vertical ? "none" : "0 0 760px",
            background: C.surface,
            border: `1px solid ${C.line}`,
            borderRadius: 10,
            padding: "44px 52px",
            boxShadow: "0 12px 32px -12px rgba(31,36,43,0.28)",
          }}
        >
          {CONTRACT.map((l) => {
            const cellIdx = CELLS.findIndex((c) => c.src === l.id);
            const lit = cellIdx === active;
            return (
              <div
                key={l.id}
                style={{
                  fontSize: l.id === "h" ? 30 : 23,
                  fontWeight: l.id === "h" ? 700 : 400,
                  textAlign: l.id === "h" ? "center" : "left",
                  lineHeight: 1.45,
                  margin: l.id === "h" ? "0 0 26px" : "0 0 16px",
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: lit ? C.mark : "transparent",
                }}
              >
                {l.text}
              </div>
            );
          })}
        </div>

        {/* Spreadsheet */}
        <div style={{ flex: vertical ? "none" : 1, alignSelf: vertical ? "stretch" : "flex-start", border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden", background: C.surface }}>
          <div style={{ background: "#1f7a4d", color: "#fff", fontSize: 22, fontWeight: 500, padding: "12px 20px" }}>
            evidence_najmu.xlsx
          </div>
          {CELLS.map((c, i) => {
            const t = f - (CELL_START + i * CELL_LEN) - 14;
            const shown = Math.max(0, Math.min(c.value.length, Math.floor(t / 2.2)));
            const typing = i === active && shown < c.value.length;
            const isTypo = i === 3 && f >= typoAt;
            return (
              <div key={c.col} style={{ display: "flex", borderTop: `1px solid ${C.line}`, fontSize: 26 }}>
                <div style={{ width: 180, padding: "18px 20px", background: C.subtle, color: C.muted, fontWeight: 500 }}>
                  {c.col}
                </div>
                <div
                  style={{
                    flex: 1,
                    padding: "18px 20px",
                    position: "relative",
                    fontVariantNumeric: "tabular-nums",
                    outline: i === active ? `3px solid ${C.ok}` : "none",
                    outlineOffset: -3,
                    background: isTypo ? C.errBg : "transparent",
                    color: isTypo ? C.err : C.ink,
                  }}
                >
                  {c.value.slice(0, shown)}
                  {typing && <span style={{ borderLeft: `2px solid ${C.ink}`, marginLeft: 2 }} />}
                  {isTypo && (
                    <div
                      style={{
                        position: "absolute",
                        right: 20,
                        top: "50%",
                        transform: `translateY(-50%) scale(${typo})`,
                        fontSize: 21,
                        fontWeight: 700,
                      }}
                    >
                      ve smlouvě 19 150
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <Caption text={caption} key={caption} />
    </AbsoluteFill>
  );
};

const Bridge = () => {
  const f = useCurrentFrame();
  const s = spring({ frame: f, fps: 30, config: { damping: 16 } });
  return (
    <AbsoluteFill
      style={{ background: C.accent, justifyContent: "center", alignItems: "center", opacity: fade(f, S.bridge, 6, 8) }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 28, transform: `scale(${0.9 + s * 0.1})` }}>
        <div
          style={{
            width: 104,
            height: 104,
            borderRadius: 22,
            background: "#fff",
            color: C.accent,
            fontSize: 64,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          D
        </div>
        <div style={{ color: "#fff", fontSize: 92, fontWeight: 700, letterSpacing: -1.5 }}>S Docflow</div>
      </div>
    </AbsoluteFill>
  );
};

type Cap = { from: number; to: number; text: string };

type Focus = { at: number; scale: number; origin: string };
type Pan = [frame: number, view: "pdf" | "panel"];

const Clip = (props: {
  src: string;
  len: number;
  captions: Cap[];
  focus?: Focus;
  pans: Pan[];
  verticalFocus?: Focus;
}) => (useVertical() ? <VerticalClip {...props} /> : <WideClip {...props} />);

const WideClip = ({ src, len, captions, focus }: { src: string; len: number; captions: Cap[]; focus?: Focus }) => {
  const f = useCurrentFrame();
  // slow push-in keeps the screen recording alive; focus zooms onto one detail for phone viewers
  const zoom = focus
    ? interpolate(f, [focus.at, focus.at + 24], [1, focus.scale], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
        easing: Easing.inOut(Easing.cubic),
      })
    : interpolate(f, [0, len], [1, 1.035]);
  const cap = captions.find((c) => f >= c.from && f < c.to);
  return (
    <AbsoluteFill style={{ opacity: fade(f, len, 8, 8) }}>
      <AbsoluteFill style={{ transform: `scale(${zoom})`, transformOrigin: focus?.origin ?? "50% 40%" }}>
        <OffthreadVideo src={staticFile(src)} muted />
      </AbsoluteFill>
      <div
        style={{
          position: "absolute",
          bottom: 28,
          left: 36,
          fontSize: 20,
          color: C.muted,
          background: "rgba(255,255,255,0.92)",
          padding: "6px 14px",
          borderRadius: 999,
          border: `1px solid ${C.line}`,
        }}
      >
        Skutečná aplikace · fiktivní dokumenty
      </div>
      {cap && <Caption text={cap.text} key={cap.text} startOffset={f - cap.from} top />}
    </AbsoluteFill>
  );
};

// 4:5 layout: caption band on top, below it a window onto the 1920×1080 recording that
// pans between the PDF (left) and the result panel (right); the full width would be unreadable.
const BAND = 300;
const VIEW_H = 1350 - BAND;
const REC_SCALE = VIEW_H / 1080;
const WINDOW = 1080 / REC_SCALE; // recording pixels visible across the frame
const LEFT = { pdf: 150, panel: 1920 - WINDOW };

const VerticalClip = ({
  src,
  len,
  captions,
  pans,
  verticalFocus,
}: {
  src: string;
  len: number;
  captions: Cap[];
  pans: Pan[];
  verticalFocus?: Focus;
}) => {
  const f = useCurrentFrame();
  const frames = pans.flatMap(([at], i) => (i ? [at, at + 18] : [at]));
  const lefts = pans.flatMap(([, view], i) => (i ? [LEFT[pans[i - 1][1]], LEFT[view]] : [LEFT[view]]));
  const left =
    frames.length > 1
      ? interpolate(f, frames, lefts, {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.inOut(Easing.cubic),
        })
      : lefts[0];
  const zoom = verticalFocus
    ? interpolate(f, [verticalFocus.at, verticalFocus.at + 24], [1, verticalFocus.scale], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
        easing: Easing.inOut(Easing.cubic),
      })
    : 1;
  const cap = captions.find((c) => f >= c.from && f < c.to);
  return (
    <AbsoluteFill style={{ opacity: fade(f, len, 8, 8) }}>
      <div style={{ position: "absolute", top: BAND, left: 0, width: 1080, height: VIEW_H, overflow: "hidden" }}>
        <div style={{ width: "100%", height: "100%", transform: `scale(${zoom})`, transformOrigin: verticalFocus?.origin }}>
          <div
            style={{
              width: 1920,
              height: 1080,
              transform: `translateX(${-left * REC_SCALE}px) scale(${REC_SCALE})`,
              transformOrigin: "0 0",
            }}
          >
            <OffthreadVideo src={staticFile(src)} muted style={{ width: 1920, height: 1080 }} />
          </div>
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: BAND,
          background: C.ink,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 64px",
        }}
      >
        {cap && (
          <div key={cap.text} style={{ color: "#fff", fontSize: 58, fontWeight: 700, lineHeight: 1.12, ...rise(f - cap.from) }}>
            {cap.text}
          </div>
        )}
        <div style={{ color: "#9aa3ae", fontSize: 24, marginTop: 18 }}>Skutečná aplikace · fiktivní dokumenty</div>
      </div>
    </AbsoluteFill>
  );
};

// On screen recordings the caption sits over the app's nav bar, where it hides nothing that matters.
const Caption = ({ text, startOffset, top }: { text: string; startOffset?: number; top?: boolean }) => {
  const local = useCurrentFrame();
  const vertical = useVertical();
  const f = startOffset ?? local;
  return (
    <div
      style={{
        position: "absolute",
        left: 40,
        right: 40,
        ...(top ? { top: 14 } : { bottom: 64 }),
        display: "flex",
        justifyContent: "center",
        textAlign: "center",
      }}
    >
      <div
        style={{
          background: C.ink,
          color: "#fff",
          fontSize: vertical ? 44 : 46,
          fontWeight: 700,
          padding: "20px 38px",
          borderRadius: 14,
          boxShadow: "0 12px 32px -12px rgba(31,36,43,0.45)",
          ...rise(f),
        }}
      >
        {text}
      </div>
    </div>
  );
};

// Fictional rows, first one is the demo contract.
const ROWS = [
  ["Mgr. Kristýna Málková", "Štefánikova 1426/38, Brno", "1. 11. 2026", "16 500", "2 650", "19 150", "33 000"],
  ["Tomáš Řezníček", "Cejl 54, Brno", "1. 9. 2026", "14 200", "2 300", "16 500", "28 400"],
  ["Eva Kratochvílová", "Kounicova 12, Brno", "1. 10. 2026", "18 900", "3 100", "22 000", "37 800"],
  ["Jan Dvořák", "Veveří 87, Brno", "15. 10. 2026", "12 800", "2 050", "14 850", "25 600"],
  ["Lucie Benešová", "Údolní 33, Brno", "1. 12. 2026", "21 000", "3 400", "24 400", "42 000"],
];
const COLS = ["Nájemce", "Adresa", "Začátek", "Nájemné", "Zálohy", "Celkem", "Kauce"];
const WIDTHS = [370, 410, 210, 175, 165, 175, 175];
// 4:5 shows only the columns that fit: Nájemce, Nájemné, Celkem, Kauce.
const VERTICAL_COLS = [0, 3, 5, 6];
const VERTICAL_WIDTHS = [380, 200, 200, 180];

const Sheet = () => {
  const f = useCurrentFrame();
  const vertical = useVertical();
  const pick = (cells: string[]) => (vertical ? VERTICAL_COLS.map((i) => cells[i]) : cells);
  return (
    <AbsoluteFill
      style={{
        padding: vertical ? "0 50px" : "80px 110px",
        justifyContent: vertical ? "center" : "flex-start",
        opacity: fade(f, S.sheet, 10, 10),
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 18, ...rise(f) }}>
        <div style={{ fontSize: 44, fontWeight: 700 }}>Vy jen zkontrolujete a exportujete</div>
      </div>
      <div style={{ fontSize: 26, color: C.muted, marginTop: 10, ...rise(f, 4) }}>
        Schválené smlouvy jdou rovnou do vaší evidence v Excelu.
      </div>
      <div
        style={{
          marginTop: 48,
          border: `1px solid ${C.line}`,
          borderRadius: 10,
          overflow: "hidden",
          background: C.surface,
          boxShadow: "0 12px 32px -12px rgba(31,36,43,0.28)",
          ...rise(f, 8),
        }}
      >
        <div style={{ background: "#1f7a4d", color: "#fff", fontSize: 22, fontWeight: 500, padding: "12px 20px" }}>
          evidence_najmu.xlsx
        </div>
        <Row cells={pick(COLS)} header />
        {ROWS.map((r, i) => {
          const at = 30 + i * 12;
          const p = interpolate(f, [at, at + 10], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          return (
            <div key={i} style={{ opacity: p, transform: `translateX(${(1 - p) * -30}px)` }}>
              <Row cells={pick(r)} />
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 20, color: C.muted, marginTop: 18 }}>Ilustrační data, fiktivní nájemníci.</div>
    </AbsoluteFill>
  );
};

const Row = ({ cells, header }: { cells: string[]; header?: boolean }) => {
  const vertical = useVertical();
  return (
  <div style={{ display: "flex", borderTop: `1px solid ${C.line}`, background: header ? C.subtle : C.surface }}>
    {cells.map((c, i) => (
      <div
        key={i}
        style={{
          width: (vertical ? VERTICAL_WIDTHS : WIDTHS)[i],
          flexShrink: 0,
          padding: "20px 18px",
          fontSize: 28,
          fontWeight: header ? 500 : 400,
          color: header ? C.muted : C.ink,
          textAlign: i >= (vertical ? 1 : 3) ? "right" : "left",
          fontVariantNumeric: "tabular-nums",
          borderLeft: i ? `1px solid ${C.line}` : "none",
          whiteSpace: "nowrap",
          overflow: "hidden",
        }}
      >
        {c}
      </div>
    ))}
  </div>
  );
};

const Cta = () => {
  const f = useCurrentFrame();
  const vertical = useVertical();
  return (
    <AbsoluteFill
      style={{
        background: C.ink,
        color: "#fff",
        justifyContent: "center",
        padding: vertical ? "0 72px" : "0 180px",
        opacity: fade(f, S.cta, 10, 1),
      }}
    >
      <div style={{ fontSize: 34, fontWeight: 500, color: "#9db4f0", ...rise(f) }}>Pilot na vašich smlouvách</div>
      <div style={{ fontSize: vertical ? 84 : 88, fontWeight: 700, letterSpacing: -1.5, lineHeight: 1.1, marginTop: 20, ...rise(f, 5) }}>
        14 dní · do 50 smluv{vertical ? <br /> : " · "}4 900 Kč
      </div>
      <div style={{ fontSize: 32, color: "#c9ced6", marginTop: 28, ...rise(f, 10) }}>
        Šablonu nastavím podle vaší evidence a změříme, kolik času ušetříte.
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: vertical ? "column" : "row",
          gap: vertical ? 28 : 48,
          alignItems: vertical ? "flex-start" : "center",
          marginTop: 72,
          ...rise(f, 16),
        }}
      >
        <div style={{ background: C.accent, fontSize: 44, fontWeight: 700, padding: "20px 36px", borderRadius: 12 }}>
          docflow.logicflow.cz
        </div>
        <div style={{ fontSize: 30, color: "#c9ced6" }}>Matěj Koval · LogicFlow</div>
      </div>
    </AbsoluteFill>
  );
};
