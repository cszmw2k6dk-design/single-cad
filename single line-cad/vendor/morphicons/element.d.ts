import { a as Sampled, c as SpringPreset, n as IconInput, r as IconNode } from "./types-JAF3s76Y.js";
import { a as ReducedMotionMode, i as PathEl, n as Morph, r as MorphOptions } from "./index-DXp5b6BU.js";
import { n as computeInitialD, t as MorphHandle } from "./controller-Dkruvf3Y.js";
//#region src/element/index.d.ts
declare const Base: typeof HTMLElement;
declare class MorphIconElement extends Base implements MorphHandle {
  #private;
  static observedAttributes: string[];
  get icon(): IconInput | undefined;
  set icon(v: IconInput | undefined);
  get from(): IconInput | undefined;
  set from(v: IconInput | undefined);
  get to(): IconInput | undefined;
  set to(v: IconInput | undefined);
  get progress(): number | undefined;
  set progress(v: number | undefined);
  get spring(): SpringPreset | MorphOptions | undefined;
  set spring(v: SpringPreset | MorphOptions | undefined);
  get reducedMotion(): ReducedMotionMode | undefined;
  set reducedMotion(v: ReducedMotionMode | undefined);
  morphTo(icon: IconInput, spring?: SpringPreset | MorphOptions): void;
  set(icon: IconInput): void;
  connectedCallback(): void;
  disconnectedCallback(): void;
  attributeChangedCallback(name: string, _old: string | null, value: string | null): void;
}
/** Defines `<morph-icon>` (or a custom tag). Idempotent per tag; a no-op
 *  without a DOM (safe to call from code that also runs during SSR). */
declare function defineMorphIcon(tag?: string): void;
declare global {
  interface HTMLElementTagNameMap {
    "morph-icon": MorphIconElement;
  }
}
//#endregion
export { type IconInput, type IconNode, type Morph, type MorphHandle, MorphIconElement, type MorphOptions, type PathEl, type ReducedMotionMode, type Sampled, type SpringPreset, computeInitialD, defineMorphIcon };