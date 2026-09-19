import { n as IconInput } from "./types-JAF3s76Y.js";
import { i as PathEl } from "./index-DXp5b6BU.js";
//#region src/adapters/canvas.d.ts
/** The 2D context surface the target drives — structural, so a real
 *  CanvasRenderingContext2D, an OffscreenCanvasRenderingContext2D or a test
 *  fake all satisfy it. Only what canvasTarget actually touches. */
interface Canvas2DContext {
  canvas: {
    width: number;
    height: number;
  };
  lineWidth: number;
  lineCap: string;
  lineJoin: string;
  strokeStyle: string | object;
  setTransform(a: number, b: number, c: number, d: number, e: number, f: number): void;
  clearRect(x: number, y: number, w: number, h: number): void;
  stroke(path: object): void;
}
/** Anything that can hand out a 2D context (an HTMLCanvasElement, an
 *  OffscreenCanvas, or a fake in tests). */
interface CanvasSurface {
  getContext(contextId: "2d"): Canvas2DContext | null;
}
interface CanvasTargetOptions {
  /** Grid the icon's coordinates live on (Lucide/Iconify default: "0 0 24 24").
   *  Used to map the geometry onto the backing store. */
  viewBox?: string;
  /** Stroke width in grid units (Lucide's default is 2). */
  strokeWidth?: number;
  /** Stroke color. Defaults to the canvas' computed CSS color when it is a
   *  styled element; otherwise the context's current `strokeStyle` stands. */
  color?: string;
  /** Clear the canvas before each frame. Turn off for manual compositing or
   *  trails — every frame then draws on top of the last. Default true. */
  clear?: boolean;
  /** Called after every drawn frame — the texture consumer's dirty signal
   *  (e.g. re-upload the canvas with `texImage2D`, or blit it into a scene). */
  onWrite?: () => void;
}
/** Adapts a canvas (or its 2D context) into a `PathEl` whose writes stroke
 *  the morph's geometry onto the canvas, so
 *  `createMorph(canvasTarget(canvas), icon)` animates pixels instead of an
 *  inline `<svg><path>`. */
declare function canvasTarget(surface: CanvasSurface | Canvas2DContext, opts?: CanvasTargetOptions): PathEl;
//#endregion
//#region src/adapters/mask.d.ts
/** The subset of an element's inline style the mask target writes — structural
 *  so a real HTMLElement's `.style` satisfies it (and a test fake supplies
 *  just these). */
interface MaskStyle {
  maskImage: string;
  webkitMaskImage: string;
  backgroundColor: string;
}
/** An element the mask target can drive (an HTMLElement, or a fake in tests). */
interface MaskEl {
  style: MaskStyle;
}
interface MaskTargetOptions {
  /** Stroke width of the mask geometry (Lucide's default is 2). */
  strokeWidth?: number;
  /** Grid the icon's coordinates live on (Lucide/Iconify default: "0 0 24 24").
   *  Used to map the geometry onto the element's box. */
  viewBox?: string;
  /** Set `background-color: currentColor` on first setup so the element shows
   *  through the mask in the current text color, standalone (no icon class).
   *  Turn off to keep an existing paint (e.g. a `bg-*` utility). Default true. */
  paint?: boolean;
}
/** What `maskTarget` returns: the driver's write contract plus a `dispose`
 *  that removes the hidden mask node (call it after `morph.destroy()` when
 *  the element unmounts). */
interface MaskPathEl extends PathEl {
  dispose(): void;
}
/** Adapts an element into a `PathEl` whose writes drive a referenced SVG
 *  mask, so `createMorph(maskTarget(el), icon)` animates a mask-styled
 *  element instead of an inline `<svg><path>`.
 *
 *  DOUBLE BUFFER, on purpose: WebKit does not reliably repaint a masked
 *  element when the CONTENT of its referenced `<mask>` mutates — a lone mask
 *  whose inner path `d` changes per frame renders frozen in Safari until the
 *  flight ends. So the target keeps TWO masks and every `d` write lands on
 *  the back buffer and re-points `mask-image` at it: the property VALUE flips
 *  (`url(#a)` ↔ `url(#b)`) each frame, which forces reference re-resolution
 *  in every engine. Chromium/Gecko pay nothing extra for it. */
declare function maskTarget(el: MaskEl, opts?: MaskTargetOptions): MaskPathEl;
//#endregion
//#region src/adapters/svg.d.ts
/** SVG markup → an IconInput the pipeline can lower. Strips non-rendered
 *  containers, rejects what can't be honestly morphed (a fill-drawn icon with
 *  no stroke, a transform, an unsupported element), and re-grids onto 24 via
 *  the viewBox when one is present — so an off-grid collection lands on the
 *  shared grid automatically. Returns a `d` (when a viewBox forced a fit) or
 *  an IconNode. */
declare function svgToIcon(markup: string): IconInput;
//#endregion
export { type Canvas2DContext, type CanvasSurface, type CanvasTargetOptions, type MaskEl, type MaskPathEl, type MaskStyle, type MaskTargetOptions, canvasTarget, maskTarget, svgToIcon };