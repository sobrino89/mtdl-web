// Line icons from the design (24×24 paths) and the mtdl mark.
import type { JSX } from "preact";

export const I = {
  search: "M10.5 17.5a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM15.5 15.5 20.5 20.5",
  help: "M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17zM9.6 9.6a2.5 2.5 0 0 1 4.9.7c0 1.7-2.5 2.1-2.5 3.7M12 17h.01",
  abos: "M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V7A1.5 1.5 0 0 1 5 5.5zM3.5 10h17M8 3.5v4M16 3.5v4M12 15h.01",
  aboAdd:
    "M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V7A1.5 1.5 0 0 1 5 5.5zM3.5 10h17M8 3.5v4M16 3.5v4M12 13v5M9.5 15.5h5",
  download: "M12 3.5v12M7 11l5 5 5-5M4.5 20h15",
  history: "M4 12a8 8 0 1 0 2.35-5.65M4 4.5V8h3.5M12 8v4l2.5 2.5",
  settings: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  monitor: "M4 5h16v11H4zM9 20h6M12 16v4",
  gauge: "M4.5 17a8 8 0 1 1 15 0M12 13l3.5-4",
  warn: "M12 3.5 21.5 20h-19zM12 10v4.5M12 17.5h.01",
  close: "M6 6l12 12M18 6 6 18",
  copy: "M9 9h10.5v10.5H9zM15 9V4.5H4.5V15H9",
  check: "M5 12.5l4.5 4.5L19 7.5",
  clock: "M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 8v4l2.5 2",
  chevron: "M6 9l6 6 6-6",
  play: "M8 5.5v13l10.5-6.5z",
  pause: "M9 5.5v13M15 5.5v13",
  external: "M14 4.5h5.5V10M19.5 4.5 11 13M17.5 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7.5a1 1 0 0 1 1-1h5",
  retry: "M4 12a8 8 0 1 0 2.35-5.65M4 4.5V8h3.5",
  trash: "M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7",
  plus: "M12 5v14M5 12h14",
  folder: "M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4.5l2 2.5H19A1.5 1.5 0 0 1 20.5 9.5V18A1.5 1.5 0 0 1 19 19.5H5A1.5 1.5 0 0 1 3.5 18z",
  arrowUp: "M12 19V5M6 11l6-6 6 6",
  more: "M12 6.5h.01M12 12h.01M12 17.5h.01",
  thumbDown: "M7 13.5V4.5M7 13.5l3.5 6c1.3 0 2.2-1.1 1.9-2.4L11.5 14h5.3a2 2 0 0 0 2-2.3l-1-6a2 2 0 0 0-2-1.7H7",
};

export function Icon(props: { d: string; size?: number; stroke?: number; class?: string; fill?: boolean }): JSX.Element {
  const { d, size = 16, stroke = 1.8, fill = false } = props;
  return (
    <svg
      class={props.class}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill ? "currentColor" : "none"}
      stroke="currentColor"
      stroke-width={stroke}
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

/** Broadcast schedule with a recording dot. */
export function Logo(props: { size?: number }): JSX.Element {
  const size = props.size ?? 26;
  return (
    <svg class="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7.5" class="logo-bg" />
      <circle cx="10.5" cy="11" r="3.6" class="logo-rec" />
      <rect x="16.5" y="9.4" width="9.5" height="3.2" rx="1.6" class="logo-line" />
      <rect x="6.9" y="15.4" width="19.1" height="3.2" rx="1.6" class="logo-line" opacity=".6" />
      <rect x="6.9" y="21.4" width="12.6" height="3.2" rx="1.6" class="logo-line" opacity=".32" />
    </svg>
  );
}
