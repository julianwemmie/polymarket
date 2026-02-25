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
  outcome: "Yes" | "No" | string;
  amount: number;
  price: number;
  profit: number | null;
  timestamp: string;
  is_suspicious: boolean;
  market_question?: string;
  market_resolution?: string;
}

export interface WalletEntityContext {
  entity_id: number;
  entity_name: string;
  entity_markets_traded: number;
  entity_resolved_markets: number;
  entity_win_rate: number | null;
  overall_win_rate: number | null;
  win_rate_delta: number | null;
  suspicion_score: number | null;
  is_flagged: boolean;
}

export interface Wallet {
  address: string;
  first_seen: string | null;
  market_count: number;
  total_volume: number;
  total_profit: number;
  suspicion_score: number;
  funding_source: string | null;
  win_count: number;
  loss_count: number;
  win_rate: number;
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
  entity_investigations: WalletEntityContext[];
}

export interface FullMarketRecord {
  condition_id: string;
  title: string;
  outcome_bought: string;
  side: string;
  trades: number;
  total_size: number;
  total_cost: number;
  resolved: boolean;
  won: boolean | null;
}

export interface WalletFullHistory {
  address: string;
  total_trades: number;
  total_markets: number;
  resolved_markets: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  markets: FullMarketRecord[];
}

export type EntityStatus =
  | "draft"
  | "searching"
  | "ingesting"
  | "scoring"
  | "done"
  | "error";

export interface Entity {
  id: number;
  name: string;
  search_terms: string[];
  status: EntityStatus;
  discovered_market_count: number;
  included_market_count: number;
  scored_wallet_count: number;
  flagged_wallet_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface EntityMarket {
  id: number;
  entity_id: number;
  condition_id: string;
  question: string;
  slug: string | null;
  volume: number;
  resolved: boolean;
  winning_outcome: string | null;
  match_term: string | null;
  included: boolean;
  created_at: string;
}

export interface EntityMarketBreakdown {
  condition_id: string;
  question: string;
  resolved: boolean;
  won: boolean | null;
  profit: number;
  trade_count: number;
  winning_outcome: string | null;
}

export interface EntityWalletScore {
  id: number;
  entity_id: number;
  wallet_address: string;
  entity_markets_traded: number;
  entity_resolved_markets: number;
  entity_wins: number;
  entity_losses: number;
  entity_win_rate: number | null;
  entity_profit: number;
  overall_markets: number;
  overall_wins: number;
  overall_losses: number;
  overall_win_rate: number | null;
  win_rate_delta: number | null;
  suspicion_score: number | null;
  is_flagged: boolean;
  reasons: string[];
  market_breakdown: EntityMarketBreakdown[] | null;
  created_at: string;
}

export interface EntityDetail extends Entity {
  markets: EntityMarket[];
  wallet_scores: EntityWalletScore[];
}

export interface EntityDiscoveryMarket {
  condition_id: string;
  question: string;
  slug: string | null;
  volume: number;
  resolved: boolean;
  winning_outcome: string | null;
  match_terms: string[];
  included: boolean;
}

export interface EntityDiscoverResponse {
  entity_id: number;
  markets: EntityDiscoveryMarket[];
}

export interface EntityProgress {
  running: boolean;
  done: boolean;
  stage: string;
  current: number;
  total: number;
  current_market: string;
  wallet_current: number;
  wallet_total: number;
  current_wallet: string;
  wallet_stage?: string;
  resolved_markets: number;
  error: string | null;
}
