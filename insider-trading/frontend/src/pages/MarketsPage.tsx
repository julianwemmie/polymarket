import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Market } from "../types";
import SuspicionBadge from "../components/SuspicionBadge";

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

export default function MarketsPage() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getMarkets({ limit: 100 })
      .then(setMarkets)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Loading markets...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load markets</p>
          <p className="text-gray-500 text-sm mt-1">{error}</p>
        </div>
      </div>
    );
  }

  if (markets.length === 0) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 max-w-lg text-center">
          <p className="text-gray-300 font-semibold text-lg">No markets yet</p>
          <p className="text-gray-500 text-sm mt-2">
            Run an investigation from the{" "}
            <Link to="/entities" className="text-blue-400 hover:text-blue-300">
              Investigations
            </Link>{" "}
            page to analyze markets.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Markets</h1>
        <p className="text-gray-500 text-sm mt-1">
          {markets.length} resolved markets analyzed
        </p>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Market
                </th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Resolution
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Volume
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Flagged Wallets
                </th>
                <th className="text-right text-gray-500 font-medium px-4 py-3 text-xs uppercase tracking-wider">
                  Suspicion
                </th>
              </tr>
            </thead>
            <tbody>
              {markets.map((market) => (
                <tr
                  key={market.id}
                  className="border-b border-gray-800/50 hover:bg-gray-800/40 transition-colors"
                >
                  <td className="px-4 py-3 max-w-md">
                    <Link
                      to={`/markets/${market.id}`}
                      className="text-blue-400 hover:text-blue-300 transition-colors text-sm leading-snug block"
                    >
                      {market.question}
                    </Link>
                    <span className="text-gray-600 text-xs">{market.category}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`text-xs font-bold px-2 py-0.5 rounded ${
                        market.resolution === "Yes"
                          ? "bg-green-900/60 text-green-300 border border-green-700/50"
                          : market.resolution === "No"
                            ? "bg-red-900/60 text-red-300 border border-red-700/50"
                            : "bg-gray-800 text-gray-400 border border-gray-700"
                      }`}
                    >
                      {market.resolution || "Pending"}
                    </span>
                  </td>
                  <td className="text-right text-gray-300 font-mono px-4 py-3 text-xs">
                    {formatVolume(market.volume)}
                  </td>
                  <td className="text-right px-4 py-3">
                    <span
                      className={`font-mono text-xs ${
                        market.suspicious_wallet_count > 0
                          ? "text-red-400"
                          : "text-gray-600"
                      }`}
                    >
                      {market.suspicious_wallet_count}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <SuspicionBadge score={market.suspicion_score} size="sm" />
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
