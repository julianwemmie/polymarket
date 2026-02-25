import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type {
  EntityDetail,
  EntityDiscoveryMarket,
  EntityProgress,
  EntityWalletScore,
} from "../types";

function formatPct(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

function formatDelta(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function formatVolume(volume: number): string {
  if (Math.abs(volume) >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (Math.abs(volume) >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

function statusClass(status: string): string {
  switch (status) {
    case "done":
      return "bg-green-500/20 text-green-300 border border-green-500/30";
    case "error":
      return "bg-red-500/20 text-red-300 border border-red-500/30";
    case "ingesting":
    case "scoring":
      return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
    case "searching":
      return "bg-blue-500/20 text-blue-300 border border-blue-500/30";
    default:
      return "bg-gray-700 text-gray-300 border border-gray-600";
  }
}

export default function EntityPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [entity, setEntity] = useState<EntityDetail | null>(null);
  const [wallets, setWallets] = useState<EntityWalletScore[]>([]);
  const [discovered, setDiscovered] = useState<EntityDiscoveryMarket[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);

  const [progress, setProgress] = useState<EntityProgress | null>(null);
  const [expandedWallets, setExpandedWallets] = useState<Record<string, boolean>>({});

  const eventSourceRef = useRef<EventSource | null>(null);

  const closeProgressStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const loadEntity = useCallback(async () => {
    if (!id) return;
    const parsedId = Number(id);
    const detail = await api.getEntity(parsedId);
    setEntity(detail);
    if (detail.markets.length > 0 && discovered.length === 0) {
      const fromDb: EntityDiscoveryMarket[] = detail.markets.map((m) => ({
        condition_id: m.condition_id,
        question: m.question,
        slug: m.slug,
        volume: m.volume,
        resolved: m.resolved,
        winning_outcome: m.winning_outcome,
        match_terms: m.match_term ? [m.match_term] : [],
        included: m.included,
      }));
      setDiscovered(fromDb);
    }
  }, [discovered.length, id]);

  const loadWallets = useCallback(async () => {
    if (!id) return;
    const parsedId = Number(id);
    const rows = await api.getEntityWallets(parsedId, {
      min_entity_markets: 2,
      limit: 200,
      sort: "delta",
    });
    setWallets(rows);
  }, [id]);

  const startProgressStream = useCallback(() => {
    if (!id) return;
    closeProgressStream();

    const es = new EventSource(api.entityProgressUrl(id));
    eventSourceRef.current = es;

    es.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data) as EntityProgress;
        setProgress(data);

        if (data.done) {
          closeProgressStream();
          setAnalyzing(false);
          await loadEntity();
          await loadWallets();
        }
      } catch {
        // ignore malformed updates
      }
    };

    es.onerror = () => {
      closeProgressStream();
    };
  }, [closeProgressStream, id, loadEntity, loadWallets]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);

    Promise.all([loadEntity(), loadWallets()])
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    return () => {
      closeProgressStream();
    };
  }, [id, loadEntity, loadWallets, closeProgressStream]);

  useEffect(() => {
    if (!entity) return;
    if (entity.status === "ingesting" || entity.status === "scoring") {
      startProgressStream();
    }
  }, [entity, startProgressStream]);

  const runDiscovery = async () => {
    if (!id) return;
    setDiscovering(true);
    setError(null);
    try {
      const res = await api.discoverEntityMarkets(id);
      setDiscovered(res.markets);
      await loadEntity();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Discovery failed";
      setError(message);
    } finally {
      setDiscovering(false);
    }
  };

  const runAnalysis = async () => {
    if (!id || discovered.length === 0) return;
    setAnalyzing(true);
    setError(null);

    try {
      await api.analyzeEntity(id, discovered);
      setProgress({
        running: true,
        done: false,
        stage: "ingesting",
        current: 0,
        total: discovered.filter((m) => m.included).length,
        current_market: "",
        wallet_current: 0,
        wallet_total: 0,
        current_wallet: "",
        resolved_markets: 0,
        error: null,
      });
      startProgressStream();
      await loadEntity();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Analysis failed";
      setError(message);
      setAnalyzing(false);
    }
  };

  const toggleIncluded = (conditionId: string) => {
    setDiscovered((prev) =>
      prev.map((m) =>
        m.condition_id === conditionId ? { ...m, included: !m.included } : m,
      ),
    );
  };

  const includeAll = (value: boolean) => {
    setDiscovered((prev) => prev.map((m) => ({ ...m, included: value })));
  };

  const includedCount = useMemo(
    () => discovered.filter((m) => m.included).length,
    [discovered],
  );

  const progressPct = useMemo(() => {
    if (!progress) return 0;
    if (progress.stage === "scoring") {
      if (!progress.wallet_total) return 0;
      return Math.round((progress.wallet_current / progress.wallet_total) * 100);
    }
    if (!progress.total) return 0;
    return Math.round((progress.current / progress.total) * 100);
  }, [progress]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-sm text-gray-500">
        Loading investigation...
      </div>
    );
  }

  if (error || !entity) {
    return (
      <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-xl mx-auto mt-10">
        <p className="text-red-300 font-medium">Failed to load investigation</p>
        <p className="text-sm text-red-200/80 mt-1">{error || "Investigation not found"}</p>
        <button
          type="button"
          onClick={() => navigate("/entities")}
          className="mt-4 text-sm text-gray-300 hover:text-white"
        >
          Back to investigations
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Link to="/entities" className="hover:text-gray-300">Investigations</Link>
        <span>/</span>
        <span className="text-gray-300">{entity.name}</span>
      </div>

      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-white">{entity.name}</h1>
            <p className="text-xs text-gray-500 mt-1">Search terms: {entity.search_terms.join(", ")}</p>
          </div>
          <span className={`text-xs px-2 py-0.5 rounded ${statusClass(entity.status)}`}>
            {entity.status}
          </span>
        </div>

        {entity.error_message && (
          <div className="mt-3 bg-red-900/20 border border-red-800 rounded p-2 text-sm text-red-300">
            {entity.error_message}
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-4 text-xs">
          <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
            <p className="text-gray-500">Discovered</p>
            <p className="text-gray-100 font-mono">{entity.discovered_market_count}</p>
          </div>
          <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
            <p className="text-gray-500">Included</p>
            <p className="text-gray-100 font-mono">{entity.included_market_count}</p>
          </div>
          <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
            <p className="text-gray-500">Wallets Scored</p>
            <p className="text-gray-100 font-mono">{entity.scored_wallet_count}</p>
          </div>
          <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
            <p className="text-gray-500">Flagged</p>
            <p className="text-red-300 font-mono">{entity.flagged_wallet_count}</p>
          </div>
        </div>
      </section>

      {(entity.status === "ingesting" || entity.status === "scoring" || analyzing) && progress && (
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="flex items-center justify-between text-sm mb-2">
            <p className="text-gray-200 font-medium">
              {progress.stage === "scoring" ? "Scoring wallets" : "Ingesting markets"}
            </p>
            <p className="text-gray-500">{progressPct}%</p>
          </div>

          <div className="h-2 bg-gray-800 rounded overflow-hidden">
            <div
              className="h-full bg-red-500 transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          <div className="mt-3 text-xs text-gray-400">
            {progress.stage === "scoring" ? (
              <p>
                Wallet {progress.wallet_current} / {progress.wallet_total}
                {progress.current_wallet ? ` (${progress.current_wallet.slice(0, 10)}...)` : ""}
                {progress.wallet_stage ? ` · ${progress.wallet_stage}` : ""}
              </p>
            ) : (
              <p>
                Market {progress.current} / {progress.total}
                {progress.current_market ? ` (${progress.current_market})` : ""}
              </p>
            )}
            <p className="mt-1">
              {progress.resolved_markets} / {entity.included_market_count} markets resolved. Unresolved markets are excluded from scoring.
            </p>
            {progress.error && <p className="mt-1 text-red-300">{progress.error}</p>}
          </div>
        </section>
      )}

      {(entity.status === "draft" || entity.status === "searching") && (
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">Draft: Discover and Select Markets</h2>
              <p className="text-xs text-gray-500 mt-1">
                Discover candidate markets, exclude irrelevant ones, then run analysis.
              </p>
            </div>
            <button
              type="button"
              onClick={runDiscovery}
              disabled={discovering}
              className="px-3 py-2 rounded text-sm font-medium bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-400"
            >
              {discovering ? "Discovering..." : "Discover Markets"}
            </button>
          </div>

          {discovered.length > 0 && (
            <>
              <div className="flex items-center justify-between mt-4 mb-2 text-xs">
                <p className="text-gray-400">
                  {includedCount} / {discovered.length} markets selected
                </p>
                <div className="flex items-center gap-2">
                  <button type="button" onClick={() => includeAll(true)} className="text-gray-400 hover:text-white">Select all</button>
                  <button type="button" onClick={() => includeAll(false)} className="text-gray-400 hover:text-white">Clear all</button>
                </div>
              </div>

              <div className="border border-gray-800 rounded overflow-hidden max-h-[420px] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-gray-950">
                    <tr className="border-b border-gray-800">
                      <th className="text-left px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Use</th>
                      <th className="text-left px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Market</th>
                      <th className="text-left px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Match</th>
                      <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Volume</th>
                      <th className="text-left px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Resolution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {discovered.map((m) => (
                      <tr key={m.condition_id} className="border-b border-gray-800/60 hover:bg-gray-800/30">
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={m.included}
                            onChange={() => toggleIncluded(m.condition_id)}
                            className="accent-red-500"
                          />
                        </td>
                        <td className="px-3 py-2 text-gray-200 max-w-xl">
                          <p className="leading-snug">{m.question}</p>
                          <p className="text-xs text-gray-600 font-mono mt-0.5">{m.condition_id.slice(0, 14)}...</p>
                        </td>
                        <td className="px-3 py-2 text-xs text-gray-400">{m.match_terms.join(", ") || "--"}</td>
                        <td className="px-3 py-2 text-right text-xs text-gray-300 font-mono">{formatVolume(m.volume)}</td>
                        <td className="px-3 py-2 text-xs">
                          {m.resolved ? (
                            <span className="text-green-300 bg-green-500/20 border border-green-500/30 rounded px-2 py-0.5">
                              {m.winning_outcome || "Resolved"}
                            </span>
                          ) : (
                            <span className="text-gray-400 bg-gray-800 border border-gray-700 rounded px-2 py-0.5">
                              Unresolved
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-3 flex justify-end">
                <button
                  type="button"
                  disabled={includedCount === 0 || analyzing}
                  onClick={runAnalysis}
                  className="px-4 py-2 rounded text-sm font-medium bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-400"
                >
                  {analyzing ? "Starting..." : "Run Analysis"}
                </button>
              </div>
            </>
          )}
        </section>
      )}

      {entity.status === "done" && (
        <section className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800">
            <h2 className="text-lg font-semibold text-white">Ranked Wallet Results</h2>
            <p className="text-xs text-gray-500 mt-1">
              Delta = entity win rate minus overall wallet win rate.
            </p>
          </div>

          {wallets.length === 0 ? (
            <div className="p-6 text-sm text-gray-500">No qualifying wallets were found in at least 2 entity markets.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800">
                    <th className="text-left px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Wallet</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Entity WR</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Overall WR</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Delta</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Entity Mkts</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Resolved</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Profit</th>
                    <th className="text-right px-3 py-2 text-xs text-gray-500 uppercase tracking-wider">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {wallets.map((w) => {
                    const open = expandedWallets[w.wallet_address] || false;
                    const deltaPositive = (w.win_rate_delta || 0) > 0;
                    return (
                      <Fragment key={w.wallet_address}>
                        <tr
                          className={`border-b border-gray-800/70 cursor-pointer ${w.is_flagged ? "bg-red-950/15" : "hover:bg-gray-800/30"}`}
                          onClick={() =>
                            setExpandedWallets((prev) => ({
                              ...prev,
                              [w.wallet_address]: !prev[w.wallet_address],
                            }))
                          }
                        >
                          <td className="px-3 py-2">
                            <Link
                              to={`/wallets/${w.wallet_address}`}
                              className="text-blue-400 hover:text-blue-300 font-mono text-xs"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {w.wallet_address.slice(0, 8)}...{w.wallet_address.slice(-4)}
                            </Link>
                            {w.is_flagged && (
                              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/30">
                                Flagged
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-200">{formatPct(w.entity_win_rate)}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-400">{formatPct(w.overall_win_rate)}</td>
                          <td className={`px-3 py-2 text-right font-mono text-xs ${deltaPositive ? "text-green-300" : "text-red-300"}`}>
                            {formatDelta(w.win_rate_delta)}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-300">{w.entity_markets_traded}</td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-300">{w.entity_resolved_markets}</td>
                          <td className={`px-3 py-2 text-right font-mono text-xs ${w.entity_profit >= 0 ? "text-green-300" : "text-red-300"}`}>
                            {w.entity_profit >= 0 ? "+" : ""}
                            {formatVolume(w.entity_profit)}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-xs text-gray-300">{formatPct(w.suspicion_score)}</td>
                        </tr>
                        {open && w.market_breakdown && (
                          <tr className="border-b border-gray-800/70 bg-gray-950/50">
                            <td colSpan={8} className="px-3 py-2">
                              <div className="space-y-1 text-xs">
                                {w.market_breakdown.map((m) => (
                                  <div key={`${w.wallet_address}-${m.condition_id}`} className="flex items-center justify-between gap-3 border border-gray-800 rounded px-2 py-1.5">
                                    <div className="text-gray-300 truncate">{m.question}</div>
                                    <div className="shrink-0 flex items-center gap-3 font-mono">
                                      <span className="text-gray-500">{m.trade_count} trades</span>
                                      <span className="text-gray-400">{m.resolved ? "resolved" : "unresolved"}</span>
                                      <span className={m.won === true ? "text-green-300" : m.won === false ? "text-red-300" : "text-gray-500"}>
                                        {m.won === true ? "W" : m.won === false ? "L" : "--"}
                                      </span>
                                      <span className={m.profit >= 0 ? "text-green-300" : "text-red-300"}>
                                        {m.profit >= 0 ? "+" : ""}
                                        {formatVolume(m.profit)}
                                      </span>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
