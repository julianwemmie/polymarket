import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Entity } from "../types";

function statusClass(status: Entity["status"]): string {
  switch (status) {
    case "done":
      return "bg-green-500/20 text-green-300 border border-green-500/30";
    case "error":
      return "bg-red-500/20 text-red-300 border border-red-500/30";
    case "ingesting":
    case "scoring":
      return "bg-amber-500/20 text-amber-300 border border-amber-500/30";
    case "searching":
      return "bg-blue-500/20 text-blue-300 border border-blue-500/30";
    default:
      return "bg-gray-700 text-gray-300 border border-gray-600";
  }
}

export default function EntitiesPage() {
  const navigate = useNavigate();

  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [termInput, setTermInput] = useState("");
  const [terms, setTerms] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  const loadEntities = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getEntities()
      .then(setEntities)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadEntities();
  }, [loadEntities]);

  const addTerm = useCallback(() => {
    const cleaned = termInput.trim();
    if (!cleaned) return;
    if (!terms.includes(cleaned)) {
      setTerms((prev) => [...prev, cleaned]);
    }
    setTermInput("");
  }, [termInput, terms]);

  const onTermKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTerm();
    }
  };

  const removeTerm = (term: string) => {
    setTerms((prev) => prev.filter((t) => t !== term));
  };

  const canSubmit = useMemo(() => {
    return name.trim().length > 0 && terms.length > 0 && !creating;
  }, [creating, name, terms.length]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (termInput.trim()) {
      addTerm();
      return;
    }
    if (!canSubmit) return;

    try {
      setCreating(true);
      const created = await api.createEntity({
        name: name.trim(),
        search_terms: terms,
      });
      setName("");
      setTerms([]);
      setTermInput("");
      await loadEntities();
      navigate(`/entities/${created.id}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create investigation";
      setError(message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h1 className="text-xl font-semibold text-white">New Investigation</h1>
        <p className="text-sm text-gray-500 mt-1">
          Choose an entity and keyword terms. You can review discovered markets before analysis.
        </p>

        <form onSubmit={onSubmit} className="mt-4 space-y-4">
          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Entity Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Google, Federal Reserve, Trump..."
              className="w-full bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:ring-1 focus:ring-red-500"
            />
          </div>

          <div>
            <label className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Search Terms</label>
            <div className="bg-gray-950 border border-gray-700 rounded px-3 py-2">
              <div className="flex flex-wrap gap-2 mb-2">
                {terms.map((term) => (
                  <button
                    type="button"
                    key={term}
                    onClick={() => removeTerm(term)}
                    className="text-xs px-2 py-1 rounded bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700"
                  >
                    {term} ×
                  </button>
                ))}
              </div>
              <input
                value={termInput}
                onChange={(e) => setTermInput(e.target.value)}
                onKeyDown={onTermKeyDown}
                onBlur={addTerm}
                placeholder="Type term and press Enter (e.g. GOOG, Alphabet, antitrust)"
                className="w-full bg-transparent text-sm text-white placeholder:text-gray-600 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-gray-500">Use 2-5 focused terms for best discovery coverage.</p>
            <button
              type="submit"
              disabled={!canSubmit}
              className="px-4 py-2 rounded text-sm font-medium bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed"
            >
              {creating ? "Creating..." : "Create Investigation"}
            </button>
          </div>
        </form>
      </section>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">Investigations</h2>
          <button
            type="button"
            onClick={loadEntities}
            className="text-xs text-gray-400 hover:text-white"
          >
            Refresh
          </button>
        </div>

        {loading ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-sm text-gray-500">
            Loading investigations...
          </div>
        ) : entities.length === 0 ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-8 text-center text-sm text-gray-500">
            No investigations yet.
          </div>
        ) : (
          <div className="space-y-2">
            {entities.map((entity) => (
              <Link
                key={entity.id}
                to={`/entities/${entity.id}`}
                className="block bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 hover:border-gray-700 transition-colors"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-white font-medium">{entity.name}</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Terms: {entity.search_terms.join(", ")}
                    </p>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded ${statusClass(entity.status)}`}>
                    {entity.status}
                  </span>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 text-xs">
                  <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
                    <p className="text-gray-500">Discovered</p>
                    <p className="text-gray-200 font-mono">{entity.discovered_market_count}</p>
                  </div>
                  <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
                    <p className="text-gray-500">Included</p>
                    <p className="text-gray-200 font-mono">{entity.included_market_count}</p>
                  </div>
                  <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
                    <p className="text-gray-500">Wallets Scored</p>
                    <p className="text-gray-200 font-mono">{entity.scored_wallet_count}</p>
                  </div>
                  <div className="bg-gray-950 border border-gray-800 rounded px-2 py-1">
                    <p className="text-gray-500">Flagged</p>
                    <p className="text-red-300 font-mono">{entity.flagged_wallet_count}</p>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {error && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-3 text-sm text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
