import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { MarketDetail, Trade } from "../types";

function formatVolume(volume: number): string {
  if (volume >= 1_000_000) return `$${(volume / 1_000_000).toFixed(1)}M`;
  if (volume >= 1_000) return `$${(volume / 1_000).toFixed(1)}k`;
  return `$${volume.toFixed(0)}`;
}

function truncateAddress(addr: string): string {
  if (addr.length <= 10) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

interface WalletSummary {
  address: string;
  trades: Trade[];
  net_profit: number;
  total_volume: number;
  buy_count: number;
  sell_count: number;
  is_suspicious: boolean;
}

function groupByWallet(trades: Trade[]): WalletSummary[] {
  const map = new Map<string, Trade[]>();
  for (const t of trades) {
    const existing = map.get(t.wallet_address) || [];
    existing.push(t);
    map.set(t.wallet_address, existing);
  }

  const wallets: WalletSummary[] = [];
  for (const [address, walletTrades] of map.entries()) {
    wallets.push({
      address,
      trades: walletTrades.sort(
        (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      ),
      net_profit: walletTrades.reduce((sum, t) => sum + (t.profit || 0), 0),
      total_volume: walletTrades.reduce((sum, t) => sum + t.amount, 0),
      buy_count: walletTrades.filter((t) => t.side === "BUY").length,
      sell_count: walletTrades.filter((t) => t.side === "SELL").length,
      is_suspicious: walletTrades.some((t) => t.is_suspicious),
    });
  }

  // Flagged first, then by profit
  wallets.sort((a, b) => {
    if (a.is_suspicious && !b.is_suspicious) return -1;
    if (!a.is_suspicious && b.is_suspicious) return 1;
    return b.net_profit - a.net_profit;
  });

  return wallets;
}

function WalletRow({ wallet }: { wallet: WalletSummary }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr
        onClick={() => setOpen(!open)}
        className={`border-b border-gray-800/50 cursor-pointer transition-colors ${
          wallet.is_suspicious
            ? "bg-red-950/10 hover:bg-red-950/20"
            : "hover:bg-gray-800/30"
        }`}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <Link
              to={`/wallets/${wallet.address}`}
              onClick={(e) => e.stopPropagation()}
              className="text-blue-400 hover:text-blue-300 font-mono text-xs transition-colors"
            >
              {truncateAddress(wallet.address)}
            </Link>
            {wallet.is_suspicious && (
              <span className="text-xs font-semibold px-1.5 py-0 rounded-full bg-red-500/20 text-red-400 border border-red-500/30">
                Flagged
              </span>
            )}
          </div>
        </td>
        <td className="px-4 py-3 text-right text-gray-400 font-mono text-xs">
          {wallet.trades.length}
        </td>
        <td className="px-4 py-3 text-right text-gray-300 font-mono text-xs">
          {formatVolume(wallet.total_volume)}
        </td>
        <td className="px-4 py-3 text-right font-mono text-xs">
          <span className={wallet.net_profit >= 0 ? "text-green-400" : "text-red-400"}>
            {wallet.net_profit >= 0 ? "+" : ""}
            {formatVolume(wallet.net_profit)}
          </span>
        </td>
        <td className="px-4 py-3 text-right">
          <svg
            className={`w-4 h-4 text-gray-500 transition-transform inline ${open ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </td>
      </tr>
      {open && wallet.trades.map((t) => (
        <tr key={t.id} className="border-b border-gray-800/20 bg-gray-950/30">
          <td className="pl-8 pr-4 py-1.5 text-gray-600 text-xs whitespace-nowrap">
            {new Date(t.timestamp).toLocaleString("en-US", {
              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            })}
          </td>
          <td className="px-4 py-1.5 text-xs">
            <span className={t.side === "BUY" ? "text-green-400" : "text-red-400"}>
              {t.side}
            </span>
            {" "}
            <span className="text-gray-500">{t.outcome}</span>
          </td>
          <td className="px-4 py-1.5 text-right text-gray-400 font-mono text-xs">
            {formatVolume(t.amount)} @ {(t.price * 100).toFixed(1)}c
          </td>
          <td className="px-4 py-1.5 text-right font-mono text-xs">
            {t.profit !== null ? (
              <span className={t.profit >= 0 ? "text-green-400" : "text-red-400"}>
                {t.profit >= 0 ? "+" : ""}{formatVolume(t.profit)}
              </span>
            ) : (
              <span className="text-gray-700">--</span>
            )}
          </td>
          <td />
        </tr>
      ))}
    </>
  );
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
          <p className="text-gray-500 text-sm">Loading market...</p>
        </div>
      </div>
    );
  }

  if (error || !market) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-6 max-w-md text-center">
          <p className="text-red-400 font-medium">Failed to load market</p>
          <p className="text-gray-500 text-sm mt-1">{error || "Market not found"}</p>
          <Link to="/markets" className="inline-block mt-4 text-sm text-gray-400 hover:text-white transition-colors">
            Back to markets
          </Link>
        </div>
      </div>
    );
  }

  const walletSummaries = groupByWallet(market.trades);
  const flaggedCount = walletSummaries.filter((w) => w.is_suspicious).length;

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-4">
        <Link to="/markets" className="hover:text-gray-300 transition-colors">Markets</Link>
        <span>/</span>
        <span className="text-gray-400 truncate max-w-xs">{market.question.slice(0, 40)}...</span>
      </div>

      {/* Market Header */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-6">
        <h1 className="text-xl font-bold text-white leading-snug mb-3">
          {market.question}
        </h1>

        <div className="flex flex-wrap items-center gap-3">
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
          <span className="text-xs text-gray-400">
            Volume: <span className="text-white font-medium">{formatVolume(market.volume)}</span>
          </span>
          <span className="text-xs text-gray-400">
            Wallets: <span className="text-white font-medium">{walletSummaries.length}</span>
          </span>
          {flaggedCount > 0 && (
            <span className="text-xs text-red-400">
              Flagged: <span className="font-medium">{flaggedCount}</span>
            </span>
          )}
          <span className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded border border-gray-700">
            {market.category}
          </span>
        </div>
      </div>

      {/* Wallet Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800">
          <h2 className="text-white font-semibold text-sm">
            Traders ({walletSummaries.length})
          </h2>
          <p className="text-gray-600 text-xs mt-0.5">Click a row to expand individual trades</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Wallet</th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Trades</th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Volume</th>
                <th className="text-right text-gray-500 font-medium px-4 py-2.5 text-xs uppercase tracking-wider">Profit</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {walletSummaries.map((w) => (
                <WalletRow key={w.address} wallet={w} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
