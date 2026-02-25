import type {
  Entity,
  EntityDetail,
  EntityDiscoverResponse,
  EntityDiscoveryMarket,
  EntityWalletScore,
  Market,
  MarketDetail,
  WalletDetail,
  WalletFullHistory,
} from "../types";

export const API_BASE = "http://localhost:8000/api";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `API error: ${res.status}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json();
}

async function fetchJson<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function putJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "PUT",
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function deleteJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: "DELETE" });
}

export const api = {
  fetchJson,
  postJson,
  putJson,
  deleteJson,

  getMarkets: (params?: { entity?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.entity) search.set("entity", params.entity);
    if (params?.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return fetchJson<Market[]>(`/markets${qs ? `?${qs}` : ""}`);
  },

  getMarket: (id: string) => fetchJson<MarketDetail>(`/markets/${id}`),
  getWallet: (address: string) => fetchJson<WalletDetail>(`/wallets/${address}`),
  getWalletFullHistory: (address: string) =>
    fetchJson<WalletFullHistory>(`/wallets/${address}/full-history`),

  createEntity: (payload: { name: string; search_terms: string[] }) =>
    postJson<Entity>("/entities", payload),
  getEntities: () => fetchJson<Entity[]>("/entities"),
  getEntity: (id: number | string) => fetchJson<EntityDetail>(`/entities/${id}`),
  discoverEntityMarkets: (id: number | string) =>
    postJson<EntityDiscoverResponse>(`/entities/${id}/discover`),
  analyzeEntity: (
    id: number | string,
    markets: EntityDiscoveryMarket[],
  ) => postJson<{ status: string }>(`/entities/${id}/analyze`, { markets }),
  getEntityWallets: (
    id: number | string,
    params?: { min_entity_markets?: number; limit?: number; sort?: string },
  ) => {
    const search = new URLSearchParams();
    if (params?.min_entity_markets)
      search.set("min_entity_markets", String(params.min_entity_markets));
    if (params?.limit) search.set("limit", String(params.limit));
    if (params?.sort) search.set("sort", params.sort);
    const qs = search.toString();
    return fetchJson<EntityWalletScore[]>(
      `/entities/${id}/wallets${qs ? `?${qs}` : ""}`,
    );
  },
  deleteEntity: (id: number | string) =>
    deleteJson<{ status: string; entity_id: number }>(`/entities/${id}`),
  entityProgressUrl: (id: number | string) => `${API_BASE}/entities/${id}/progress`,
};
