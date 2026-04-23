import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Circle } from 'lucide-react';
import type { Tab } from '../App';

const TAB_LABELS: Record<Tab, string> = {
  dashboard:  "Panel Principal",
  posiciones: "Posiciones Abiertas",
  mercado:    "Análisis de Mercado",
  historial:  "Historial de Operaciones",
  sistema:    "Sistema y Configuración",
};

interface HeaderProps {
  activeTab: Tab;
}

const Header: React.FC<HeaderProps> = ({ activeTab }) => {
  const [equity, setEquity] = useState<number | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const poll = async () => {
    try {
      const ops: any = await invoke("get_operational_status");
      setEquity(ops.equity ?? null);
      setMode(ops.mode ?? null);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  };

  useEffect(() => {
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-800 flex items-center justify-between px-6 shrink-0">
      <h1 className="text-sm font-semibold text-white">{TAB_LABELS[activeTab]}</h1>

      <div className="flex items-center gap-4">
        {/* Connection dot */}
        <div className="flex items-center gap-1.5 text-xs">
          <Circle
            size={7}
            className={connected ? "fill-emerald-500 text-emerald-500" : "fill-rose-500 text-rose-500"}
          />
          <span className={connected ? "text-emerald-400" : "text-rose-400"}>
            {connected ? "Conectado" : "Sin conexión"}
          </span>
        </div>

        {/* Mode badge */}
        {mode && (
          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border ${
            mode === 'LIVE'
              ? 'bg-rose-500/15 text-rose-400 border-rose-500/30'
              : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
          }`}>
            {mode === 'LIVE' ? '⚡ EN VIVO' : '🧪 SIMULACIÓN'}
          </span>
        )}

        {/* Equity */}
        {equity !== null && (
          <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded px-3 py-1">
            <span className="text-[10px] text-gray-500 uppercase font-semibold tracking-wider">Equity</span>
            <span className="font-mono text-sm font-bold text-emerald-400">
              ${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
        )}
      </div>
    </header>
  );
};

export default Header;
