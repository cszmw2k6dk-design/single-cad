import { a as buildPlan, c as interpPolar, o as allocOutputs, r as resampleIcon } from "./spring-CFHloqPP.js";
import { i as serialize } from "./normalize-CYnN3Npw.js";
import { canonicalD, createMorph } from "./dom.js";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
//#region src/react/index.tsx
const useIsoLayoutEffect = typeof document === "undefined" ? useEffect : useLayoutEffect;
/** Frozen shape of the from→to pair at t, using the pure core (SSR-safe).
*  At exact endpoints returns the canonical `d` (real curves, not polyline). */
function frozenD(from, to, t) {
	if (t <= 0) return canonicalD(from);
	if (t >= 1) return canonicalD(to);
	const plan = buildPlan(resampleIcon(from), resampleIcon(to));
	const out = allocOutputs(plan);
	interpPolar(plan, t, out);
	return serialize(out, plan.items.map((it) => it.closed));
}
const MorphIcon = forwardRef(function MorphIcon(props, ref) {
	const { icon, from, to, progress, spring, reducedMotion, size = 24, color = "currentColor", strokeWidth = 2, absoluteStrokeWidth, label, ...rest } = props;
	const controlled = from !== void 0 && to !== void 0;
	const initialIcon = icon ?? from ?? to;
	const [initialD] = useState(() => {
		if (controlled) return frozenD(from, to, progress ?? 0);
		return initialIcon !== void 0 ? canonicalD(initialIcon) : "";
	});
	const pathRef = useRef(null);
	const morphRef = useRef(null);
	const springRef = useRef(spring);
	springRef.current = spring;
	const rmRef = useRef(reducedMotion);
	rmRef.current = reducedMotion;
	const prevIcon = useRef(icon);
	const prevControlled = useRef(controlled);
	const dead = useRef(false);
	const based = useRef(false);
	const pair = useRef(null);
	/** Driver birth, lazy included (#1 of the lifecycle contract): an
	*  iconless mount keeps the element and the FIRST icon to show up (prop
	*  or imperative) creates the driver, already showing it — no flight.
	*  Stable across renders: closes over refs only. */
	const ensure = useCallback((birth) => {
		if (morphRef.current) return morphRef.current;
		const el = pathRef.current;
		if (dead.current || !el) return null;
		morphRef.current = createMorph(el, birth, { reducedMotion: rmRef.current });
		return morphRef.current;
	}, []);
	useIsoLayoutEffect(() => {
		dead.current = false;
		const el = pathRef.current;
		if (el && initialIcon !== void 0) {
			const m = createMorph(el, controlled ? from : initialIcon, { reducedMotion: rmRef.current });
			morphRef.current = m;
			if (controlled) {
				pair.current = [from, to];
				const t = progress ?? 0;
				if (t <= 0) m.set(from);
				else if (t >= 1) m.set(to);
				else {
					m.seek(to, t);
					based.current = true;
				}
			}
		}
		return () => {
			dead.current = true;
			morphRef.current?.destroy();
			morphRef.current = null;
			based.current = false;
			pair.current = null;
		};
	}, []);
	useEffect(() => {
		const m = morphRef.current;
		if (m) m.reducedMotion = reducedMotion ?? "never";
	}, [reducedMotion]);
	useEffect(() => {
		const left = prevControlled.current && !controlled;
		prevControlled.current = controlled;
		const changed = icon !== prevIcon.current;
		prevIcon.current = icon;
		if (controlled) return;
		if (icon === void 0 || !changed && !left) return;
		pair.current = null;
		based.current = false;
		const m = morphRef.current;
		if (m) m.morphTo(icon, springRef.current);
		else ensure(icon);
	}, [
		icon,
		controlled,
		ensure
	]);
	useEffect(() => {
		if (!controlled) return;
		const m = morphRef.current ?? ensure(from);
		if (!m) return;
		const t = progress ?? 0;
		if (!pair.current || pair.current[0] !== from || pair.current[1] !== to) {
			pair.current = [from, to];
			based.current = false;
		}
		if (t <= 0) {
			m.set(from);
			based.current = false;
		} else if (t >= 1) {
			m.set(to);
			based.current = false;
		} else {
			if (!based.current) {
				m.set(from);
				based.current = true;
			}
			m.seek(to, t);
		}
	}, [
		controlled,
		from,
		to,
		progress,
		ensure
	]);
	useImperativeHandle(ref, () => ({
		morphTo: (i, s) => {
			pair.current = null;
			based.current = false;
			const m = morphRef.current;
			if (m) m.morphTo(i, s ?? springRef.current);
			else ensure(i);
		},
		set: (i) => {
			pair.current = null;
			based.current = false;
			const m = morphRef.current;
			if (m) m.set(i);
			else ensure(i);
		}
	}), [ensure]);
	const sw = absoluteStrokeWidth ? Number(strokeWidth) * 24 / Number(size) : strokeWidth;
	return /* @__PURE__ */ jsxs("svg", {
		xmlns: "http://www.w3.org/2000/svg",
		width: size,
		height: size,
		viewBox: "0 0 24 24",
		fill: "none",
		stroke: color,
		strokeWidth: sw,
		strokeLinecap: "round",
		strokeLinejoin: "round",
		role: label ? "img" : void 0,
		"aria-hidden": label ? void 0 : true,
		...rest,
		children: [label ? /* @__PURE__ */ jsx("title", { children: label }) : null, /* @__PURE__ */ jsx("path", {
			ref: pathRef,
			d: initialD
		})]
	});
});
//#endregion
export { MorphIcon };
