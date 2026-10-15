// Small shared controls: switch, the bandwidth limit fields (status bar popover, downloads, settings), copying.
import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { api, type Settings, type Status } from "../api";
import * as F from "../format";
import { t } from "../i18n";
import { limitAction, limitToggle } from "../status-texts";
import { refreshStatus, saveSettings, toast } from "../store";

export function Switch(props: {
  on: boolean;
  label: string;
  onToggle: () => void;
  small?: boolean;
  disabled?: boolean;
}): JSX.Element {
  return (
    <button
      role="switch"
      aria-checked={props.on}
      aria-label={props.label}
      disabled={props.disabled}
      class={`switch${props.on ? " on" : ""}${props.small ? " small" : ""}`}
      onClick={props.onToggle}
    >
      <span />
    </button>
  );
}

export function limitConversion(limit: Status["limit"]): string {
  const unit = limit.speed_unit;
  const other = unit === "MB/s" ? "Mbit/s" : "MB/s";
  if (limit.bandwidth_limit <= 0) return t("limit.zero");
  const text = t("limit.conv", {
    a: `${F.dec(F.fromBytesPerSecond(limit.bandwidth_limit, unit))} ${unit}`,
    b: `${F.dec(F.fromBytesPerSecond(limit.bandwidth_limit, other))} ${other}`,
  });
  return text + (limit.bandwidth_enabled ? "" : t("limit.off_now"));
}

function saveLimit(changes: Partial<Settings>): void {
  void saveSettings(changes).catch(() => toast(t("settings.save_failed")));
}

/** On means a limit is in effect now; switching on without a value starts at 50 Mbit/s. Inside a schedule window
 * the switch opens the window early (until it ends) or returns to the schedule. With ``normal`` (the settings
 * next to the value) it switches only the normal limit. */
export function LimitSwitch(props: { limit: Status["limit"]; small?: boolean; normal?: boolean }): JSX.Element {
  const { limit } = props;
  const toggle = () => {
    const action = props.normal ? { settings: limitToggle(limit) } : limitAction(limit);
    if ("settings" in action) saveLimit(action.settings);
    else void api.pauseLimit(action.pause).then(refreshStatus, () => toast(t("settings.save_failed")));
  };
  const on = props.normal ? limit.bandwidth_enabled && limit.bandwidth_limit > 0 : limit.rate > 0;
  return <Switch on={on} label={t("limit.switch")} onToggle={toggle} small={props.small} />;
}

/** Value and unit of the global limit (and optional presets); every change is saved at once and applies to
 * running downloads without a restart. */
export function LimitFields(props: { limit: Status["limit"]; presets?: boolean; children?: JSX.Element }): JSX.Element {
  const { limit } = props;
  const unit = limit.speed_unit;
  const on = limit.bandwidth_enabled && limit.bandwidth_limit > 0;
  const shown = limit.bandwidth_limit > 0 ? F.dec(F.fromBytesPerSecond(limit.bandwidth_limit, unit)) : "";
  const [draft, setDraft] = useState<string | null>(null);
  const onInput = (text: string) => {
    setDraft(text);
    const value = F.parseDecimal(text);
    if (value === null) saveLimit({ bandwidth_limit: 0 });
    else if (!Number.isNaN(value) && value >= 0) {
      const bytes = F.toBytesPerSecond(value, unit);
      saveLimit({ bandwidth_limit: bytes, bandwidth_enabled: bytes > 0 ? true : limit.bandwidth_enabled });
    }
  };
  const presets = unit === "MB/s" ? [2, 5, 10, 25] : [10, 20, 50, 100];
  return (
    <>
      <div class="row">
        {props.children}
        <input
          type="text"
          inputMode="decimal"
          class={`input limit-input${on || !shown ? "" : " dimmed"}`}
          value={draft ?? shown}
          onInput={(e) => onInput((e.target as HTMLInputElement).value)}
          onBlur={() => setDraft(null)}
          aria-label={t("limit.value")}
          placeholder={t("limit.unlimited")}
        />
        <div class="segmented unit-seg" role="radiogroup" aria-label={t("limit.unit")}>
          {(["Mbit/s", "MB/s"] as const).map((u) => (
            <button
              key={u}
              role="radio"
              aria-checked={unit === u}
              class={unit === u ? "on" : ""}
              onClick={() => (setDraft(null), saveLimit({ speed_unit: u }))}
            >
              {u}
            </button>
          ))}
        </div>
      </div>
      {props.presets && (
        <div class="row wrap">
          {presets.map((p) => {
            const bytes = F.toBytesPerSecond(p, unit);
            const active = limit.bandwidth_enabled && Math.abs(limit.bandwidth_limit - bytes) < 2;
            return (
              <button
                key={p}
                class={`preset${active ? " on" : ""}`}
                onClick={() => (setDraft(null), saveLimit({ bandwidth_limit: bytes, bandwidth_enabled: true }))}
              >
                {p}
              </button>
            );
          })}
          <button class="preset" onClick={() => (setDraft(null), saveLimit({ bandwidth_enabled: false }))}>
            {t("limit.no_limit_button")}
          </button>
        </div>
      )}
    </>
  );
}

/** Clipboard also over plain http in the LAN, where navigator.clipboard does not exist (no secure context). */
export function copyText(text: string): void {
  const done = () => toast(t("copy.done"));
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    return;
  }
  fallbackCopy(text, done);
}

function fallbackCopy(text: string, done: () => void): void {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  if (ok) done();
  else toast(t("copy.failed"));
}
