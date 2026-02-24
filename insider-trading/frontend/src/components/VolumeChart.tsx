import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { Trade } from "../types";

interface VolumeChartProps {
  trades: Trade[];
}

interface DayBucket {
  date: string;
  normalVolume: number;
  suspiciousVolume: number;
}

function aggregateByDay(trades: Trade[]): DayBucket[] {
  const buckets: Record<string, DayBucket> = {};

  for (const trade of trades) {
    const date = trade.timestamp.split("T")[0];
    if (!buckets[date]) {
      buckets[date] = { date, normalVolume: 0, suspiciousVolume: 0 };
    }
    if (trade.is_suspicious) {
      buckets[date].suspiciousVolume += trade.amount;
    } else {
      buckets[date].normalVolume += trade.amount;
    }
  }

  return Object.values(buckets).sort((a, b) => a.date.localeCompare(b.date));
}

function formatDate(date: string): string {
  const d = new Date(date + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatVolume(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}k`;
  return `$${value.toFixed(0)}`;
}

interface TooltipPayloadEntry {
  dataKey: string;
  value: number;
  color: string;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const normal =
    payload.find((p) => p.dataKey === "normalVolume")?.value ?? 0;
  const suspicious =
    payload.find((p) => p.dataKey === "suspiciousVolume")?.value ?? 0;
  const total = normal + suspicious;
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 shadow-xl text-sm">
      <p className="text-gray-400 text-xs">{label ? formatDate(label) : ""}</p>
      <p className="text-white font-medium mt-1">
        Total: {formatVolume(total)}
      </p>
      <p className="text-gray-400">Normal: {formatVolume(normal)}</p>
      <p className="text-red-400">Suspicious: {formatVolume(suspicious)}</p>
    </div>
  );
}

export default function VolumeChart({ trades }: VolumeChartProps) {
  const data = aggregateByDay(trades);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h3 className="text-white font-semibold text-sm mb-4 flex items-center gap-2">
        <span className="w-2 h-2 bg-amber-500 rounded-full" />
        Daily Volume
      </h3>
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={data} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => formatVolume(v)}
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
          />
          <Tooltip content={<CustomTooltip />} />
          <Bar
            dataKey="normalVolume"
            stackId="volume"
            fill="#374151"
            radius={[0, 0, 0, 0]}
            name="Normal Volume"
          />
          <Bar
            dataKey="suspiciousVolume"
            stackId="volume"
            fill="#dc2626"
            radius={[2, 2, 0, 0]}
            name="Suspicious Volume"
          />
        </BarChart>
      </ResponsiveContainer>
      <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-gray-700" />
          Normal volume
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-2 rounded-sm bg-red-600" />
          Suspicious volume
        </span>
      </div>
    </div>
  );
}
