import { Composition } from "remotion";
import { DocflowDemo, TOTAL_FRAMES } from "./DocflowDemo";

export const Root = () => (
  <>
    <Composition
      id="DocflowDemo"
      component={DocflowDemo}
      durationInFrames={TOTAL_FRAMES}
      fps={30}
      width={1920}
      height={1080}
    />
    {/* 4:5 for the LinkedIn / Instagram feed */}
    <Composition
      id="DocflowDemoVertical"
      component={DocflowDemo}
      durationInFrames={TOTAL_FRAMES}
      fps={30}
      width={1080}
      height={1350}
    />
  </>
);
