import { a as buildPlan, c as interpPolar, o as allocOutputs, r as resampleIcon } from "./spring-CFHloqPP.js";
import { i as serialize } from "./normalize-CYnN3Npw.js";
import { canonicalD, createMorph } from "./dom.js";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { jsx } from "react/jsx-runtime";
import { AccessibilityInfo } from "react-native";
import Svg, { Path } from "react-native-svg";
//#region src/react-native/iso-layout-effect.ts
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;
//#endregion
//#region src/react-native/index.tsx
let reduced = false;
let rmInit = false;
function initReducedMotion() {
	if (rmInit) return;
	rmInit = true;
	try {
		AccessibilityInfo.isReduceMotionEnabled().then((value) => {
			reduced = value;
		}, () => {});
		AccessibilityInfo.addEventListener("reduceMotionChanged", (value) => {
			reduced = value;
		});
	} catch {}
}
/** Frozen shape of the from→to pair at t, using the pure core. At exact
*  endpoints returns the canonical `d` (real curves, not polyline). */
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
	const liveD = useRef(initialD);
	const declaredD = liveD.current;
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
	/** The PathEl shim: createMorph only ever writes `d`, and Path forwards
	*  it to the native view without re-rendering. Stable identity. */
	const el = useRef({ setAttribute: (name, value) => {
		if (name !== "d") return;
		liveD.current = value;
		pathRef.current?.setNativeProps({ d: value });
	} });
	/** morphTo that applies the reduced-motion policy ("user" reads the OS
	*  setting via AccessibilityInfo, best-effort). */
	const fly = (m, i, s) => {
		const mode = rmRef.current ?? "never";
		if (mode === "user") initReducedMotion();
		if (mode === "always" || mode === "user" && reduced) m.set(i);
		else m.morphTo(i, s);
	};
	const flyRef = useRef(fly);
	flyRef.current = fly;
	/** Driver birth, lazy included (#1 of the lifecycle contract): an
	*  iconless mount keeps the element and the FIRST icon to show up (prop
	*  or imperative) creates the driver, already showing it — no flight.
	*  Stable across renders: closes over refs only. */
	const ensure = useCallback((birth) => {
		if (morphRef.current) return morphRef.current;
		if (dead.current || !pathRef.current) return null;
		morphRef.current = createMorph(el.current, birth);
		return morphRef.current;
	}, []);
	useIsoLayoutEffect(() => {
		dead.current = false;
		if (pathRef.current && initialIcon !== void 0) {
			const m = createMorph(el.current, controlled ? from : initialIcon);
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
	useIsoLayoutEffect(() => {
		if (liveD.current !== declaredD) pathRef.current?.setNativeProps({ d: liveD.current });
	});
	useEffect(() => {
		if (reducedMotion === "user") initReducedMotion();
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
		if (m) flyRef.current(m, icon, springRef.current);
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
			if (m) flyRef.current(m, i, s ?? springRef.current);
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
	return /* @__PURE__ */ jsx(Svg, {
		width: size,
		height: size,
		viewBox: "0 0 24 24",
		fill: "none",
		stroke: color,
		strokeWidth: sw,
		strokeLinecap: "round",
		strokeLinejoin: "round",
		role: label ? "img" : void 0,
		"aria-label": label,
		"aria-hidden": label ? void 0 : true,
		...rest,
		children: /* @__PURE__ */ jsx(Path, {
			ref: pathRef,
			d: declaredD
		})
	});
});
//#endregion
export { MorphIcon };
