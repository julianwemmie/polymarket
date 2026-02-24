import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { WalletDetail } from "../types";
import SuspicionBadge from "../components/SuspicionBadge";

function truncateAddress(addr: string): string {
  if (addr.length <= 10) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

export default function WalletPage() {
  const { address } = useParams<{ address: string }>();
  const [wallet, setWallet] = useState<WalletDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!address) return;
    setLoading(true);
    api
      .getWallet(address)
      .then(setWallet)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [address]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Investigating wallet...</p>
        </div>
      </div>
    );
  }

  if (error || !wallet) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load wallet</p>
          <p className="text-gray-500 text-sm mt-1">
            {error || "Wallet not found"}
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

  const sortedTrades = [...wallet.trades].sort(
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
        <span className="text-gray-400">Wallet</span>
      </div>

      {/* Wallet Header */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <p className="text-gray-500 text-xs uppercase tracking-wider mb-1">
              Wallet Address
            </p>
            <p className="text-white font-mono text-lg break-all">
              {wallet.address}
            </p>
          </div>
          <SuspicionBadge score={wallet.suspicion_score} size="lg" />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-4">
          <div>
            <p className="text-gray-500 text-xs uppercase tracking-wider">
              First Seen
            </p>
            <p className="text-white text-sm font-medium mt-0.5">
              {wallet.first_seen
                ? new Date(wallet.first_seen).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })
                : "Unknown"}
            </p>
          </div>
          <div>
            <p className="text-gray-500 text-xs uppercase tracking-wider">
              Markets
            </p>
            <p className="text-white text-sm font-medium mt-0.5">
              {wallet.market_count}
            </p>
          </div>
          <div>
            <p className="text-gray-500 text-xs uppercase tracking-wider">
              Total Volume
            </p>
            <p className="text-white text-sm font-medium mt-0.5">
              {formatVolume(wallet.total_volume)}
            </p>
          </div>
          <div>
            <p className="text-gray-500 text-xs uppercase tracking-wider">
              Total Profit
            </p>
            <p
              className={`text-sm font-medium mt-0.5 ${
                wallet.total_profit >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              {wallet.total_profit >= 0 ? "+" : ""}
              {formatVolume(wallet.total_profit)}
            </p>
          </div>
        </div>

        {wallet.funding_source && (
          <div className="mt-4 pt-4 border-t border-gray-800">
            <p className="text-gray-500 text-xs uppercase tracking-wider">
              Funding Source
            </p>
            <p className="text-gray-300 font-mono text-xs mt-0.5">
              {wallet.funding_source}
            </p>
          </div>
        )}
      </div>

      {/* Suspicion Flags */}
      {wallet.suspicion_flags.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-6">
          <h2 className="text-white font-semibold text-sm mb-4 flex items-center gap-2">
            <span className="w-2 h-2 bg-red-500 rounded-full" />
            Suspicion Flags
            <span className="text-gray-500 font-normal">
              ({wallet.suspicion_flags.length})
            </span>
          </h2>

          <div className="space-y-4">
            {wallet.suspicion_flags.map((flag) => (
              <div
                key={flag.id}
                className="bg-gray-950/50 border border-gray-800 rounded-lg p-4"
              >
                <div className="flex items-center justify-between mb-2">
                  <Link
                    to={`/markets/${flag.market_id}`}
                    className="text-blue-400 hover:text-blue-300 text-sm font-medium transition-colors"
                  >
                    Market {truncateAddress(flag.market_id)}
                  </Link>
                  <div className="flex items-center gap-3">
                    <SuspicionBadge score={flag.score} size="sm" />
                    <span className="text-gray-600 text-xs">
                      {new Date(flag.created_at).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </span>
                  </div>
                </div>
                <ul className="space-y-1 mt-2">
                  {flag.reasons.map((reason, i) => (
                    <li
                      key={i}
                      className="text-gray-400 text-xs flex items-start gap-2"
                    >
                      <span className="text-red-500 mt-0.5 shrink-0">
                        *
                      </span>
                      {reason}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trade History Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-white font-semibold text-sm flex items-center gap-2">
            <span className="w-2 h-2 bg-blue-500 rounded-full" />
            Trade History
          </h2>
          <span className="text-gray-500 text-xs">
            {wallet.trades.length} trades |{" "}
            {wallet.trades.filter((t) => t.is_suspicious).length} flagged
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
                  Market
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
                      to={`/markets/${trade.market_id}`}
                      className="font-mono text-xs text-blue-400 hover:text-blue-300 transition-colors"
                    >
                      {truncateAddress(trade.market_id)}
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
