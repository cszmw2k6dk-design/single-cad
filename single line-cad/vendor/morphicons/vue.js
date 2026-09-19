import { a as buildPlan, c as interpPolar, o as allocOutputs, r as resampleIcon } from "./spring-CFHloqPP.js";
import { i as serialize } from "./normalize-CYnN3Npw.js";
import { canonicalD, createMorph } from "./dom.js";
import { defineComponent, h, onBeforeUnmount, onMounted, shallowRef, watch } from "vue";
//#region src/vue/index.ts
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
const MorphIcon = defineComponent({
	name: "MorphIcon",
	props: {
		/** Uncontrolled mode: the current icon; changing the prop animates. */
		icon: {
			type: [String, Array],
			default: void 0
		},
		/** Controlled mode: source endpoint of the pair. */
		from: {
			type: [String, Array],
			default: void 0
		},
		/** Controlled mode: target endpoint of the pair. */
		to: {
			type: [String, Array],
			default: void 0
		},
		/** Controlled mode: 0..1 progress of the frozen morph (no spring). */
		progress: {
			type: Number,
			default: void 0
		},
		/** Physics for uncontrolled/imperative mode: preset or custom spring. */
		spring: {
			type: [String, Object],
			default: void 0
		},
		/** Reduced-motion policy: "never" (default) animates regardless of the OS
		*  setting, "user" honors prefers-reduced-motion (morphs degrade to an
		*  instant swap while it is on), "always" always jumps. */
		reducedMotion: {
			type: String,
			default: "never"
		},
		size: {
			type: [Number, String],
			default: 24
		},
		color: {
			type: String,
			default: "currentColor"
		},
		strokeWidth: {
			type: [Number, String],
			default: 2
		},
		/** Like lucide: stroke width does not scale with `size`. */
		absoluteStrokeWidth: {
			type: Boolean,
			default: false
		},
		/** Accessibility: with label → role="img" + <title>; without → aria-hidden. */
		label: {
			type: String,
			default: void 0
		}
	},
	setup(props, { expose }) {
		const initialD = (() => {
			const { icon, from, to, progress } = props;
			if (from !== void 0 && to !== void 0) return frozenD(from, to, progress ?? 0);
			const first = icon ?? from ?? to;
			return first !== void 0 ? canonicalD(first) : "";
		})();
		const pathEl = shallowRef(null);
		let morph = null;
		let dead = false;
		let based = false;
		let pair = null;
		let prevIcon = props.icon;
		let prevControlled = props.from !== void 0 && props.to !== void 0;
		/** Driver birth, lazy included (#1 of the lifecycle contract): an
		*  iconless mount keeps the element and the FIRST icon to show up (prop
		*  or imperative) creates the driver, already showing it — no flight. */
		const ensure = (birth) => {
			if (morph) return morph;
			const el = pathEl.value;
			if (dead || !el) return null;
			morph = createMorph(el, birth, { reducedMotion: props.reducedMotion });
			return morph;
		};
		onMounted(() => {
			const el = pathEl.value;
			const { icon, from, to, progress } = props;
			const controlled = from !== void 0 && to !== void 0;
			const initialIcon = icon ?? from ?? to;
			if (!el || initialIcon === void 0) return;
			const m = createMorph(el, controlled ? from : initialIcon, { reducedMotion: props.reducedMotion });
			morph = m;
			if (controlled) {
				pair = [from, to];
				const t = progress ?? 0;
				if (t <= 0) m.set(from);
				else if (t >= 1) m.set(to);
				else {
					m.seek(to, t);
					based = true;
				}
			}
		});
		onBeforeUnmount(() => {
			dead = true;
			morph?.destroy();
			morph = null;
			based = false;
			pair = null;
		});
		watch(() => props.reducedMotion, (v) => {
			if (morph) morph.reducedMotion = v;
		});
		watch(() => [
			props.icon,
			props.from,
			props.to
		], () => {
			const { icon, from, to } = props;
			const controlled = from !== void 0 && to !== void 0;
			const left = prevControlled && !controlled;
			prevControlled = controlled;
			const changed = icon !== prevIcon;
			prevIcon = icon;
			if (controlled) return;
			if (icon === void 0 || !changed && !left) return;
			pair = null;
			based = false;
			if (morph) {
				morph.reducedMotion = props.reducedMotion;
				morph.morphTo(icon, props.spring);
			} else ensure(icon);
		});
		watch(() => [
			props.from,
			props.to,
			props.progress
		], () => {
			const { from, to, progress } = props;
			if (from === void 0 || to === void 0) return;
			const m = morph ?? ensure(from);
			if (!m) return;
			const t = progress ?? 0;
			if (!pair || pair[0] !== from || pair[1] !== to) {
				pair = [from, to];
				based = false;
			}
			if (t <= 0) {
				m.set(from);
				based = false;
			} else if (t >= 1) {
				m.set(to);
				based = false;
			} else {
				if (!based) {
					m.set(from);
					based = true;
				}
				m.seek(to, t);
			}
		});
		expose({
			morphTo: (i, s) => {
				pair = null;
				based = false;
				if (morph) morph.morphTo(i, s ?? props.spring);
				else ensure(i);
			},
			set: (i) => {
				pair = null;
				based = false;
				if (morph) morph.set(i);
				else ensure(i);
			}
		});
		return () => {
			const sw = props.absoluteStrokeWidth ? Number(props.strokeWidth) * 24 / Number(props.size) : props.strokeWidth;
			return h("svg", {
				xmlns: "http://www.w3.org/2000/svg",
				width: props.size,
				height: props.size,
				viewBox: "0 0 24 24",
				fill: "none",
				stroke: props.color,
				"stroke-width": sw,
				"stroke-linecap": "round",
				"stroke-linejoin": "round",
				role: props.label ? "img" : void 0,
				"aria-hidden": props.label ? void 0 : "true"
			}, [props.label ? h("title", props.label) : null, h("path", {
				ref: pathEl,
				d: initialD
			})]);
		};
	}
});
//#endregion
export { MorphIcon };
