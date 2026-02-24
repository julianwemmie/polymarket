import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { MarketDetail } from "../types";
import SuspicionBadge from "../components/SuspicionBadge";
import TradeTimeline from "../components/TradeTimeline";
import VolumeChart from "../components/VolumeChart";

function truncateAddress(addr: string): string {
  if (addr.length <= 10) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

export default function MarketPage() {
  const { id } = useParams<{ id: string }>();
  const [market, setMarket] = useState<MarketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api
      .getMarket(id)
      .then(setMarket)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Loading market data...</p>
        </div>
      </div>
    );
  }

  if (error || !market) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load market</p>
          <p className="text-gray-500 text-sm mt-1">
            {error || "Market not found"}
          </p>
          <Link
            to="/"
            className="inline-block mt-4 text-sm text-gray-400 hover:text-white transition-colors"
          >
            Back to leaderboard
          </Link>
        </div>
      </div>
    );
  }

  const sortedTrades = [...market.trades].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-4">
        <Link to="/" className="hover:text-gray-300 transition-colors">
          Leaderboard
        </Link>
        <span>/</span>
        <span className="text-gray-400">Market</span>
      </div>

      {/* Market Header */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <h1 className="text-xl font-bold text-white leading-snug flex-1">
            {market.question}
          </h1>
          <SuspicionBadge score={market.suspicion_score} size="lg" />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs bg-gray-800 text-gray-300 px-2.5 py-1 rounded border border-gray-700">
            {market.entity}
          </span>
          <span className="text-xs text-gray-500">{market.category}</span>
          <span className="text-gray-700">|</span>
          <span
            className={`text-xs font-bold px-2 py-0.5 rounded ${
              market.resolution === "Yes"
                ? "bg-green-900/60 text-green-300 border border-green-700/50"
                : market.resolution === "No"
                  ? "bg-red-900/60 text-red-300 border border-red-700/50"
                  : "bg-gray-800 text-gray-400 border border-gray-700"
            }`}
          >
            Resolved: {market.resolution || "Pending"}
          </span>
          <span className="text-gray-700">|</span>
          <span className="text-xs text-gray-400">
            Volume:{" "}
            <span className="text-white font-medium">
              {formatVolume(market.volume)}
            </span>
          </span>
          <span className="text-gray-700">|</span>
          <span className="text-xs text-gray-400">
            Flagged wallets:{" "}
            <span className="text-red-400 font-medium">
              {market.suspicious_wallet_count}
            </span>
          </span>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <TradeTimeline
          trades={market.trades}
          resolvedAt={market.resolved_at}
        />
        <VolumeChart trades={market.trades} />
      </div>

      {/* Trades Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-white font-semibold text-sm flex items-center gap-2">
            <span className="w-2 h-2 bg-blue-500 rounded-full" />
            Trade History
          </h2>
          <span className="text-gray-500 text-xs">
            {market.trades.length} trades |{" "}
            {market.trades.filter((t) => t.is_suspicious).length} flagged
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Time
                </th>
                <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Wallet
                </th>
                <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Side
                </th>
                <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Outcome
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Amount
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Price
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Profit
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedTrades.map((trade) => (
                <tr
                  key={trade.id}
                  className={`border-b border-gray-800/50 transition-colors ${
                    trade.is_suspicious
                      ? "bg-red-950/20 hover:bg-red-950/30"
                      : "hover:bg-gray-800/30"
                  }`}
                >
                  <td className="px-4 py-2.5 text-gray-400 text-xs whitespace-nowrap">
                    {new Date(trade.timestamp).toLocaleString("en-US", {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                  <td className="px-4 py-2.5">
                    <Link
                      to={`/wallets/${trade.wallet_address}`}
                      className="font-mono text-xs text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      {truncateAddress(trade.wallet_address)}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`text-xs font-medium ${
                        trade.side === "BUY"
                          ? "text-green-400"
                          : "text-red-400"
                      }`}
                    >
                      {trade.side}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-gray-300 text-xs">
                    {trade.outcome}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-200 font-mono text-xs">
                    {formatVolume(trade.amount)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-400 font-mono text-xs">
                    {(trade.price * 100).toFixed(0)}c
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">
                    {trade.profit !== null ? (
                      <span
                        className={
                          trade.profit >= 0
                            ? "text-green-400"
                            : "text-red-400"
                        }
                      >
                        {trade.profit >= 0 ? "+" : ""}
                        {formatVolume(trade.profit)}
                      </span>
                    ) : (
                      <span className="text-gray-600">--</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {trade.is_suspicious ? (
                      <span className="text-xs text-red-400 font-medium">
                        FLAGGED
                      </span>
                    ) : (
                      <span className="text-xs text-gray-600">--</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
