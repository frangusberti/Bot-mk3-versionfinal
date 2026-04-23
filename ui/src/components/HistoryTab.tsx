import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { ChevronDown } from 'lucide-react';

interface Trade {
  symbol: string;
  side: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl_net: number;
  fees: number;
  entry_ts: number;
  exit_ts: number;
}

const HistoryTab: React.FC = () => {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [sessions, setSessions] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const fetchHistory = async (sid: string) => {
    setLoading(true);
    try {
      const data: Trade[] = await invoke("get_trade_history", { sessionId: sid });
      setTrades(data.sort((a, b) => b.exit_ts - a.exit_ts));
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const init = async () => {
      try {
        const sids: string[] = await invoke("get_trade_sessions");
        setSessions(sids);
        if (sids.length > 0) {
          setSelected(sids[0]);
          fetchHistory(sids[0]);
        } else {
          setLoading(false);
        }
      } catch (e) {
        console.error(e);
        setLoading(false);
      }
    };
    init();
  }, []);

  const fmt = (ts: number) =>
    new Date(ts).toLocaleString('es-ES', {
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });

  const duration = (a: number, b: number) => {
    const s = Math.round((b - a) / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  };

  return (
    <div className="space-y-5">
      {/* Session selector */}
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-gray-500 uppercase tracking-widest font-bold">
          Operaciones cerradas por sesión
        </p>
        <div className="relative">
          <select
            value={selected}
            onChange={(e) => { setSelected(e.target.value); fetchHistory(e.target.value); }}
            className="appearance-none bg-gray-900 border border-gray-700 rounded-lg pl-3 pr-8 py-1.5 text-xs font-semibold text-gray-300 focus:outline-none focus:border-blue-500 cursor-pointer"
          >
            {sessions.map(s => <option key={s} value={s}>{s}</option>)}
            {sessions.length === 0 && <option value="">Sin sesiones</option>}
          </select>
          <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none" />
        </div>
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="border-b border-gray-800">
                {["Activo / Cierre", "Dirección", "Entrada → Salida", "Cantidad", "PnL Neto", "Duración"].map(h => (
                  <th key={h} className="px-5 py-3 text-[10px] font-bold uppercase tracking-wider text-gray-600">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-gray-600 text-sm animate-pulse">
                    Cargando historial...
                  </td>
                </tr>
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-gray-600 text-sm">
                    No hay operaciones registradas en esta sesión.
                  </td>
                </tr>
              ) : trades.map((t, idx) => (
                <tr key={idx} className="hover:bg-gray-800/30 transition-colors group">
                  <td className="px-5 py-4">
                    <span className="font-semibold text-gray-200">{t.symbol}</span>
                    <div className="text-[10px] text-gray-600 font-mono mt-0.5">{fmt(t.exit_ts)}</div>
                  </td>
                  <td className="px-5 py-4">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${
                      t.side.toUpperCase() === 'LONG'
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                        : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                    }`}>
                      {t.side.toUpperCase() === 'LONG' ? 'Largo' : 'Corto'}
                    </span>
                  </td>
                  <td className="px-5 py-4 font-mono text-xs">
                    <span className="text-gray-300">${t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    <span className="text-gray-600 mx-1.5">→</span>
                    <span className="text-gray-400">${t.exit_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                  </td>
                  <td className="px-5 py-4 font-mono text-gray-500 text-xs">{t.qty.toLocaleString()}</td>
                  <td className="px-5 py-4">
                    <span className={`font-mono font-bold text-sm ${t.pnl_net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {t.pnl_net >= 0 ? '+' : ''}${t.pnl_net.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                    <div className="text-[10px] text-gray-700 font-mono opacity-0 group-hover:opacity-100 transition-opacity mt-0.5">
                      Fees: ${t.fees.toFixed(2)}
                    </div>
                  </td>
                  <td className="px-5 py-4">
                    <span className="text-[11px] font-mono text-gray-500">{duration(t.entry_ts, t.exit_ts)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default HistoryTab;
