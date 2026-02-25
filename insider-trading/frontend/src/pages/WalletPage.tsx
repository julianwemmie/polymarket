import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { WalletDetail, Trade, SuspicionFlag, WalletFullHistory, FullMarketRecord } from "../types";

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

// ── Our-dataset market grouping (from DB) ────────────────────────

interface MarketGroup {
  market_id: string;
  question: string;
  resolution: string;
  trades: Trade[];
  net_profit: number;
  total_volume: number;
  won: boolean;
  flag: SuspicionFlag | null;
}

function groupByMarket(trades: Trade[], flags: SuspicionFlag[]): MarketGroup[] {
  const map = new Map<string, Trade[]>();
  for (const t of trades) {
    const existing = map.get(t.market_id) || [];
    existing.push(t);
    map.set(t.market_id, existing);
  }

  const flagMap = new Map<string, SuspicionFlag>();
  for (const f of flags) {
    const existing = flagMap.get(f.market_id);
    if (!existing || f.score > existing.score) flagMap.set(f.market_id, f);
  }

  const groups: MarketGroup[] = [];
  for (const [market_id, marketTrades] of map.entries()) {
    const net_profit = marketTrades.reduce((sum, t) => sum + (t.profit || 0), 0);
    const total_volume = marketTrades.reduce((sum, t) => sum + t.amount, 0);
    groups.push({
      market_id,
      question: marketTrades[0]?.market_question || market_id,
      resolution: marketTrades[0]?.market_resolution || "Unknown",
      trades: marketTrades.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
      net_profit,
      total_volume,
      won: net_profit > 0,
      flag: flagMap.get(market_id) || null,
    });
  }

  groups.sort((a, b) => {
    if (a.flag && !b.flag) return -1;
    if (!a.flag && b.flag) return 1;
    return b.net_profit - a.net_profit;
  });
  return groups;
}

// ── Flagged market card (from our DB) ────────────────────────────

function FlaggedMarketCard({ group }: { group: MarketGroup }) {
  const [showTrades, setShowTrades] = useState(false);

  return (
    <div className={`border rounded-lg overflow-hidden ${
      group.flag ? "border-red-800/60 bg-red-950/10" : "border-gray-800 bg-gray-900"
    }`}>
      <div className="px-4 py-3">
        <div className="flex items-start justify-between gap-3 mb-2">
          <Link to={`/markets/${group.market_id}`} className="text-sm text-blue-400 hover:text-blue-300 transition-colors leading-snug">
            {group.question}
          </Link>
          <span className={`shrink-0 text-xs font-bold px-2 py-0.5 rounded ${
            group.won ? "bg-green-900/40 text-green-400 border border-green-700/40" : "bg-red-900/40 text-red-400 border border-red-700/40"
          }`}>
            {group.won ? "WON" : "LOST"}
          </span>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <span className="text-gray-500">
            Resolved <span className={group.resolution === "Yes" ? "text-green-400 font-semibold" : group.resolution === "No" ? "text-red-400 font-semibold" : "text-gray-400"}>{group.resolution}</span>
          </span>
          <span className="text-gray-600">|</span>
          <span className={`font-mono font-semibold ${group.net_profit >= 0 ? "text-green-400" : "text-red-400"}`}>
            {group.net_profit >= 0 ? "+" : ""}{formatVolume(group.net_profit)} profit
          </span>
          <span className="text-gray-600">|</span>
          <button onClick={() => setShowTrades(!showTrades)} className="text-gray-400 hover:text-gray-200 transition-colors">
            {group.trades.length} trade{group.trades.length !== 1 ? "s" : ""} {showTrades ? "▴" : "▾"}
          </button>
        </div>

        {group.flag && group.flag.reasons.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {group.flag.reasons.map((r, i) => (
              <span key={i} className="text-xs px-2 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/20">{r}</span>
            ))}
          </div>
        )}
      </div>

      {showTrades && (
        <div className="border-t border-gray-800 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800/50">
                <th className="text-left text-gray-600 font-medium px-4 py-2">Time</th>
                <th className="text-left text-gray-600 font-medium px-4 py-2">Side</th>
                <th className="text-left text-gray-600 font-medium px-4 py-2">Outcome</th>
                <th className="text-right text-gray-600 font-medium px-4 py-2">Amount</th>
                <th className="text-right text-gray-600 font-medium px-4 py-2">Price</th>
                <th className="text-right text-gray-600 font-medium px-4 py-2">Profit</th>
              </tr>
            </thead>
            <tbody>
              {group.trades.map((t) => (
                <tr key={t.id} className="border-b border-gray-800/30 hover:bg-gray-800/20">
                  <td className="px-4 py-1.5 text-gray-500 whitespace-nowrap">
                    {new Date(t.timestamp).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td className="px-4 py-1.5"><span className={t.side === "BUY" ? "text-green-400" : "text-red-400"}>{t.side}</span></td>
                  <td className="px-4 py-1.5 text-gray-400">{t.outcome}</td>
                  <td className="px-4 py-1.5 text-right text-gray-300 font-mono">{formatVolume(t.amount)}</td>
                  <td className="px-4 py-1.5 text-right text-gray-500 font-mono">{(t.price * 100).toFixed(1)}c</td>
                  <td className="px-4 py-1.5 text-right font-mono">
                    {t.profit !== null ? (
                      <span className={t.profit >= 0 ? "text-green-400" : "text-red-400"}>
                        {t.profit >= 0 ? "+" : ""}{formatVolume(t.profit)}
                      </span>
                    ) : <span className="text-gray-700">--</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Full history market row ──────────────────────────────────────

function FullHistoryRow({ m }: { m: FullMarketRecord }) {
  return (
    <tr className={`border-b border-gray-800/50 ${
      m.won === true ? "bg-green-950/5" : m.won === false ? "bg-red-950/5" : ""
    }`}>
      <td className="px-4 py-2 text-sm text-gray-300 max-w-md truncate">{m.title}</td>
      <td className="px-4 py-2 text-center">
        {m.resolved ? (
          m.won === true ? (
            <span className="text-xs font-bold text-green-400 bg-green-900/30 px-2 py-0.5 rounded">W</span>
          ) : (
            <span className="text-xs font-bold text-red-400 bg-red-900/30 px-2 py-0.5 rounded">L</span>
          )
        ) : (
          <span className="text-xs text-gray-600">--</span>
        )}
      </td>
      <td className="px-4 py-2 text-xs text-gray-400">{m.outcome_bought}</td>
      <td className="px-4 py-2 text-right text-xs text-gray-400 font-mono">{m.trades}</td>
      <td className="px-4 py-2 text-right text-xs text-gray-300 font-mono">{formatVolume(m.total_cost)}</td>
    </tr>
  );
}

// ── Main wallet page ─────────────────────────────────────────────

export default function WalletPage() {
  const { address } = useParams<{ address: string }>();
  const [wallet, setWallet] = useState<WalletDetail | null>(null);
  const [fullHistory, setFullHistory] = useState<WalletFullHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!address) return;
    setLoading(true);
    setHistoryLoading(true);

    api.getWallet(address)
      .then(setWallet)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    api.getWalletFullHistory(address)
      .then(setFullHistory)
      .catch(() => {}) // Non-critical
      .finally(() => setHistoryLoading(false));
  }, [address]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Loading wallet...</p>
        </div>
      </div>
    );
  }

  if (error || !wallet) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load wallet</p>
          <p className="text-gray-500 text-sm mt-1">{error || "Wallet not found"}</p>
          <Link to="/entities" className="inline-block mt-4 text-sm text-gray-400 hover:text-white transition-colors">Back to investigations</Link>
        </div>
      </div>
    );
  }

  const marketGroups = groupByMarket(wallet.trades, wallet.suspicion_flags);
  const isFlagged = wallet.suspicion_score >= 0.6;

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-4">
        <Link to="/entities" className="hover:text-gray-300 transition-colors">Investigations</Link>
        <span>/</span>
        <span className="text-gray-400 font-mono text-xs">{address?.slice(0, 10)}...</span>
      </div>

      {/* ── Real Win Rate (Full History) ─────────────────────────── */}
      <div className={`rounded-lg p-6 mb-4 border ${
        isFlagged ? "bg-red-950/20 border-red-800/50" : "bg-gray-900 border-gray-800"
      }`}>
        <div className="flex items-center gap-3 mb-4">
          <p className="text-white font-mono text-sm break-all">{wallet.address}</p>
          {isFlagged && (
            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 border border-red-500/30">Flagged</span>
          )}
        </div>

        {historyLoading ? (
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-gray-500 text-sm">Fetching full trade history from Polymarket...</p>
          </div>
        ) : fullHistory ? (
          <>
            <p className="text-gray-300 text-sm mb-3">
              This wallet has traded in <span className="text-white font-semibold">{fullHistory.total_markets} markets</span> on Polymarket
              ({fullHistory.total_trades} total trades).
              {fullHistory.resolved_markets > 0 && (
                <> Of the <span className="text-white font-semibold">{fullHistory.resolved_markets} resolved markets</span>,
                they won <span className="text-green-400 font-semibold">{fullHistory.wins}</span> and
                lost <span className="text-red-400 font-semibold">{fullHistory.losses}</span>.</>
              )}
            </p>

            {/* Win rate bar */}
            {fullHistory.resolved_markets > 0 && (
              <div className="flex items-center gap-3 mb-3">
                <div className="flex-1 h-4 bg-gray-800 rounded-full overflow-hidden flex">
                  {fullHistory.wins > 0 && (
                    <div className="bg-green-500 h-full flex items-center justify-center" style={{ width: `${(fullHistory.wins / fullHistory.resolved_markets) * 100}%` }}>
                      {fullHistory.wins > 2 && <span className="text-[10px] font-bold text-white">{fullHistory.wins}W</span>}
                    </div>
                  )}
                  {fullHistory.losses > 0 && (
                    <div className="bg-red-500 h-full flex items-center justify-center" style={{ width: `${(fullHistory.losses / fullHistory.resolved_markets) * 100}%` }}>
                      {fullHistory.losses > 2 && <span className="text-[10px] font-bold text-white">{fullHistory.losses}L</span>}
                    </div>
                  )}
                </div>
                <span className={`text-lg font-mono font-bold shrink-0 ${
                  (fullHistory.win_rate ?? 0) >= 0.80 ? "text-red-400" :
                  (fullHistory.win_rate ?? 0) >= 0.60 ? "text-yellow-400" :
                  "text-gray-300"
                }`}>
                  {fullHistory.win_rate !== null ? `${(fullHistory.win_rate * 100).toFixed(0)}%` : "--"}
                </span>
              </div>
            )}

            {/* Comparison with our dataset */}
            <div className="bg-gray-800/50 rounded px-3 py-2 text-xs text-gray-400">
              Our dataset covers {marketGroups.length} of their {fullHistory.total_markets} markets.
              {fullHistory.win_rate !== null && wallet.win_rate !== fullHistory.win_rate && (
                <> Our subset shows {(wallet.win_rate * 100).toFixed(0)}% win rate vs their real {(fullHistory.win_rate * 100).toFixed(0)}%.</>
              )}
            </div>
          </>
        ) : (
          <p className="text-gray-300 text-sm">
            Traded in <span className="text-white font-semibold">{marketGroups.length} of our analyzed markets</span>.
            Could not fetch full Polymarket history.
          </p>
        )}
      </div>

      {/* ── Entity Investigations ─────────────────────────────── */}
      {wallet.entity_investigations.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
          <h2 className="text-white font-semibold text-sm mb-2">
            Entity Investigations ({wallet.entity_investigations.length})
          </h2>
          <div className="space-y-2">
            {wallet.entity_investigations.map((ctx) => (
              <div
                key={ctx.entity_id}
                className={`border rounded px-3 py-2 ${
                  ctx.is_flagged
                    ? "border-red-800/60 bg-red-950/10"
                    : "border-gray-800 bg-gray-950/40"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <Link
                    to={`/entities/${ctx.entity_id}`}
                    className="text-blue-400 hover:text-blue-300 text-sm transition-colors"
                  >
                    {ctx.entity_name}
                  </Link>
                  {ctx.is_flagged && (
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 border border-red-500/30">
                      Flagged
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-2 text-xs">
                  <div className="text-gray-500">
                    Entity WR <span className="text-gray-300 font-mono">{formatPercent(ctx.entity_win_rate)}</span>
                  </div>
                  <div className="text-gray-500">
                    Overall WR <span className="text-gray-300 font-mono">{formatPercent(ctx.overall_win_rate)}</span>
                  </div>
                  <div className="text-gray-500">
                    Delta{" "}
                    <span
                      className={`font-mono ${
                        (ctx.win_rate_delta ?? 0) >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {formatPercent(ctx.win_rate_delta)}
                    </span>
                  </div>
                  <div className="text-gray-500">
                    Markets <span className="text-gray-300 font-mono">{ctx.entity_markets_traded}</span>
                  </div>
                  <div className="text-gray-500">
                    Resolved <span className="text-gray-300 font-mono">{ctx.entity_resolved_markets}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Flagged Markets (from our analysis) ──────────────────── */}
      {marketGroups.length > 0 && (
        <>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-white font-semibold text-sm">
              Flagged Markets from Our Analysis ({marketGroups.length})
            </h2>
          </div>
          <div className="space-y-2 mb-6">
            {marketGroups.map((group) => (
              <FlaggedMarketCard key={group.market_id} group={group} />
            ))}
          </div>
        </>
      )}

      {/* ── Full Market History ───────────────────────────────────── */}
      {fullHistory && fullHistory.markets.length > 0 && (
        <>
          <div className="mb-3">
            <h2 className="text-white font-semibold text-sm">
              All Markets ({fullHistory.markets.length})
            </h2>
            <p className="text-gray-600 text-xs">Complete trade history from Polymarket</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800">
                    <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Market</th>
                    <th className="text-center text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Result</th>
                    <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Position</th>
                    <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Trades</th>
                    <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {fullHistory.markets.map((m) => (
                    <FullHistoryRow key={m.condition_id} m={m} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
