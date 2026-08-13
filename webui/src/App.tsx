import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  ActivityResponse,
  AgentResponse,
  apiGet,
  apiMutation,
  Candidate,
  Exclusion,
  MemoryResponse,
  PatternRecord,
  PrivacyResponse,
  ProcessingRun,
  SettingsResponse,
  StatusResponse,
  WakeResponse,
  WorkspaceData,
} from "./api";

type View =
  | "overview"
  | "activity"
  | "patterns"
  | "memory"
  | "privacy"
  | "agent"
  | "settings";

const views: Array<{ id: View; label: string; glyph: string }> = [
  { id: "overview", label: "Vue d’ensemble", glyph: "⌁" },
  { id: "activity", label: "Activité", glyph: "◷" },
  { id: "patterns", label: "Patterns", glyph: "⌗" },
  { id: "memory", label: "Mémoire", glyph: "◇" },
  { id: "privacy", label: "Confidentialité", glyph: "◉" },
  { id: "agent", label: "Agent", glyph: "⌘" },
  { id: "settings", label: "Réglages", glyph: "⚙" },
];

export function App() {
  const [activeView, setActiveView] = useState<View>(viewFromHash());
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [
        status,
        activity,
        patterns,
        memory,
        privacy,
        processing,
        agent,
        exclusions,
        settings,
      ] = await Promise.all([
        apiGet<StatusResponse>("/status"),
        apiGet<ActivityResponse>("/activity"),
        apiGet<{ patterns: PatternRecord[] }>("/patterns"),
        apiGet<MemoryResponse>("/memory"),
        apiGet<PrivacyResponse>("/privacy"),
        apiGet<{ runs: ProcessingRun[] }>("/processing"),
        apiGet<AgentResponse>("/agent"),
        apiGet<Exclusion[]>("/exclusions"),
        apiGet<SettingsResponse>("/settings"),
      ]);
      setData({
        status,
        activity,
        patterns,
        memory,
        privacy,
        processing,
        agent,
        exclusions,
        settings,
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onHashChange = () => setActiveView(viewFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void apiGet<StatusResponse>("/status")
        .then((status) => {
          setData((current) => (current ? { ...current, status } : current));
        })
        .catch((caught) => setError(errorMessage(caught)));
    }, 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const mutate = useCallback(
    async (operation: () => Promise<unknown>) => {
      setMutating(true);
      setError(null);
      try {
        await operation();
        await load();
      } catch (caught) {
        setError(errorMessage(caught));
      } finally {
        setMutating(false);
      }
    },
    [load],
  );

  const selectView = (view: View) => {
    window.location.hash = view;
    setActiveView(view);
  };

  const collection = data ? collectionPresentation(data.status.collection) : null;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            C
          </span>
          <div>
            <strong>CONTX</strong>
            <span>Mémoire locale</span>
          </div>
        </div>

        <nav aria-label="Navigation principale">
          {views.map((view) => (
            <button
              className={activeView === view.id ? "nav-item active" : "nav-item"}
              key={view.id}
              onClick={() => selectView(view.id)}
              type="button"
            >
              <span aria-hidden="true">{view.glyph}</span>
              {view.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <span
            className={`status-dot ${data?.status.health ?? "offline"}`}
            aria-hidden="true"
          />
          <div>
            <strong>
              {data?.status.health === "healthy"
                ? "Système opérationnel"
                : data
                  ? "Attention requise"
                  : "Connexion locale"}
            </strong>
            <span>API {data?.status.api_version ?? "v1"}</span>
          </div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="eyebrow">ESPACE PERSONNEL · LOCAL UNIQUEMENT</p>
            <h1>{views.find((item) => item.id === activeView)?.label}</h1>
          </div>
          <div className="topbar-actions">
            <button
              className="button secondary"
              disabled={loading || mutating}
              onClick={() => void load()}
              type="button"
            >
              Actualiser
            </button>
            {data && collection && (
              <button
                className={
                  collection.action === "resume"
                    ? "button accent"
                    : collection.action === "pause"
                      ? "button danger"
                      : "button secondary"
                }
                disabled={mutating || collection.action === null}
                onClick={() =>
                  collection.action &&
                  void mutate(() => apiMutation(`/${collection.action}`, "POST", {}))
                }
                type="button"
              >
                {collection.actionLabel}
              </button>
            )}
          </div>
        </header>

        {error && (
          <div className="notice error" role="alert">
            <div>
              <strong>Le service local n’a pas pu terminer l’action.</strong>
              <p>{error}</p>
            </div>
            <button onClick={() => setError(null)} type="button" aria-label="Fermer">
              ×
            </button>
          </div>
        )}

        {loading && !data ? (
          <LoadingState />
        ) : data ? (
          <ViewContent
            activeView={activeView}
            data={data}
            disabled={mutating}
            mutate={mutate}
          />
        ) : (
          <EmptyState
            title="Interface indisponible"
            detail="Vérifie que le service CONTX écoute bien sur 127.0.0.1, puis actualise cette page."
          />
        )}
      </main>
    </div>
  );
}

function ViewContent({
  activeView,
  data,
  disabled,
  mutate,
}: {
  activeView: View;
  data: WorkspaceData;
  disabled: boolean;
  mutate: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  if (activeView === "overview") {
    return <Overview status={data.status} processing={data.processing.runs} />;
  }
  if (activeView === "activity") {
    return (
      <Activity
        data={data.activity}
        disabled={disabled}
        mutate={mutate}
      />
    );
  }
  if (activeView === "patterns") {
    return <Patterns patterns={data.patterns.patterns} />;
  }
  if (activeView === "memory") {
    return <Memory data={data.memory} disabled={disabled} mutate={mutate} />;
  }
  if (activeView === "privacy") {
    return (
      <Privacy
        data={data.privacy}
        exclusions={data.exclusions}
        disabled={disabled}
        mutate={mutate}
      />
    );
  }
  if (activeView === "agent") {
    return <Agent data={data.agent} disabled={disabled} mutate={mutate} />;
  }
  return <Settings settings={data.settings} exclusions={data.exclusions} />;
}

function Overview({
  status,
  processing,
}: {
  status: StatusResponse;
  processing: ProcessingRun[];
}) {
  const collection = collectionPresentation(status.collection);
  const modelHealthy = status.model.runtime_available && status.model.model_available;
  return (
    <div className="page-stack">
      {status.issues.length > 0 && (
        <section className="notice warning">
          <div>
            <strong>CONTX fonctionne en mode dégradé.</strong>
            <p>{status.issues[0].summary}</p>
          </div>
          <span className="pill warning">{status.issues.length} signalements</span>
        </section>
      )}

      <section className="hero-card">
        <div>
          <p className="eyebrow">ÉTAT EN TEMPS RÉEL</p>
          <h2>{collection.headline}</h2>
          <p>{collection.description}</p>
        </div>
        <div className="hero-orbit" aria-hidden="true">
          <span>{collection.label}</span>
        </div>
      </section>

      <section className="metric-grid" aria-label="Indicateurs principaux">
        <Metric
          label="Collecte"
          value={collection.label}
          detail={collection.detail}
          tone={collection.tone}
        />
        <Metric
          label="Modèle local"
          value={modelHealthy ? "Disponible" : "Indisponible"}
          detail={status.model.model}
          tone={modelHealthy ? "green" : "red"}
        />
        <Metric
          label="Mémoire active"
          value={String(status.counts.memories ?? 0)}
          detail={formatBytes(status.memory_usage_bytes)}
          tone="violet"
        />
        <Metric
          label="Données brutes"
          value={formatBytes(status.raw_usage_bytes)}
          detail={`Rétention locale · ${status.counts.observations ?? 0} observations`}
          tone="blue"
        />
      </section>

      <div className="two-columns">
        <section className="panel">
          <PanelHeading title="Chaîne de traitement" hint="Dernières exécutions" />
          {processing.length ? (
            <div className="timeline-list">
              {processing.slice(0, 6).map((run) => (
                <div className="timeline-row" key={run.id}>
                  <span className={`run-dot ${run.status}`} />
                  <div>
                    <strong>{humanize(run.pipeline)}</strong>
                    <span>{formatDate(run.started_at)}</span>
                  </div>
                  <span className={`pill ${toneForStatus(run.status)}`}>
                    {humanize(run.status)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyInline text="Aucun traitement enregistré." />
          )}
        </section>

        <section className="panel">
          <PanelHeading title="Capacités locales" hint="Aucune demande automatique" />
          <div className="capability-list">
            {status.capabilities.map((capability) => (
              <div className="capability-row" key={capability.name}>
                <div>
                  <strong>{humanize(capability.name)}</strong>
                  <span>{capability.settings_path ?? humanize(capability.reason_code ?? "prêt")}</span>
                </div>
                <span className={`pill ${toneForStatus(capability.status)}`}>
                  {humanize(capability.status)}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Activity({
  data,
  disabled,
  mutate,
}: {
  data: ActivityResponse;
  disabled: boolean;
  mutate: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [summary, setSummary] = useState("");
  const [reason, setReason] = useState("");

  const beginCorrection = (eventId: string, currentSummary: string) => {
    setCorrecting(eventId);
    setSummary(currentSummary);
    setReason("");
  };

  const submitCorrection = (event: FormEvent, eventId: string) => {
    event.preventDefault();
    void mutate(async () => {
      await apiMutation(`/events/${eventId}/corrections`, "POST", {
        summary: summary.trim(),
        reason: reason.trim(),
      });
      setCorrecting(null);
    });
  };

  return (
    <div className="page-stack">
      <section className="metric-grid compact">
        <Metric label="Observations" value={String(data.observations.length)} detail="Métadonnées locales" tone="blue" />
        <Metric label="Événements" value={String(data.events.length)} detail="Interprétations structurées" tone="violet" />
        <Metric label="Corrections" value={String(data.corrections.length)} detail="Historique append-only" tone="amber" />
      </section>
      <div className="two-columns activity-columns">
        <section className="panel">
          <PanelHeading title="Événements récents" hint="Du plus récent au plus ancien" />
          {data.events.length ? (
            <div className="card-list">
              {data.events.map((event) => (
                <article className="record-card" key={event.id}>
                  <div className="record-topline">
                    <span className="pill violet">{humanize(event.type)}</span>
                    <time>{formatDate(event.started_at)}</time>
                  </div>
                  <h3>{event.summary}</h3>
                  <div className="tag-row">
                    {event.projects.map((project) => <span key={project}>#{project}</span>)}
                  </div>
                  <RecordMeta
                    items={[
                      ["Confiance", formatPercent(event.confidence)],
                      ["Sensibilité", humanize(event.sensitivity)],
                      ["Sources", String(event.source_observation_ids.length)],
                    ]}
                  />
                  {correcting === event.id ? (
                    <form
                      className="correction-form"
                      onSubmit={(formEvent) => submitCorrection(formEvent, event.id)}
                    >
                      <label>
                        Résumé corrigé
                        <input
                          maxLength={2000}
                          onChange={(inputEvent) => setSummary(inputEvent.target.value)}
                          required
                          value={summary}
                        />
                      </label>
                      <label>
                        Raison
                        <input
                          maxLength={500}
                          onChange={(inputEvent) => setReason(inputEvent.target.value)}
                          placeholder="Ce qui était incorrect"
                          required
                          value={reason}
                        />
                      </label>
                      <div className="form-actions">
                        <button className="button accent" disabled={disabled} type="submit">Enregistrer la correction</button>
                        <button className="text-button" onClick={() => setCorrecting(null)} type="button">Annuler</button>
                      </div>
                    </form>
                  ) : (
                    <button
                      className="text-button action-link"
                      disabled={disabled}
                      onClick={() => beginCorrection(event.id, event.summary)}
                      type="button"
                    >
                      Corriger cet événement
                    </button>
                  )}
                </article>
              ))}
            </div>
          ) : <EmptyInline text="Aucun événement sémantique n’a encore été produit." />}
        </section>
        <section className="panel">
          <PanelHeading title="Observations" hint="Le chemin des fichiers bruts n’est jamais exposé" />
          {data.observations.length ? (
            <div className="compact-list">
              {data.observations.map((observation) => (
                <article className="compact-record" key={observation.id}>
                  <div>
                    <strong>{observation.app_name ?? humanize(observation.source_type)}</strong>
                    <span>{observation.window_title ?? shortId(observation.id)}</span>
                  </div>
                  <div className="right-meta">
                    <span className={`pill ${observation.excluded ? "red" : "neutral"}`}>
                      {observation.excluded ? "Exclue" : humanize(observation.processing_status)}
                    </span>
                    <time>{formatDate(observation.captured_at)}</time>
                  </div>
                </article>
              ))}
            </div>
          ) : <EmptyInline text="Aucune observation enregistrée." />}
        </section>
      </div>
    </div>
  );
}

function Patterns({ patterns }: { patterns: PatternRecord[] }) {
  return (
    <div className="page-stack">
      <section className="section-intro">
        <div>
          <p className="eyebrow">TENDANCES REJOUABLES</p>
          <h2>Des conclusions reliées à leurs preuves.</h2>
        </div>
        <span className="big-number">{patterns.length}</span>
      </section>
      {patterns.length ? (
        <section className="pattern-grid">
          {patterns.map((pattern) => (
            <article className="pattern-card" key={pattern.id}>
              <div className="record-topline">
                <span className={`pill ${toneForStatus(pattern.status)}`}>{humanize(pattern.status)}</span>
                <span>{formatPercent(pattern.confidence)} de confiance</span>
              </div>
              <h3>{pattern.summary}</h3>
              <p className="subtle">{humanize(pattern.type)}</p>
              <progress
                aria-label="Confiance"
                className="evidence-bar"
                max={1}
                value={pattern.confidence}
              />
              <RecordMeta
                items={[
                  ["Preuves", `${pattern.evidence_count} événements`],
                  ["Fenêtre", `${formatShortDate(pattern.window_start)} → ${formatShortDate(pattern.window_end)}`],
                  ["Validité", pattern.valid_until ? formatShortDate(pattern.valid_until) : "Ouverte"],
                ]}
              />
              <details>
                <summary>Voir les références</summary>
                <div className="id-list">{pattern.source_event_ids.map((id) => <code key={id}>{id}</code>)}</div>
              </details>
            </article>
          ))}
        </section>
      ) : <EmptyState title="Aucun pattern détecté" detail="Les patterns apparaîtront après plusieurs événements reliés dans une fenêtre temporelle suffisante." />}
    </div>
  );
}

function Memory({
  data,
  disabled,
  mutate,
}: {
  data: MemoryResponse;
  disabled: boolean;
  mutate: (operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [wake, setWake] = useState<WakeResponse | null>(null);
  const [wakeError, setWakeError] = useState<string | null>(null);
  const [waking, setWaking] = useState(false);
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [replacement, setReplacement] = useState("");
  const active = data.memories.filter((item) => item.status === "active");

  const loadWake = async () => {
    setWaking(true);
    setWakeError(null);
    try {
      setWake(await apiGet<WakeResponse>("/memory/wake"));
    } catch (caught) {
      setWakeError(errorMessage(caught));
    } finally {
      setWaking(false);
    }
  };

  const submitMemoryCorrection = (event: FormEvent, memoryId: string) => {
    event.preventDefault();
    void mutate(async () => {
      await apiMutation(`/memory/${memoryId}/corrections`, "POST", {
        replacement: replacement.trim(),
      });
      setCorrecting(null);
      setReplacement("");
      setWake(null);
    });
  };

  return (
    <div className="page-stack">
      <section className="memory-hero">
        <div>
          <p className="eyebrow">PROJECTION ACTIVE OPTMEM</p>
          <h2>{active.length} souvenirs actuellement présentés à l’agent.</h2>
          <p>Les anciennes vérités restent dans l’historique, sans être présentées comme actuelles.</p>
        </div>
        <button className="button accent" disabled={waking} onClick={() => void loadWake()} type="button">
          {waking ? "Construction locale…" : "Prévisualiser wake"}
        </button>
      </section>
      {wakeError && <div className="notice error"><p>{wakeError}</p></div>}
      {wake && (
        <section className="wake-preview">
          <div className="record-topline">
            <span className="pill green">Contexte direct</span>
            <span>{wake.active_memory_count} souvenirs · génération {shortId(wake.projection_generation)}</span>
          </div>
          <pre>{wake.content || "La mémoire active est vide."}</pre>
          {!wake.complete && <p className="subtle">Une page suivante est disponible.</p>}
        </section>
      )}
      <div className="two-columns">
        <section className="panel">
          <PanelHeading title="Mémoire finale" hint={`${active.length} actives · ${data.memories.length} historiques`} />
          {data.memories.length ? (
            <div className="card-list">
              {data.memories.map((memory) => (
                <article className={`record-card memory-record ${memory.status}`} key={memory.id}>
                  <div className="record-topline">
                    <span className={`pill ${toneForStatus(memory.status)}`}>{humanize(memory.status)}</span>
                    <time>{formatDate(memory.created_at)}</time>
                  </div>
                  <h3>{memory.text}</h3>
                  <RecordMeta items={[["Confiance", formatPercent(memory.confidence)], ["Événements", String(memory.event_ids.length)], ["Observations", String(memory.observation_ids.length)]]} />
                  <details><summary>Provenance</summary><div className="id-list"><code>Mémoire {memory.id}</code><code>Candidat {memory.candidate_id}</code>{memory.supersedes_memory_id && <code>Remplace {memory.supersedes_memory_id}</code>}</div></details>
                  {memory.status === "active" && (correcting === memory.id ? (
                    <form className="correction-form" onSubmit={(formEvent) => submitMemoryCorrection(formEvent, memory.id)}>
                      <label>Nouvelle vérité actuelle<input maxLength={4000} onChange={(inputEvent) => setReplacement(inputEvent.target.value)} placeholder="Une phrase explicite et autonome" required value={replacement} /></label>
                      <p>La version actuelle restera dans l’historique et deviendra supersédée.</p>
                      <div className="form-actions"><button className="button accent" disabled={disabled} type="submit">Créer la correction</button><button className="text-button" onClick={() => setCorrecting(null)} type="button">Annuler</button></div>
                    </form>
                  ) : (
                    <button className="text-button action-link" disabled={disabled} onClick={() => { setCorrecting(memory.id); setReplacement(""); }} type="button">Corriger ce souvenir</button>
                  ))}
                </article>
              ))}
            </div>
          ) : <EmptyInline text="Aucun souvenir n’a encore franchi la frontière de promotion." />}
        </section>
        <section className="panel">
          <PanelHeading title="Candidats" hint="Décisions du worker" />
          {data.candidates.length ? data.candidates.map((candidate) => <CandidateRow candidate={candidate} key={candidate.id} />) : <EmptyInline text="Aucun candidat en attente ou décidé." />}
        </section>
      </div>
    </div>
  );
}

function CandidateRow({ candidate }: { candidate: Candidate }) {
  return (
    <article className="candidate-row">
      <div className="record-topline"><span className={`pill ${toneForStatus(candidate.status)}`}>{humanize(candidate.status)}</span><span>score {formatPercent(candidate.score)}</span></div>
      <p>{candidate.text}</p>
      <span className="subtle">{humanize(candidate.source_type)} · {formatDate(candidate.created_at)}</span>
    </article>
  );
}

function Privacy({ data, exclusions, disabled, mutate }: { data: PrivacyResponse; exclusions: Exclusion[]; disabled: boolean; mutate: (operation: () => Promise<unknown>) => Promise<void> }) {
  const [ruleType, setRuleType] = useState("app_bundle_id");
  const [pattern, setPattern] = useState("");
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeConfirmation, setPurgeConfirmation] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!pattern.trim()) return;
    void mutate(async () => {
      await apiMutation("/exclusions", "POST", { rule_type: ruleType, pattern: pattern.trim() });
      setPattern("");
    });
  };

  return (
    <div className="page-stack">
      <section className="privacy-banner">
        <div className="privacy-lock" aria-hidden="true">◉</div>
        <div><p className="eyebrow">FRONTIÈRE LOCALE</p><h2>Les contenus restent sur cette machine.</h2><p>L’API expose les identités et expirations des artefacts, jamais leur chemin système.</p></div>
      </section>
      <div className="two-columns privacy-columns">
        <section className="panel">
          <PanelHeading title="Exclusions avant capture" hint={`${exclusions.filter((item) => item.enabled).length} règles actives`} />
          <form className="exclusion-form" onSubmit={submit}>
            <select aria-label="Type d’exclusion" value={ruleType} onChange={(event) => setRuleType(event.target.value)}>
              <option value="app_bundle_id">Identifiant d’application</option>
              <option value="app_name_contains">Nom d’application</option>
              <option value="window_title_contains">Titre de fenêtre</option>
            </select>
            <input aria-label="Motif d’exclusion" maxLength={255} onChange={(event) => setPattern(event.target.value)} placeholder="com.example.private" value={pattern} />
            <button className="button accent" disabled={disabled || !pattern.trim()} type="submit">Ajouter</button>
          </form>
          <div className="rule-list">
            {exclusions.map((rule) => (
              <div className="rule-row" key={rule.id}>
                <button className={`toggle ${rule.enabled ? "on" : ""}`} disabled={disabled} onClick={() => void mutate(() => apiMutation(`/exclusions/${rule.id}`, "PATCH", { enabled: !rule.enabled }))} type="button" aria-label={rule.enabled ? "Désactiver" : "Activer"}><span /></button>
                <div><strong>{rule.pattern}</strong><span>{humanize(rule.rule_type)}{rule.built_in ? " · règle intégrée" : ""}</span></div>
                {!rule.built_in && <button className="text-button danger-text" disabled={disabled} onClick={() => void mutate(() => apiMutation(`/exclusions/${rule.id}`, "DELETE", {}))} type="button">Supprimer</button>}
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <PanelHeading title="Artefacts temporaires" hint={`${formatBytes(data.raw_artifacts.reduce((sum, item) => sum + item.size_bytes, 0))} présents`} />
          {data.raw_artifacts.length ? data.raw_artifacts.map((artifact) => (
            <div className="artifact-row" key={artifact.observation_id}>
              <span className={`file-icon ${artifact.available ? "available" : "missing"}`}>▧</span>
              <div><strong>{humanize(artifact.source_type)}</strong><span>Expire {relativeDate(artifact.expires_at)}</span></div>
              <div className="right-meta"><strong>{formatBytes(artifact.size_bytes)}</strong><code>{shortId(artifact.observation_id)}</code></div>
            </div>
          )) : <EmptyInline text="Aucun artefact brut temporaire." />}
        </section>
      </div>
      <section className="panel">
        <PanelHeading title="Transformations par le modèle local" hint={`${data.transformations.length} exécutions inspectables`} />
        {data.transformations.length ? (
          <div className="table-scroll"><table><thead><tr><th>État</th><th>Entrées autorisées</th><th>Modèle</th><th>Contrat</th><th>Sensibilité</th><th>Durée</th></tr></thead><tbody>{data.transformations.map((item) => <tr key={item.id}><td><span className={`pill ${toneForStatus(item.status)}`}>{humanize(item.status)}</span></td><td>{item.source_observation_ids.length} observation(s)</td><td>{item.resolved_model ?? item.configured_model}<small>{item.model_digest ? shortId(item.model_digest) : "identité en attente"}</small></td><td>{item.prompt_version}<small>{item.output_schema_version}</small></td><td>{humanize(item.interpretation?.sensitivity ?? "non classé")}</td><td>{item.wall_duration_ms === null ? "—" : `${item.wall_duration_ms} ms`}</td></tr>)}</tbody></table></div>
        ) : <EmptyInline text="Aucune transformation locale enregistrée." />}
      </section>
      <section className="danger-zone">
        <div>
          <p className="eyebrow">ACTION IRRÉVERSIBLE</p>
          <h2>Supprimer immédiatement toutes les données brutes</h2>
          <p>Les fichiers temporaires seront supprimés avant que leurs observations soient tombstonées. Les événements et la provenance structurée restent conservés.</p>
        </div>
        {purgeOpen ? (
          <form
            className="purge-confirmation"
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(async () => {
                await apiMutation("/privacy/raw-artifacts", "DELETE", {
                  confirmation: purgeConfirmation,
                });
                setPurgeOpen(false);
                setPurgeConfirmation("");
              });
            }}
          >
            <label>Écris <code>DELETE RAW ARTIFACTS</code> pour confirmer<input onChange={(event) => setPurgeConfirmation(event.target.value)} value={purgeConfirmation} /></label>
            <div className="form-actions"><button className="button danger" disabled={disabled || purgeConfirmation !== "DELETE RAW ARTIFACTS"} type="submit">Supprimer les fichiers bruts</button><button className="text-button" onClick={() => setPurgeOpen(false)} type="button">Annuler</button></div>
          </form>
        ) : (
          <button className="button danger" disabled={disabled || data.raw_artifacts.length === 0} onClick={() => setPurgeOpen(true)} type="button">Préparer la suppression</button>
        )}
      </section>
    </div>
  );
}

function Agent({ data, disabled, mutate }: { data: AgentResponse; disabled: boolean; mutate: (operation: () => Promise<unknown>) => Promise<void> }) {
  return (
    <div className="page-stack">
      <section className="section-intro"><div><p className="eyebrow">FRONTIÈRE D’ÉCRITURE</p><h2>L’agent propose. CONTX valide. La mémoire décide.</h2></div><span className="big-number">{data.proposals.length}</span></section>
      <div className="two-columns">
        <section className="panel"><PanelHeading title="Propositions reçues" hint="Aucune écriture directe" />{data.proposals.length ? data.proposals.map((proposal) => <article className="record-card" key={proposal.id}><div className="record-topline"><span className={`pill ${toneForStatus(proposal.status)}`}>{humanize(proposal.status)}</span><time>{formatDate(proposal.created_at)}</time></div><h3>{proposal.text}</h3><p className="subtle">{proposal.agent_id} · {humanize(proposal.agent_role)}</p>{proposal.reason && <p className="reason">{humanize(proposal.reason)}</p>}<details><summary>Référence</summary><div className="id-list"><code>{proposal.reference_type ?? "aucune"}</code><code>{proposal.reference_id ?? "aucun identifiant"}</code></div></details>{proposal.status === "pending" && <div className="proposal-actions"><button className="button accent" disabled={disabled} onClick={() => { if (window.confirm(`Adopter cette proposition dans la mémoire finale ?\n\n${proposal.text}`)) void mutate(() => apiMutation(`/agent/proposals/${proposal.id}/adopt`, "POST", {})); }} type="button">Valider localement et adopter</button><button className="button secondary" disabled={disabled} onClick={() => { if (window.confirm("Rejeter définitivement cette proposition ?")) void mutate(() => apiMutation(`/agent/proposals/${proposal.id}/reject`, "POST", { reason: "user_rejected" })); }} type="button">Rejeter</button></div>}</article>) : <EmptyInline text="Aucune proposition d’agent." />}</section>
        <section className="panel"><PanelHeading title="Validations locales" hint="Provenance sans contenu privé" />{data.validations.length ? data.validations.map((validation) => <article className="candidate-row" key={validation.proposal_id}><div className="record-topline"><span className={`pill ${toneForStatus(validation.decision)}`}>{humanize(validation.decision)}</span><span>{formatPercent(validation.confidence)}</span></div><p>{humanize(validation.reason_code)}</p><RecordMeta items={[["Modèle", validation.model], ["Mémoire comparée", String(validation.active_memory_count)], ["Durée", `${validation.wall_duration_ms} ms`]]} /></article>) : <EmptyInline text="Aucune proposition n’a encore été évaluée par le modèle local." />}</section>
      </div>
    </div>
  );
}

function Settings({ settings, exclusions }: { settings: SettingsResponse; exclusions: Exclusion[] }) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState(false);
  const sections = useMemo(() => [
    ["Collecte", settings.collection], ["Modèle local", settings.model], ["Événements", settings.events], ["Mémoire", settings.memory], ["Traitement", settings.processing], ["API locale", settings.api],
  ] as const, [settings]);

  if (deleted) {
    return (
      <EmptyState
        title="Toutes les données CONTX ont été supprimées"
        detail="Le service local est désormais retiré. Ferme cette page ; une prochaine initialisation repartira d’un état vide."
      />
    );
  }

  return (
    <div className="page-stack">
      <section className="notice info"><div><strong>Configuration en lecture seule pour la v0.6.</strong><p>Les modifications persistantes restent contrôlées par le fichier privé de configuration ; les contrôles quotidiens sont disponibles directement dans l’interface.</p></div><span className="pill neutral">schéma {settings.config_version}</span></section>
      <section className="settings-grid">{sections.map(([title, values]) => <article className="settings-card" key={title}><h3>{title}</h3><dl>{Object.entries(values).map(([key, value]) => <div key={key}><dt>{humanize(key)}</dt><dd>{formatSetting(value)}</dd></div>)}</dl></article>)}</section>
      <section className="panel"><PanelHeading title="Résumé des exclusions" hint={`${exclusions.length} règles persistées`} /><div className="tag-row">{exclusions.filter((item) => item.enabled).slice(0, 12).map((item) => <span key={item.id}>{item.pattern}</span>)}</div></section>
      {deleteError && <div className="notice error"><p>{deleteError}</p></div>}
      <section className="danger-zone">
        <div><p className="eyebrow">SUPPRESSION COMPLÈTE</p><h2>Effacer configuration, mémoire, historique, caches et journaux</h2><p>Le collecteur doit être arrêté. Cette action ferme SQLite, vérifie les trois racines CONTX et ne touche à aucun fichier en dehors de ces emplacements.</p></div>
        {deleteOpen ? (
          <form className="purge-confirmation" onSubmit={(event) => { event.preventDefault(); setDeleting(true); setDeleteError(null); void apiMutation<{ service_state: string }>("/system/data", "DELETE", { confirmation: deleteConfirmation }).then(() => setDeleted(true)).catch((caught) => setDeleteError(errorMessage(caught))).finally(() => setDeleting(false)); }}>
            <label>Écris <code>DELETE ALL CONTX DATA</code> pour confirmer<input onChange={(event) => setDeleteConfirmation(event.target.value)} value={deleteConfirmation} /></label>
            <div className="form-actions"><button className="button danger" disabled={deleting || deleteConfirmation !== "DELETE ALL CONTX DATA"} type="submit">Supprimer définitivement CONTX</button><button className="text-button" disabled={deleting} onClick={() => setDeleteOpen(false)} type="button">Annuler</button></div>
          </form>
        ) : <button className="button danger" onClick={() => setDeleteOpen(true)} type="button">Préparer la suppression complète</button>}
      </section>
    </div>
  );
}

function Metric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) {
  return <article className={`metric-card ${tone}`}><div className="metric-accent" /><span>{label}</span><strong>{value}</strong><p>{detail}</p></article>;
}

function PanelHeading({ title, hint }: { title: string; hint: string }) {
  return <div className="panel-heading"><h2>{title}</h2><span>{hint}</span></div>;
}

function RecordMeta({ items }: { items: Array<[string, string]> }) {
  return <dl className="record-meta">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function LoadingState() {
  return <div className="loading-state" aria-live="polite"><span className="loader" /><strong>Lecture de l’état local…</strong><p>CONTX rassemble les informations sans envoyer de contenu hors de cette machine.</p></div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <section className="empty-state"><span aria-hidden="true">◇</span><h2>{title}</h2><p>{detail}</p></section>;
}

function EmptyInline({ text }: { text: string }) {
  return <p className="empty-inline">{text}</p>;
}

type CollectionPresentation = {
  label: string;
  headline: string;
  description: string;
  detail: string;
  tone: string;
  action: "pause" | "resume" | null;
  actionLabel: string;
};

function collectionPresentation(
  collection: StatusResponse["collection"],
): CollectionPresentation {
  if (collection.daemon_running && collection.paused) {
    return {
      label: "En pause",
      headline: "La collecte attend.",
      description:
        "Aucune nouvelle observation n’est collectée. La mémoire et l’historique restent consultables.",
      detail: "Daemon actif · collecte suspendue",
      tone: "amber",
      action: "resume",
      actionLabel: "Reprendre",
    };
  }

  if (collection.daemon_running) {
    return {
      label: "Active",
      headline: "CONTX observe localement.",
      description:
        "La politique d’exclusion est appliquée avant chaque capture autorisée.",
      detail: "Daemon actif",
      tone: "green",
      action: "pause",
      actionLabel: "Mettre en pause",
    };
  }

  if (collection.background_enabled) {
    return {
      label: "Arrêtée",
      headline: "Le collecteur n’est pas démarré.",
      description:
        "La collecte de fond est configurée, mais aucun daemon CONTX actif n’a été détecté.",
      detail: "Daemon arrêté",
      tone: "red",
      action: null,
      actionLabel: "Collecteur arrêté",
    };
  }

  return {
    label: "Désactivée",
    headline: "La collecte de fond est désactivée.",
    description:
      "Aucune nouvelle observation n’est collectée. La mémoire locale existante reste consultable.",
    detail: "Désactivée dans la configuration",
    tone: "neutral",
    action: null,
    actionLabel: "Collecte désactivée",
  };
}

function viewFromHash(): View {
  const candidate = window.location.hash.slice(1) as View;
  return views.some((view) => view.id === candidate) ? candidate : "overview";
}

function humanize(value: string): string {
  const normalized = value.replaceAll("_", " ").trim();
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : "—";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "short" }).format(new Date(value));
}

function relativeDate(value: string): string {
  const milliseconds = new Date(value).getTime() - Date.now();
  const hours = Math.round(milliseconds / 3_600_000);
  if (Math.abs(hours) < 24) return hours >= 0 ? `dans ${hours} h` : `depuis ${Math.abs(hours)} h`;
  const days = Math.round(hours / 24);
  return days >= 0 ? `dans ${days} j` : `depuis ${Math.abs(days)} j`;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} o`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} Ko`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} Mo`;
  return `${(value / 1024 ** 3).toFixed(1)} Go`;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)} %`;
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function toneForStatus(status: string): string {
  if (["active", "available", "succeeded", "stored", "accepted", "adopted"].includes(status)) return "green";
  if (["failed", "rejected", "abandoned", "forbidden", "unavailable"].includes(status)) return "red";
  if (["pending", "running", "deferred", "permission_required", "paused"].includes(status)) return "amber";
  if (["superseded", "expired", "disabled"].includes(status)) return "neutral";
  return "violet";
}

function formatSetting(value: unknown): string {
  if (typeof value === "boolean") return value ? "Activé" : "Désactivé";
  if (value === null || value === undefined) return "—";
  return String(value);
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "Erreur locale inconnue";
}
