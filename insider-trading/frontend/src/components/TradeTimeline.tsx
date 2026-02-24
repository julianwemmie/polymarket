import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import type { Trade } from "../types";

interface TradeTimelineProps {
  trades: Trade[];
  resolvedAt: string | null;
}

function truncateAddress(addr: string): string {
  if (addr.length <= 10) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function formatTimestamp(ts: number): string {
  return new Date(ts).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

interface TooltipPayloadEntry {
  payload: {
    originalTrade: Trade;
    timestamp: number;
    amount: number;
  };
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const data = payload[0].payload;
  const trade = data.originalTrade;
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 shadow-xl text-sm">
      <p className="text-gray-400 font-mono text-xs">
        {truncateAddress(trade.wallet_address)}
      </p>
      <p className="text-white font-medium mt-1">
        ${trade.amount.toLocaleString()}
      </p>
      <p className="text-gray-300">
        {trade.side} {trade.outcome} @ {(trade.price * 100).toFixed(0)}c
      </p>
      <p className="text-gray-500 text-xs mt-1">
        {new Date(trade.timestamp).toLocaleString()}
      </p>
      {trade.is_suspicious && (
        <p className="text-red-400 text-xs mt-1 font-medium">
          Flagged suspicious
        </p>
      )}
    </div>
  );
}

export default function TradeTimeline({
  trades,
  resolvedAt,
}: TradeTimelineProps) {
  const suspiciousTrades = trades
    .filter((t) => t.is_suspicious)
    .map((t) => ({
      timestamp: new Date(t.timestamp).getTime(),
      amount: t.amount,
      originalTrade: t,
    }));

  const normalTrades = trades
    .filter((t) => !t.is_suspicious)
    .map((t) => ({
      timestamp: new Date(t.timestamp).getTime(),
      amount: t.amount,
      originalTrade: t,
    }));

  const resolvedAtMs = resolvedAt ? new Date(resolvedAt).getTime() : null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h3 className="text-white font-semibold text-sm mb-4 flex items-center gap-2">
        <span className="w-2 h-2 bg-red-500 rounded-full" />
        Trade Timeline
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatTimestamp}
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            name="Time"
          />
          <YAxis
            dataKey="amount"
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            name="Amount"
          />
          <Tooltip
            content={<CustomTooltip />}
            cursor={{ strokeDasharray: "3 3", stroke: "#4b5563" }}
          />
          {resolvedAtMs && (
            <ReferenceLine
              x={resolvedAtMs}
              stroke="#f59e0b"
              strokeDasharray="5 5"
              label={{
                value: "Resolution",
                position: "top",
                fill: "#f59e0b",
                fontSize: 11,
              }}
            />
          )}
          <Scatter
            name="Normal Trades"
            data={normalTrades}
            fill="#4b5563"
            fillOpacity={0.6}
          />
          <Scatter
            name="Suspicious Trades"
            data={suspiciousTrades}
            fill="#ef4444"
            fillOpacity={0.9}
          />
        </ScatterChart>
      </ResponsiveContainer>
      <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-gray-500" />
          Normal trades
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-red-500" />
          Suspicious trades
        </span>
        {resolvedAt && (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-amber-500" />
            Resolution date
          </span>
        )}
      </div>
    </div>
  );
}
