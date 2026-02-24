import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { LeaderboardEntry } from "../types";
import SuspicionBadge from "../components/SuspicionBadge";
import MarketCard from "../components/MarketCard";

const API_BASE = "http://localhost:8000";

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

interface IngestProgress {
  running: boolean;
  current: number;
  total: number;
  current_market: string;
  done_count: number;
  error: string | null;
  done?: boolean;
}

export default function HomePage() {
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedEntity, setExpandedEntity] = useState<string | null>(null);
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null);
  const [ingesting, setIngesting] = useState(false);

  const loadData = useCallback(() => {
    setLoading(true);
    api
      .getLeaderboard()
      .then(setLeaderboard)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const startIngest = async () => {
    setIngesting(true);
    setIngestProgress({ running: true, current: 0, total: 0, current_market: "Starting...", done_count: 0, error: null });
    await fetch(`${API_BASE}/api/ingest?limit=15`, { method: "POST" });

    const evtSource = new EventSource(`${API_BASE}/api/ingest/progress`);
    evtSource.onmessage = (event) => {
      const data: IngestProgress = JSON.parse(event.data);
      setIngestProgress(data);
      if (data.done || (!data.running && data.total > 0)) {
        evtSource.close();
        setIngesting(false);
        loadData();
      }
    };
    evtSource.onerror = () => {
      evtSource.close();
      setIngesting(false);
      loadData();
    };
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Analyzing on-chain data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load data</p>
          <p className="text-gray-500 text-sm mt-1">{error}</p>
        </div>
      </div>
    );
  }

  if (leaderboard.length === 0 && !ingesting) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 max-w-lg text-center">
          <div className="text-4xl mb-4">🔍</div>
          <p className="text-gray-300 font-semibold text-lg">No data yet</p>
          <p className="text-gray-500 text-sm mt-2 mb-6">
            Click below to fetch the top Polymarket markets and analyze them for suspicious trading activity.
          </p>
          <button
            onClick={startIngest}
            className="px-6 py-3 bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg transition-colors"
          >
            Start Analysis
          </button>
        </div>
      </div>
    );
  }

  if (ingesting && ingestProgress) {
    const pct = ingestProgress.total > 0 ? Math.round((ingestProgress.current / ingestProgress.total) * 100) : 0;
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 max-w-lg w-full">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-5 h-5 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-white font-semibold">Analyzing Markets...</p>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-gray-800 rounded-full h-3 mb-3">
            <div
              className="bg-red-500 h-3 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>

          <div className="flex justify-between text-sm mb-4">
            <span className="text-gray-400">
              {ingestProgress.current} / {ingestProgress.total} markets
            </span>
            <span className="text-gray-500">{pct}%</span>
          </div>

          <div className="bg-gray-950 rounded p-3 border border-gray-800">
            <p className="text-gray-500 text-xs uppercase tracking-wider mb-1">Currently Processing</p>
            <p className="text-gray-300 text-sm truncate">{ingestProgress.current_market}</p>
          </div>

          {ingestProgress.done_count > 0 && (
            <p className="text-gray-600 text-xs mt-3">
              {ingestProgress.done_count} markets completed
            </p>
          )}

          {ingestProgress.error && (
            <p className="text-red-400 text-sm mt-3">{ingestProgress.error}</p>
          )}
        </div>
      </div>
    );
  }

  const totalEntities = leaderboard.length;
  const totalSuspiciousWallets = leaderboard.reduce(
    (s, e) => s + e.total_suspicious_wallets,
    0
  );
  const totalMarkets = leaderboard.reduce(
    (s, e) => s + e.total_markets_affected,
    0
  );

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Entity Leaderboard</h1>
        <p className="text-gray-500 text-sm mt-1">
          Entities ranked by suspicious trading activity across Polymarket
        </p>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p className="text-gray-500 text-xs uppercase tracking-wider">
            Entities Tracked
          </p>
          <p className="text-2xl font-bold text-white mt-1">{totalEntities}</p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p className="text-gray-500 text-xs uppercase tracking-wider">
            Suspicious Wallets
          </p>
          <p className="text-2xl font-bold text-red-400 mt-1">
            {totalSuspiciousWallets}
          </p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p className="text-gray-500 text-xs uppercase tracking-wider">
            Markets Analyzed
          </p>
          <p className="text-2xl font-bold text-white mt-1">{totalMarkets}</p>
        </div>
      </div>

      {/* Leaderboard Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Rank
                </th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Entity
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Suspicious Wallets
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Markets Affected
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Avg Suspicion
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Suspicious Volume
                </th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.map((entry, i) => {
                const isExpanded = expandedEntity === entry.entity;
                return (
                  <tr key={entry.entity} className="group">
                    <td colSpan={6} className="p-0">
                      {/* Main row */}
                      <button
                        onClick={() =>
                          setExpandedEntity(isExpanded ? null : entry.entity)
                        }
                        className="w-full flex items-center border-b border-gray-800/50 hover:bg-gray-800/40 transition-colors cursor-pointer"
                      >
                        <span className="text-gray-600 font-mono text-xs px-4 py-3 w-16 text-left">
                          #{i + 1}
                        </span>
                        <span className="text-white font-medium px-4 py-3 flex-1 text-left">
                          {entry.entity}
                          <span className="text-gray-600 ml-2 text-xs">
                            {isExpanded ? "[-]" : "[+]"}
                          </span>
                        </span>
                        <span className="text-red-400 font-mono px-4 py-3 w-36 text-right">
                          {entry.total_suspicious_wallets}
                        </span>
                        <span className="text-gray-300 font-mono px-4 py-3 w-36 text-right">
                          {entry.total_markets_affected}
                        </span>
                        <span className="px-4 py-3 w-40 text-right">
                          <SuspicionBadge
                            score={entry.avg_suspicion_score}
                            size="sm"
                          />
                        </span>
                        <span className="text-gray-200 font-mono px-4 py-3 w-40 text-right">
                          {formatVolume(entry.total_suspicious_volume)}
                        </span>
                      </button>

                      {/* Expanded markets */}
                      {isExpanded && entry.top_markets.length > 0 && (
                        <div className="bg-gray-950/50 border-b border-gray-800 px-6 py-4">
                          <div className="flex items-center justify-between mb-3">
                            <p className="text-gray-500 text-xs uppercase tracking-wider">
                              Top Markets for {entry.entity}
                            </p>
                            <Link
                              to={`/markets?entity=${encodeURIComponent(entry.entity)}`}
                              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                            >
                              View all markets
                            </Link>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                            {entry.top_markets.map((market) => (
                              <MarketCard key={market.id} market={market} />
                            ))}
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
