import { a as Sampled, c as SpringPreset, n as IconInput, r as IconNode } from "./types-JAF3s76Y.js";
import { a as ReducedMotionMode, i as PathEl, n as Morph, r as MorphOptions } from "./index-DXp5b6BU.js";
import { SvgProps } from "react-native-svg";
//#region src/react-native/index.d.ts
/** Imperative surface exposed via ref. */
interface MorphHandle {
  morphTo(icon: IconInput, spring?: SpringPreset | MorphOptions): void;
  set(icon: IconInput): void;
}
interface MorphIconProps extends Omit<SvgProps, "from" | "to"> {
  /** Uncontrolled mode: the current icon; changing the prop animates. */
  icon?: IconInput;
  /** Controlled mode: source endpoint of the pair. */
  from?: IconInput;
  /** Controlled mode: target endpoint of the pair. */
  to?: IconInput;
  /** Controlled mode: 0..1 progress of the frozen morph (no spring). */
  progress?: number;
  /** Physics for uncontrolled/imperative mode: preset or custom spring. */
  spring?: SpringPreset | MorphOptions;
  /** Reduced-motion policy: "never" (default) animates regardless of the OS
   *  setting, "user" honors AccessibilityInfo's reduce motion (morphs degrade
   *  to an instant swap while it is on; best-effort, the query is async),
   *  "always" always jumps. */
  reducedMotion?: ReducedMotionMode;
  size?: number | string;
  color?: string;
  strokeWidth?: number | string;
  /** Like lucide: stroke width does not scale with `size`. */
  absoluteStrokeWidth?: boolean;
  /** Accessibility: with label → role="img" + aria-label; without → aria-hidden. */
  label?: string;
}
declare const MorphIcon: import("react").ForwardRefExoticComponent<MorphIconProps & import("react").RefAttributes<MorphHandle>>;
//#endregion
export { type IconInput, type IconNode, type Morph, MorphHandle, MorphIcon, MorphIconProps, type MorphOptions, type PathEl, type ReducedMotionMode, type Sampled, type SpringPreset };