import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { History, Search, Download, Filter } from 'lucide-react';

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
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const initData = async () => {
    try {
      const sids: string[] = await invoke("get_trade_sessions");
      setSessions(sids);
      if (sids.length > 0) {
        setSelectedSession(sids[0]);
        fetchHistory(sids[0]);
      } else {
        setLoading(false);
      }
    } catch (e) {
      console.error(e);
      setLoading(false);
    }
  };

  const fetchHistory = async (sid: string) => {
    setLoading(true);
    try {
      const data: Trade[] = await invoke("get_trade_history", { sessionId: sid });
      // Sort by exit_ts descending
      setTrades(data.sort((a, b) => b.exit_ts - a.exit_ts));
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    initData();
  }, []);

  const formatDate = (ts: number) => {
    return new Date(ts).toLocaleString('es-ES', { 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      day: '2-digit',
      month: 'short'
    });
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-700">
      
      {/* Header & Session Selector */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900/40 p-6 rounded-3xl border border-slate-800">
        <div className="flex items-center space-x-4 text-slate-400">
          <History size={20} />
          <div>
            <h2 className="text-xl font-bold text-slate-100 italic">Auditoría Operativa</h2>
            <p className="text-[10px] font-bold uppercase tracking-widest opacity-50">Historial de Round-Trips</p>
          </div>
        </div>
        
        <div className="flex items-center space-x-3">
          <div className="relative group">
            <select 
              value={selectedSession}
              onChange={(e) => {
                setSelectedSession(e.target.value);
                fetchHistory(e.target.value);
              }}
              className="appearance-none bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 pr-10 text-xs font-bold text-slate-300 focus:outline-none focus:border-blue-500 transition-all cursor-pointer hover:bg-slate-900"
            >
              {sessions.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
              {sessions.length === 0 && <option value="">Sin sesiones activas</option>}
            </select>
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none opacity-50 group-hover:opacity-100 transition-opacity">
              <Filter size={14} />
            </div>
          </div>
        </div>
      </div>

      {/* Trades Table */}
      <div className="bg-slate-900/40 rounded-3xl border border-slate-800 overflow-hidden shadow-2xl">
        <div className="overflow-x-auto custom-scrollbar">
          <table className="w-full text-left">
            <thead>
              <tr className="bg-slate-950/50 border-b border-slate-800">
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500">Activo</th>
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500">Lado</th>
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500">Entrada / Salida</th>
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500 text-right">Cantidad</th>
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500 text-right">PnL Neto</th>
                <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-slate-500 text-right">Tiempo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-600 italic animate-pulse">
                    Consultando registros de auditoría...
                  </td>
                </tr>
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-600 italic">
                    No se han registrado operaciones en esta sesión.
                  </td>
                </tr>
              ) : trades.map((t, idx) => (
                <tr key={idx} className="hover:bg-slate-800/30 transition-colors group">
                  <td className="px-6 py-5">
                    <div className="flex flex-col">
                      <span className="font-bold text-slate-200">{t.symbol}</span>
                      <span className="text-[10px] text-slate-500 font-mono">{formatDate(t.exit_ts)}</span>
                    </div>
                  </td>
                  <td className="px-6 py-5">
                    <span className={`px-2 py-1 rounded-md text-[10px] font-bold uppercase ${t.side.toUpperCase() === 'LONG' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-rose-500/10 text-rose-500'}`}>
                      {t.side.toUpperCase() === 'LONG' ? 'Largo' : 'Corto'}
                    </span>
                  </td>
                  <td className="px-6 py-5">
                    <div className="flex flex-col font-mono text-xs">
                      <span className="text-slate-300">${t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                      <span className="text-slate-500 text-[10px] mt-0.5">➔ ${t.exit_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    </div>
                  </td>
                  <td className="px-6 py-5 text-right font-mono text-xs text-slate-400">
                    {t.qty.toLocaleString()}
                  </td>
                  <td className="px-6 py-5 text-right font-mono text-sm">
                    <span className={`font-bold ${t.pnl_net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {t.pnl_net >= 0 ? '+' : ''}{t.pnl_net.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                    <div className="text-[9px] text-slate-500 font-bold opacity-0 group-hover:opacity-100 transition-opacity">
                      Fees: ${t.fees.toFixed(2)}
                    </div>
                  </td>
                  <td className="px-6 py-5 text-right">
                    <span className="text-[10px] font-bold text-slate-600 bg-slate-800/50 px-2 py-1 rounded-full">
                      {Math.round((t.exit_ts - t.entry_ts) / 1000)}s
                    </span>
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
