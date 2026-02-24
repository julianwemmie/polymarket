interface SuspicionBadgeProps {
  score: number;
  size?: "sm" | "md" | "lg";
}

export default function SuspicionBadge({
  score,
  size = "md",
}: SuspicionBadgeProps) {
  const pct = Math.round(score * 100);

  let bgColor: string;
  let textColor: string;
  let label: string;

  if (score >= 0.7) {
    bgColor = "bg-red-900/60 border-red-700/50";
    textColor = "text-red-300";
    label = "High Risk";
  } else if (score >= 0.4) {
    bgColor = "bg-amber-900/60 border-amber-700/50";
    textColor = "text-amber-300";
    label = "Medium Risk";
  } else {
    bgColor = "bg-green-900/60 border-green-700/50";
    textColor = "text-green-300";
    label = "Low Risk";
  }

  const sizeClasses = {
    sm: "text-xs px-2 py-0.5",
    md: "text-sm px-2.5 py-1",
    lg: "text-base px-3 py-1.5",
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border font-medium ${bgColor} ${textColor} ${sizeClasses[size]}`}
    >
      <span className="font-semibold">{pct}%</span>
      <span className="opacity-80">{label}</span>
    </span>
  );
}
