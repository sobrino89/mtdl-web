// The channel as a badge: its logo when the setting is on and the server has one (logos.py), else the name.
import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { useSettings } from "../store";

const missing = new Set<string>(); // channels without a logo: asked once per page load, then text

export function ChannelBadge(props: { channel: string; small?: boolean }): JSX.Element {
  const settings = useSettings();
  const [failed, setFailed] = useState(missing.has(props.channel));
  const kind = `badge${props.small ? " small-badge" : ""}`;
  if (!settings?.channel_logos || failed) return <span class={kind}>{props.channel}</span>;
  return (
    <span class={`${kind} logo-badge`} title={props.channel}>
      <img
        src={`/api/v1/logos/${encodeURIComponent(props.channel)}`}
        alt={props.channel}
        loading="lazy"
        onError={() => {
          missing.add(props.channel);
          setFailed(true);
        }}
      />
    </span>
  );
}
