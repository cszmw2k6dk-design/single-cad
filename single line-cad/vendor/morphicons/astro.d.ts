import type {
  IconInput,
  MorphOptions,
  ReducedMotionMode,
  SpringPreset,
} from "./element.js";

export interface MorphIconAstroProps {
  icon?: IconInput;
  from?: IconInput;
  to?: IconInput;
  progress?: number;
  spring?: SpringPreset | MorphOptions;
  reducedMotion?: ReducedMotionMode;
  size?: number | string;
  color?: string;
  strokeWidth?: number | string;
  absoluteStrokeWidth?: boolean;
  label?: string;
  [attr: string]: unknown;
}

/** Astro component: SSR shell over the <morph-icon> custom element. */
declare const MorphIcon: (props: MorphIconAstroProps) => unknown;
export default MorphIcon;
export type {
  IconInput,
  IconNode,
  MorphOptions,
  ReducedMotionMode,
  SpringPreset,
} from "./element.js";
