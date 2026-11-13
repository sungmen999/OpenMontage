import { FormEvent, useEffect, useMemo, useState } from "react";
import { Capability, EnvKey, Settings as SettingsData, get, post } from "./api";
import { navigate } from "./main";

const humanize = (s: string) => s.replace(/_/g, " ");

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = (refresh = false) => {
    setRefreshing(true);
    return get<SettingsData>(`/api/studio/settings${refresh ? "?refresh=1" : ""}`)
      .then(setData, (e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  };
  useEffect(() => {
    load();
  }, []);

  const sections = useMemo(() => {
    const map = new Map<string, EnvKey[]>();
    for (const key of data?.keys ?? []) map.set(key.section, [...(map.get(key.section) ?? []), key]);
    return [...map.entries()];
  }, [data]);

  return (
    <div className="settings">
      <header className="topbar">
        <button className="link" onClick={() => navigate("")}>
          ← Library
        </button>
        <h1>Settings</h1>
        <span className="spacer" />
        <button disabled={refreshing} onClick={() => load(true)}>
          {refreshing ? "Checking…" : "Re-check tools"}
        </button>
      </header>

      {error && <p className="notice bad">{error}</p>}
      {!data && !error && <p className="muted pad">Checking which tools are available on this machine…</p>}

      {data && (
        <div className="settings-body">
          <section className="panel">
            <h3>Composition runtimes</h3>
            <div className="row">
              {Object.entries(data.composition_runtimes).map(([name, ok]) => (
                <span key={name} className={ok ? "pill live" : "pill"}>
                  {name}: {ok ? "available" : "not available"}
                </span>
              ))}
            </div>
            {data.runtime_warnings.map((w) => (
              <p key={w} className="muted small">
                {w}
              </p>
            ))}
          </section>

          <section className="panel">
            <h3>Capabilities</h3>
            <div className="caps">
              {data.capabilities.map((c) => (
                <CapabilityRow key={c.capability} cap={c} />
              ))}
            </div>
          </section>

          <section className="panel">
            <h3>Keys and configuration</h3>
            <p className="muted small">
              Saved to <code>.env</code> in the repository. Values are never shown again after saving. A production
              that is already running picks up a changed key on its next agent session.
            </p>
            {sections.map(([section, keys]) => (
              <div key={section} className="key-section">
                <h4>{section || "General"}</h4>
                {keys.map((k) => (
                  <KeyRow key={k.name} envKey={k} onSaved={setData} />
                ))}
              </div>
            ))}
          </section>
        </div>
      )}
    </div>
  );
}

function CapabilityRow({ cap }: { cap: Capability }) {
  const full = cap.configured === cap.total;
  return (
    <details className="cap">
      <summary>
        <span className="cap-name">{humanize(cap.capability)}</span>
        <span className={cap.configured === 0 ? "pill warn" : full ? "pill live" : "pill"}>
          {cap.configured} of {cap.total} configured
        </span>
      </summary>
      {cap.available.length > 0 && (
        <p className="small">
          <span className="muted">Ready: </span>
          {cap.available.map((t) => t.name).join(", ")}
        </p>
      )}
      {cap.unavailable.map((t) => (
        <div key={t.name} className="small tool-setup">
          <strong>{t.name}</strong> <span className="muted">({t.runtime})</span>
          <div>{t.install_instructions}</div>
        </div>
      ))}
    </details>
  );
}

function KeyRow({ envKey, onSaved }: { envKey: EnvKey; onSaved: (s: SettingsData) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const external = envKey.source === "environment";

  async function save(next: string) {
    setBusy(true);
    setError(null);
    try {
      onSaved(await post<SettingsData>("/api/studio/settings/env", { name: envKey.name, value: next }));
      setValue("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (value.trim()) save(value);
  }

  return (
    <form className="key-row" onSubmit={submit}>
      <div className="key-info">
        <label htmlFor={`key-${envKey.name}`}>
          <code>{envKey.name}</code>
        </label>
        {envKey.set ? <span className="pill live">set</span> : <span className="pill">not set</span>}
        {envKey.description && <p className="muted small">{envKey.description}</p>}
        {envKey.tools.length > 0 && (
          <p className="muted small">
            Unlocks {envKey.tools.length} tool{envKey.tools.length > 1 ? "s" : ""}: {envKey.tools.join(", ")}
          </p>
        )}
        {external && <p className="muted small">Set in the shell environment, outside .env. Change it there.</p>}
        {error && <p className="notice bad">{error}</p>}
      </div>
      {!external && (
        <div className="key-edit">
          <input
            id={`key-${envKey.name}`}
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={envKey.set ? "Replace value" : envKey.example || "Value"}
          />
          <button className="primary" disabled={busy || !value.trim()}>
            Save
          </button>
          {envKey.set && (
            <button type="button" disabled={busy} onClick={() => save("")}>
              Remove
            </button>
          )}
        </div>
      )}
    </form>
  );
}
