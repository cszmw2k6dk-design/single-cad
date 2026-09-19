import { t as fitIcon } from "./normalize-CYnN3Npw.js";
//#region src/adapters/canvas.ts
/** Adapts a canvas (or its 2D context) into a `PathEl` whose writes stroke
*  the morph's geometry onto the canvas, so
*  `createMorph(canvasTarget(canvas), icon)` animates pixels instead of an
*  inline `<svg><path>`. */
function canvasTarget(surface, opts = {}) {
	if (typeof Path2D === "undefined") throw new Error("morphicons: canvasTarget needs Path2D (browser or worker)");
	const ctx = "getContext" in surface ? surface.getContext("2d") : surface;
	if (!ctx) throw new Error("morphicons: canvasTarget needs a 2D context");
	const vb = (opts.viewBox ?? "0 0 24 24").trim().split(/[\s,]+/).map(Number);
	const [mx, my, vw, vh] = [
		vb[0] || 0,
		vb[1] || 0,
		vb[2] || 24,
		vb[3] || vb[2] || 24
	];
	const stroke = opts.strokeWidth ?? 2;
	const clear = opts.clear !== false;
	let color = opts.color;
	let cssPending = color === void 0 && "style" in ctx.canvas;
	return { setAttribute(name, value) {
		if (name !== "d") return;
		if (cssPending) {
			cssPending = false;
			color = getComputedStyle?.(ctx.canvas).color || void 0;
		}
		const c = ctx.canvas;
		const s = Math.min(c.width / vw, c.height / vh);
		if (clear) {
			ctx.setTransform(1, 0, 0, 1, 0, 0);
			ctx.clearRect(0, 0, c.width, c.height);
		}
		ctx.setTransform(s, 0, 0, s, (c.width - vw * s) / 2 - mx * s, (c.height - vh * s) / 2 - my * s);
		ctx.lineWidth = stroke;
		ctx.lineCap = "round";
		ctx.lineJoin = "round";
		if (color !== void 0) ctx.strokeStyle = color;
		ctx.stroke(new Path2D(value));
		opts.onWrite?.();
	} };
}
//#endregion
//#region src/adapters/mask.ts
const SVG_NS = "http://www.w3.org/2000/svg";
let seq = 0;
let host = null;
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
function maskTarget(el, opts = {}) {
	if (typeof document === "undefined") throw new Error("morphicons: maskTarget needs a DOM (client-side only)");
	const doc = document;
	if (!host) {
		host = doc.createElementNS(SVG_NS, "svg");
		host.setAttribute("width", "0");
		host.setAttribute("height", "0");
		host.setAttribute("aria-hidden", "true");
		host.setAttribute("style", "position:absolute");
		doc.body.appendChild(host);
	}
	const vb = (opts.viewBox ?? "0 0 24 24").trim().split(/[\s,]+/).map(Number);
	const [mx, my, w, h] = [
		vb[0] || 0,
		vb[1] || 0,
		vb[2] || 24,
		vb[3] || vb[2] || 24
	];
	const makeLayer = () => {
		const id = `morphicons-mask-${++seq}`;
		const mask = doc.createElementNS(SVG_NS, "mask");
		mask.setAttribute("id", id);
		mask.setAttribute("maskContentUnits", "objectBoundingBox");
		const g = doc.createElementNS(SVG_NS, "g");
		g.setAttribute("transform", `scale(${1 / w} ${1 / h}) translate(${-mx} ${-my})`);
		const path = doc.createElementNS(SVG_NS, "path");
		path.setAttribute("fill", "none");
		path.setAttribute("stroke", "#fff");
		path.setAttribute("stroke-width", String(opts.strokeWidth ?? 2));
		path.setAttribute("stroke-linecap", "round");
		path.setAttribute("stroke-linejoin", "round");
		g.appendChild(path);
		mask.appendChild(g);
		if (host) host.appendChild(mask);
		return {
			url: `url(#${id})`,
			mask,
			path
		};
	};
	const layers = [makeLayer(), makeLayer()];
	let front = 0;
	const s = el.style;
	s.maskImage = s.webkitMaskImage = layers[0].url;
	if (opts.paint !== false) s.backgroundColor = "currentColor";
	return {
		setAttribute(name, d) {
			if (name !== "d") return;
			front = 1 - front;
			const layer = layers[front];
			layer.path.setAttribute("d", d);
			s.maskImage = s.webkitMaskImage = layer.url;
		},
		dispose() {
			layers[0].mask.remove();
			layers[1].mask.remove();
		}
	};
}
//#endregion
//#region src/adapters/svg.ts
const SVG_CONTAINER = /<(defs|mask|clipPath|symbol)\b[^>]*>[\s\S]*?<\/\1>/gi;
const SVG_ELEMENT = /<([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*?)\/?>/g;
const SVG_ATTR = /([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*'([^']*)'/g;
const SVG_VIEWBOX = /<svg\b[^>]*\bviewBox\s*=\s*["']([^"']+)["']/i;
const SVG_GEOMETRY = /* @__PURE__ */ new Set([
	"path",
	"line",
	"circle",
	"ellipse",
	"rect",
	"polyline",
	"polygon"
]);
const SVG_SKIP = /* @__PURE__ */ new Set([
	"svg",
	"g",
	"title",
	"desc",
	"defs",
	"mask",
	"clippath",
	"symbol"
]);
function readSvgAttrs(raw) {
	const attrs = {};
	SVG_ATTR.lastIndex = 0;
	let m;
	while ((m = SVG_ATTR.exec(raw)) !== null) {
		const name = m[1] ?? m[3];
		if (name) attrs[name] = m[2] ?? m[4] ?? "";
	}
	return attrs;
}
/** SVG markup → an IconInput the pipeline can lower. Strips non-rendered
*  containers, rejects what can't be honestly morphed (a fill-drawn icon with
*  no stroke, a transform, an unsupported element), and re-grids onto 24 via
*  the viewBox when one is present — so an off-grid collection lands on the
*  shared grid automatically. Returns a `d` (when a viewBox forced a fit) or
*  an IconNode. */
function svgToIcon(markup) {
	if (markup.trimStart().charCodeAt(0) !== 60) throw new Error("morphicons: svgToIcon expects SVG markup — a d string or IconNode is already an IconInput");
	const body = markup.replace(SVG_CONTAINER, "");
	const hasStroke = /stroke\s*=\s*["'](?!\s*none\s*["'])/i.test(body);
	const fillNone = /fill\s*=\s*["']\s*none\s*["']/i.test(body);
	if (!hasStroke && !fillNone) throw new Error("morphicons: SVG looks fill-drawn (no stroke) — only stroke-centerline icons morph");
	const node = [];
	SVG_ELEMENT.lastIndex = 0;
	let m;
	while ((m = SVG_ELEMENT.exec(body)) !== null) {
		const tag = m[1].toLowerCase();
		if (SVG_SKIP.has(tag)) continue;
		if (!SVG_GEOMETRY.has(tag)) throw new Error(`morphicons: unsupported SVG element <${tag}>`);
		const attrs = readSvgAttrs(m[2]);
		if ("transform" in attrs) throw new Error(`morphicons: <${tag}> has a transform (unsupported)`);
		node.push([tag, attrs]);
	}
	if (node.length === 0) throw new Error("morphicons: no morphable geometry in SVG");
	const vb = SVG_VIEWBOX.exec(markup);
	return vb ? fitIcon(node, vb[1]) : node;
}
//#endregion
export { canvasTarget, maskTarget, svgToIcon };
