import { CinematicRenderer } from "@composer/CinematicRenderer";
import { Explainer } from "@composer/Explainer";
import { Player, PlayerRef } from "@remotion/player";
import { Component, ReactNode, useEffect, useMemo, useRef } from "react";
import type { TimelineInfo } from "./timelineModel";
import { cutsToCinematicScenes, mapStrings } from "./timelineModel";

const COMPOSITIONS = { Explainer, CinematicRenderer } as const;

/** Props for the composition, built the way video_compose builds them for a render. */
function buildProps(draft: Record<string, unknown>, info: TimelineInfo): Record<string, unknown> {
  const origin = window.location.origin;
  const props = mapStrings(draft, (s) => (info.assets[s] ? origin + info.assets[s].url : s)) as Record<string, unknown>;
  if (info.theme && !props.themeConfig) props.themeConfig = info.theme;
  const captionProps = (draft.subtitles as Record<string, unknown> | undefined)?.caption_props;
  if (captionProps && typeof captionProps === "object") props.captionProps = captionProps;
  if (info.composition === "Explainer") adaptForExplainer(props, origin);
  if (info.composition === "CinematicRenderer") {
    if (!Array.isArray(props.scenes) || props.scenes.length === 0) {
      props.scenes = cutsToCinematicScenes((props.cuts as Record<string, unknown>[]) ?? []);
    }
    delete props.cuts;
  }
  return props;
}

/**
 * Canonical edit decisions name assets by id and time overlays with
 * start/end; the Explainer composition wants `src` and typed in/out overlays.
 * Pass through what it can play and drop what would make it throw.
 */
function adaptForExplainer(props: Record<string, unknown>, origin: string) {
  const isUrl = (v: unknown): v is string => typeof v === "string" && (v.startsWith(origin) || /^https?:/.test(v));
  if (Array.isArray(props.overlays)) {
    props.overlays = props.overlays.filter((o) => {
      const overlay = o as Record<string, unknown> | null;
      return overlay && typeof overlay.type === "string" && Number.isFinite(overlay.in_seconds) && Number.isFinite(overlay.out_seconds);
    });
  }
  const audio = { ...((props.audio as Record<string, unknown>) ?? {}) };
  const layer = (value: unknown) => {
    const record = (value as Record<string, unknown>) ?? {};
    const src = isUrl(record.src) ? record.src : isUrl(record.asset_id) ? record.asset_id : null;
    if (!src) return undefined;
    return {
      src,
      volume: typeof record.volume === "number" ? record.volume : undefined,
      fadeInSeconds: record.fadeInSeconds ?? record.fade_in_seconds,
      fadeOutSeconds: record.fadeOutSeconds ?? record.fade_out_seconds,
      offsetSeconds: record.offsetSeconds,
      loop: record.loop,
    };
  };
  props.audio = { narration: layer(audio.narration), music: layer(audio.music ?? props.music) };
}

class Boundary extends Component<{ children: ReactNode; resetKey: unknown }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  componentDidUpdate(prev: { resetKey: unknown }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return <p className="notice bad">The preview could not render these edit decisions: {this.state.error}</p>;
    }
    return this.props.children;
  }
}

export default function RemotionPreview({ draft, info, duration, seek, onTime }: {
  draft: Record<string, unknown>;
  info: TimelineInfo;
  duration: number;
  /** Changes when the user clicks the ruler; the player jumps there. */
  seek: { time: number; n: number };
  onTime: (seconds: number) => void;
}) {
  const player = useRef<PlayerRef>(null);
  const props = useMemo(() => buildProps(draft, info), [draft, info]);
  const component = COMPOSITIONS[info.composition as keyof typeof COMPOSITIONS];
  const frames = Math.max(1, Math.ceil(duration * info.fps));

  useEffect(() => {
    const current = player.current;
    if (!current) return;
    const onFrame = (e: { detail: { frame: number } }) => onTime(e.detail.frame / info.fps);
    current.addEventListener("frameupdate", onFrame);
    return () => current.removeEventListener("frameupdate", onFrame);
  }, [info.fps, onTime]);

  useEffect(() => {
    if (seek.n > 0) player.current?.seekTo(Math.min(frames - 1, Math.round(seek.time * info.fps)));
  }, [seek, frames, info.fps]);

  return (
    <Boundary resetKey={props}>
      <div className="player-frame" style={{ maxWidth: `calc(52vh * ${info.width / info.height})` }}>
      <Player
        ref={player}
        component={component}
        inputProps={props}
        durationInFrames={frames}
        fps={info.fps}
        compositionWidth={info.width}
        compositionHeight={info.height}
        controls
        acknowledgeRemotionLicense
        style={{ width: "100%", aspectRatio: `${info.width} / ${info.height}` }}
        errorFallback={({ error }) => (
          // Rendered inside the composition's own coordinate space, hence the large type.
          <p style={{ font: "44px system-ui", color: "#fff", padding: 80 }}>Preview error: {error.message}</p>
        )}
      />
      </div>
    </Boundary>
  );
}
