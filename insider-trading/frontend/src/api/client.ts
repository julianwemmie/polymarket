import type { LeaderboardEntry, Market, MarketDetail, WalletDetail } from "../types";

const API_BASE = "http://localhost:8000/api";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  getLeaderboard: () => fetchJson<LeaderboardEntry[]>("/leaderboard"),
  getMarkets: (params?: { entity?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.entity) search.set("entity", params.entity);
    if (params?.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return fetchJson<Market[]>(`/markets${qs ? `?${qs}` : ""}`);
  },
  getMarket: (id: string) => fetchJson<MarketDetail>(`/markets/${id}`),
  getWallet: (address: string) => fetchJson<WalletDetail>(`/wallets/${address}`),
};
