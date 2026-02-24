import { Link } from "react-router-dom";
import type { Market } from "../types";
import SuspicionBadge from "./SuspicionBadge";

interface MarketCardProps {
  market: Market;
}

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

export default function MarketCard({ market }: MarketCardProps) {
  return (
    <Link
      to={`/markets/${market.id}`}
      className="block bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-600 hover:bg-gray-900/80 transition-all group"
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <h3 className="text-white text-sm font-medium leading-snug group-hover:text-gray-100 flex-1">
          {market.question}
        </h3>
        <span
          className={`shrink-0 text-xs font-bold px-2 py-0.5 rounded ${
            market.resolution === "Yes"
              ? "bg-green-900/60 text-green-300 border border-green-700/50"
              : market.resolution === "No"
                ? "bg-red-900/60 text-red-300 border border-red-700/50"
                : "bg-gray-800 text-gray-400 border border-gray-700"
          }`}
        >
          {market.resolution || "Pending"}
        </span>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded border border-gray-700">
          {market.entity}
        </span>
        <span className="text-xs text-gray-500">{market.category}</span>
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-gray-400">
          <span>
            Vol{" "}
            <span className="text-gray-200 font-medium">
              {formatVolume(market.volume)}
            </span>
          </span>
          <span>
            Flagged{" "}
            <span
              className={`font-medium ${
                market.suspicious_wallet_count > 0
                  ? "text-red-400"
                  : "text-gray-200"
              }`}
            >
              {market.suspicious_wallet_count}
            </span>{" "}
            wallets
          </span>
        </div>
        <SuspicionBadge score={market.suspicion_score} size="sm" />
      </div>
    </Link>
  );
}
