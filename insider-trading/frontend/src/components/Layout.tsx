import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation();

  const navLinks = [
    { to: "/", label: "Leaderboard" },
    { to: "/markets", label: "Markets" },
  ];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Navbar */}
      <nav className="bg-gray-900 border-b border-gray-800 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <Link to="/" className="flex items-center gap-3 group">
              <div className="w-8 h-8 bg-red-600 rounded flex items-center justify-center text-white font-bold text-sm group-hover:bg-red-500 transition-colors">
                IT
              </div>
              <div className="flex flex-col">
                <span className="text-white font-bold text-sm leading-tight tracking-wide">
                  Who Can't Keep a Secret?
                </span>
                <span className="text-gray-500 text-xs leading-tight">
                  Polymarket Insider Trading Detector
                </span>
              </div>
            </Link>

            <div className="flex items-center gap-1">
              {navLinks.map((link) => {
                const isActive =
                  link.to === "/"
                    ? location.pathname === "/"
                    : location.pathname.startsWith(link.to);
                return (
                  <Link
                    key={link.to}
                    to={link.to}
                    className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-gray-800 text-white"
                        : "text-gray-400 hover:text-white hover:bg-gray-800/50"
                    }`}
                  >
                    {link.label}
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">
        {children}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 py-4">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <p className="text-center text-gray-600 text-xs">
            On-chain analysis tool for educational purposes. Not financial
            advice. Data sourced from public blockchain records and Polymarket
            APIs.
          </p>
        </div>
      </footer>
    </div>
  );
}
