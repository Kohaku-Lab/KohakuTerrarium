import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { PetPlayer } from "./player.js";
import { PetGestures } from "./gestures.js";

export const PetSprite = {
  name: "VirtualPetSprite",
  props: {
    atlas: { type: Object, required: true },
    source: { type: String, required: true },
    work: { type: Object, default: null },
    scale: { type: Number, default: 1 },
    paused: { type: Boolean, default: false },
    fit: { type: Boolean, default: false },
    interactive: { type: Boolean, default: false },
    greet: { type: Boolean, default: false },
  },
  emits: ["drag-start", "drag-move"],
  setup(props, { emit }) {
    const stage = ref(null);
    const stageWidth = ref(320);
    const stageHeight = ref(0);
    const cell = ref({ row: 0, column: 0, action: "idle" });
    const reduced = ref(false);
    let player = new PetPlayer(props.atlas);
    let animation = null;
    let mounted = false;
    let observer;
    let media;

    const factor = computed(() => {
      const widthLimit = (stageWidth.value - 16) / props.atlas.cellWidth;
      return props.fit
        ? Math.max(
            0,
            Math.min(
              props.scale,
              widthLimit,
              (stageHeight.value - 24) / props.atlas.cellHeight,
            ),
          )
        : Math.min(props.scale, Math.max(0.1, widthLimit));
    });
    const spriteStyle = computed(() => ({
      width: `${props.atlas.cellWidth * factor.value}px`,
      height: `${props.atlas.cellHeight * factor.value}px`,
      backgroundImage: `url("${props.source}")`,
      backgroundSize: `${props.atlas.width * factor.value}px ${props.atlas.height * factor.value}px`,
      backgroundPosition: `${-cell.value.column * props.atlas.cellWidth * factor.value}px ${-cell.value.row * props.atlas.cellHeight * factor.value}px`,
      left: "50%",
      transform: "translateX(-50%)",
    }));
    const stageStyle = computed(() =>
      props.fit
        ? { height: "100%", minHeight: "0" }
        : { minHeight: `${props.atlas.cellHeight * factor.value + 24}px` },
    );

    const gestures = new PetGestures(
      () => player,
      refresh,
      (event) => emit("drag-start", event),
      (delta) => emit("drag-move", delta),
    );
    function enter(event) {
      if (props.interactive) gestures.enter(event);
    }
    function down(event) {
      if (props.interactive) gestures.down(event);
    }
    function keydown(event) {
      if (
        props.interactive &&
        !event.repeat &&
        ["Enter", " "].includes(event.key)
      ) {
        event.preventDefault();
        player.cue("waving", performance.now());
        refresh();
      }
    }
    function visibilityChanged() {
      if (document.hidden) {
        gestures.dispose();
        player.cancel();
      }
      refresh();
    }
    function cancel() {
      if (animation !== null) cancelAnimationFrame(animation);
      animation = null;
    }
    function draw(now) {
      animation = null;
      cell.value = player.tick(now, props.paused || reduced.value);
      if (mounted && !props.paused && !reduced.value && !document.hidden) {
        animation = requestAnimationFrame(draw);
      }
    }
    function refresh() {
      cancel();
      if (mounted) draw(performance.now());
    }
    function motionChanged() {
      reduced.value = media.matches;
      refresh();
    }
    watch(
      () => [props.atlas.key, props.source],
      () => {
        player = new PetPlayer(props.atlas);
        player.setWork(props.work, performance.now());
        refresh();
      },
    );
    watch(
      () => props.work,
      (work) => {
        player.setWork(work, performance.now());
        refresh();
      },
      { immediate: true },
    );
    watch(() => props.paused, refresh);

    onMounted(() => {
      mounted = true;
      media = window.matchMedia("(prefers-reduced-motion: reduce)");
      media.addEventListener("change", motionChanged);
      document.addEventListener("visibilitychange", visibilityChanged);
      observer = new ResizeObserver(([entry]) => {
        stageWidth.value = entry.contentRect.width;
        stageHeight.value = entry.contentRect.height;
      });
      observer.observe(stage.value);
      motionChanged();
      if (props.interactive && props.greet && !document.hidden) {
        player.cue("waving", performance.now());
        refresh();
      }
    });
    onUnmounted(() => {
      mounted = false;
      gestures.dispose();
      cancel();
      observer?.disconnect();
      media?.removeEventListener("change", motionChanged);
      document.removeEventListener("visibilitychange", visibilityChanged);
    });
    return { stage, cell, spriteStyle, stageStyle, enter, down, keydown };
  },
  template: `
    <div ref="stage" class="vp-stage" :style="stageStyle">
      <div class="vp-sprite kt-overlay-hit" :style="spriteStyle" :role="interactive ? 'button' : 'img'" :tabindex="interactive ? 0 : undefined"
        :aria-label="atlas.displayName" :data-action="cell.action" @pointerenter="enter" @pointerdown="down" @keydown="keydown"></div>
    </div>`,
};
