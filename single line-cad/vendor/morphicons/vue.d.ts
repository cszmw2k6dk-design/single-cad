import { a as Sampled, c as SpringPreset, n as IconInput, r as IconNode } from "./types-JAF3s76Y.js";
import { a as ReducedMotionMode, i as PathEl, n as Morph, r as MorphOptions } from "./index-DXp5b6BU.js";
import { PropType } from "vue";
//#region src/vue/index.d.ts
/** Imperative surface exposed on the component instance (template ref). */
interface MorphHandle {
  morphTo(icon: IconInput, spring?: SpringPreset | MorphOptions): void;
  set(icon: IconInput): void;
}
declare const MorphIcon: import("vue").DefineComponent<import("vue").ExtractPropTypes<{
  /** Uncontrolled mode: the current icon; changing the prop animates. */
  icon: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: source endpoint of the pair. */
  from: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: target endpoint of the pair. */
  to: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: 0..1 progress of the frozen morph (no spring). */
  progress: {
    type: NumberConstructor;
    default: undefined;
  };
  /** Physics for uncontrolled/imperative mode: preset or custom spring. */
  spring: {
    type: PropType<SpringPreset | MorphOptions>;
    default: undefined;
  };
  /** Reduced-motion policy: "never" (default) animates regardless of the OS
   *  setting, "user" honors prefers-reduced-motion (morphs degrade to an
   *  instant swap while it is on), "always" always jumps. */
  reducedMotion: {
    type: PropType<ReducedMotionMode>;
    default: string;
  };
  size: {
    type: (NumberConstructor | StringConstructor)[];
    default: number;
  };
  color: {
    type: StringConstructor;
    default: string;
  };
  strokeWidth: {
    type: (NumberConstructor | StringConstructor)[];
    default: number;
  };
  /** Like lucide: stroke width does not scale with `size`. */
  absoluteStrokeWidth: {
    type: BooleanConstructor;
    default: boolean;
  };
  /** Accessibility: with label → role="img" + <title>; without → aria-hidden. */
  label: {
    type: StringConstructor;
    default: undefined;
  };
}>, () => import("vue").VNode<import("vue").RendererNode, import("vue").RendererElement, {
  [key: string]: any;
}>, {}, {}, {}, import("vue").ComponentOptionsMixin, import("vue").ComponentOptionsMixin, {}, string, import("vue").PublicProps, Readonly<import("vue").ExtractPropTypes<{
  /** Uncontrolled mode: the current icon; changing the prop animates. */
  icon: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: source endpoint of the pair. */
  from: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: target endpoint of the pair. */
  to: {
    type: PropType<IconInput>;
    default: undefined;
  };
  /** Controlled mode: 0..1 progress of the frozen morph (no spring). */
  progress: {
    type: NumberConstructor;
    default: undefined;
  };
  /** Physics for uncontrolled/imperative mode: preset or custom spring. */
  spring: {
    type: PropType<SpringPreset | MorphOptions>;
    default: undefined;
  };
  /** Reduced-motion policy: "never" (default) animates regardless of the OS
   *  setting, "user" honors prefers-reduced-motion (morphs degrade to an
   *  instant swap while it is on), "always" always jumps. */
  reducedMotion: {
    type: PropType<ReducedMotionMode>;
    default: string;
  };
  size: {
    type: (NumberConstructor | StringConstructor)[];
    default: number;
  };
  color: {
    type: StringConstructor;
    default: string;
  };
  strokeWidth: {
    type: (NumberConstructor | StringConstructor)[];
    default: number;
  };
  /** Like lucide: stroke width does not scale with `size`. */
  absoluteStrokeWidth: {
    type: BooleanConstructor;
    default: boolean;
  };
  /** Accessibility: with label → role="img" + <title>; without → aria-hidden. */
  label: {
    type: StringConstructor;
    default: undefined;
  };
}>> & Readonly<{}>, {
  icon: IconInput;
  from: IconInput;
  to: IconInput;
  progress: number;
  spring: "bouncy" | "smooth" | "snappy" | MorphOptions;
  reducedMotion: ReducedMotionMode;
  size: string | number;
  color: string;
  strokeWidth: string | number;
  absoluteStrokeWidth: boolean;
  label: string;
}, {}, {}, {}, string, import("vue").ComponentProvideOptions, true, {}, any>;
//#endregion
export { type IconInput, type IconNode, type Morph, MorphHandle, MorphIcon, type MorphOptions, type PathEl, type ReducedMotionMode, type Sampled, type SpringPreset };