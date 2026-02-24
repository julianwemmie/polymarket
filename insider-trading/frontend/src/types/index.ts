export interface Market {
  id: string;
  question: string;
  slug: string;
  entity: string;
  category: string;
  resolution: string;
  resolved_at: string | null;
  created_at: string;
  volume: number;
  suspicious_wallet_count: number;
  suspicion_score: number;
}

export interface Trade {
  id: string;
  market_id: string;
  wallet_address: string;
  side: "BUY" | "SELL";
  outcome: "Yes" | "No";
  amount: number;
  price: number;
  profit: number | null;
  timestamp: string;
  is_suspicious: boolean;
}

export interface Wallet {
  address: string;
  first_seen: string | null;
  market_count: number;
  total_volume: number;
  total_profit: number;
  suspicion_score: number;
  funding_source: string | null;
}

export interface SuspicionFlag {
  id: number;
  wallet_address: string;
  market_id: string;
  score: number;
  reasons: string[];
  created_at: string;
}

export interface MarketDetail extends Market {
  trades: Trade[];
}

export interface WalletDetail extends Wallet {
  trades: Trade[];
  suspicion_flags: SuspicionFlag[];
}

export interface LeaderboardEntry {
  entity: string;
  total_suspicious_wallets: number;
  total_markets_affected: number;
  avg_suspicion_score: number;
  total_suspicious_volume: number;
  top_markets: Market[];
}
