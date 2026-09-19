import { a as Sampled, c as SpringPreset, i as IconNodeAttrs, n as IconInput, o as SPRING_PRESETS, r as IconNode, s as Spring, t as CubicPath } from "./types-JAF3s76Y.js";
//#region src/core/plan.d.ts
interface PlanItem {
  /** Points of A with the chosen correspondence (if A is a closed loop it
   *  may come circularly re-indexed: same points, different cut). */
  a: Float64Array;
  /** A centered on its centroid. */
  aC: Float64Array;
  /** B brought into A's frame: R(−θ)·(b − c_B)/σ. */
  bT: Float64Array;
  /** B oriented, raw (for linear mode and for exact t=1). */
  bO: Float64Array;
  ca: readonly [number, number];
  cb: readonly [number, number];
  theta: number;
  lnSigma: number;
  res: number;
  /** true if both endpoints are closed loops: the subpath flies with Z.
   *  Closed → open flies open: the loop opens at the chosen cut. */
  closed: boolean;
  /** Block transport (set by the global hybrid, else null): mid-flight the
   *  centroid rides the shared similarity around the global centroid instead
   *  of lerping — off = c_A − g_A; drift closes c(1) = c_B exactly. */
  block: {
    off: readonly [number, number];
    drift: readonly [number, number];
  } | null;
}
interface MorphPlan {
  items: PlanItem[];
  n: number;
}
/** Builds the morph plan between two lists of sampled subpaths. The plan is
 *  cacheable and serializable; it accepts any list — including intermediate
 *  shapes (interruptions). */
declare function buildPlan(srcSubs: readonly Sampled[], dstSubs: readonly Sampled[]): MorphPlan;
//#endregion
//#region src/core/interpolate.d.ts
/** Preallocated output buffers for a plan (zero allocation per frame). */
declare function allocOutputs(plan: MorphPlan): Float64Array[];
declare function interpPolar(plan: MorphPlan, t: number, out: Float64Array[]): void;
/** Raw coordinate lerp (same correspondence, no decomposition). */
declare function interpLinear(plan: MorphPlan, t: number, out: Float64Array[]): void;
//#endregion
//#region src/core/normalize.d.ts
/** Icon (IconNode or `d` string) → list of cubic subpaths. */
declare function iconToCubics(input: IconInput): CubicPath[];
/** A source viewBox: `24`, `"0 0 20 20"` or `[minX, minY, w, h]`. */
type ViewBox = number | string | readonly [number, number, number, number];
/** Re-grids an icon drawn on `viewBox` onto the shared `grid` (24 by default),
 *  centred and preserving aspect ratio — the SVG `xMidYMid meet` rule.
 *
 *  Both endpoints of a morph must live on the same coordinate space. Lucide and
 *  Tabler already draw on 24×24; packs on 20 (Heroicons solid) or 32 (Carbon)
 *  do not, and mixing them unfitted makes Procrustes read the scale/offset gap
 *  as rotation. Apply once at module scope (not per render) and pass the
 *  resulting `d` anywhere an icon is accepted. */
declare function fitIcon(input: IconInput, viewBox: ViewBox, grid?: number): string;
//#endregion
//#region src/core/resample.d.ts
/** Samples a cubic subpath at N points equidistant by arc length, anchoring
 *  corners and endpoints as exact samples. Returns Float64Array(2N). Closed
 *  paths distribute N intervals around the loop (without duplicating the
 *  first point); the circular start-point freedom is resolved by the plan's
 *  circular correspondence. */
declare function resamplePath(path: CubicPath, N?: number, cornerThreshold?: number): Float64Array;
/** Full input pipeline: icon → cubics → sampled subpaths with their
 *  topology (the plan needs to know which subpaths are closed loops). */
declare function resampleIcon(input: IconInput, N?: number): Sampled[];
//#endregion
//#region src/core/serialize.d.ts
/** Sampled subpaths → polyline `d` attribute. `closed[k]` appends Z to
 *  subpath k (closed loops in flight); without flags everything is open. */
declare function serialize(subs: readonly Float64Array[], closed?: readonly boolean[]): string;
/** Cubic subpaths → canonical `d`, quantized to 4 decimals (engine-stable
 *  bytes; see fmtCanon). */
declare function cubicsToPathD(paths: readonly CubicPath[]): string;
//#endregion
export { type CubicPath, type IconInput, type IconNode, type IconNodeAttrs, type MorphPlan, type PlanItem, SPRING_PRESETS, type Sampled, Spring, type SpringPreset, type ViewBox, allocOutputs, buildPlan, cubicsToPathD, fitIcon, iconToCubics, interpLinear, interpPolar, resampleIcon, resamplePath, serialize };