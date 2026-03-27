import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react';

interface Position {
  symbol: string;
  side: string;
  qty: number;
  entry_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  leverage: number;
}

interface OperationalStatus {
  state: string;
  mode: string;
  equity: number;
  cash: number;
  exposure: number;
  symbols: Position[];
}

const OperationsTab: React.FC = () => {
  const [data, setData] = useState<OperationalStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchOps = async () => {
    try {
      const res: OperationalStatus = await invoke("get_operational_status");
      setData(res);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    fetchOps();
    const interval = setInterval(fetchOps, 5000);
    return () => clearInterval(interval);
  }, []);

  if (error) {
    return (
      <div className="p-6 bg-rose-500/10 border border-rose-500/20 rounded-2xl text-rose-500">
        <h3 className="font-bold mb-2">Error de comunicación</h3>
        <p className="text-sm opacity-80">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
      {/* Portfolio Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 shadow-xl">
          <div className="flex items-center space-x-3 mb-4 opacity-50">
            <DollarSign size={16} />
            <span className="text-xs font-bold uppercase tracking-widest">Patrimonio Total</span>
          </div>
          <div className="text-3xl font-bold text-slate-100">
            ${data?.equity?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '0.00'}
          </div>
        </div>

        <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 shadow-xl">
          <div className="flex items-center space-x-3 mb-4 opacity-50">
            <Activity size={16} />
            <span className="text-xs font-bold uppercase tracking-widest">Exposición Actual</span>
          </div>
          <div className="text-3xl font-bold text-blue-400">
             {data ? `${(data.exposure * 100).toFixed(2)}%` : '--'}
          </div>
        </div>

        <div className="bg-slate-900 p-6 rounded-2xl border border-slate-800 shadow-xl">
          <div className="flex items-center space-x-3 mb-4 opacity-50">
            <TrendingUp size={16} />
            <span className="text-xs font-bold uppercase tracking-widest">Estado Misión</span>
          </div>
          <div className="text-3xl font-bold text-emerald-400 uppercase">
             {data?.state === 'RUNNING' ? 'ACTIVA' : 'DETENIDA'}
          </div>
        </div>
      </div>

      {/* Active Positions Table */}
      <div className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-2xl">
        <div className="px-6 py-4 border-b border-slate-800 bg-slate-800/20 flex justify-between items-center">
           <h3 className="font-bold text-slate-400 text-sm uppercase tracking-widest">Posiciones Abiertas</h3>
           <span className="text-[10px] bg-slate-800 px-2 py-1 rounded text-slate-500 font-bold">LECTURA EN VIVO</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-slate-500 border-b border-slate-800">
                <th className="px-6 py-4 font-bold uppercase text-[10px]">Activo</th>
                <th className="px-6 py-4 font-bold uppercase text-[10px]">Dirección</th>
                <th className="px-6 py-4 font-bold uppercase text-[10px]">Cantidad</th>
                <th className="px-6 py-4 font-bold uppercase text-[10px]">Precio Entrada</th>
                <th className="px-6 py-4 font-bold uppercase text-[10px]">PnL No Realizado</th>
                <th className="px-6 py-4 font-bold uppercase text-[10px]">Apalancamiento</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {data?.symbols && data.symbols.length > 0 ? (
                data.symbols.map((pos) => (
                  <tr key={pos.symbol} className="hover:bg-slate-800/30 transition-colors group">
                    <td className="px-6 py-4 font-bold text-slate-200">{pos.symbol}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 rounded-md text-[10px] font-bold uppercase ${pos.side.toUpperCase() === 'LONG' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-rose-500/10 text-rose-500'}`}>
                        {pos.side.toUpperCase() === 'LONG' ? 'Largo' : 'Corto'}
                      </span>
                    </td>
                    <td className="px-6 py-4 font-mono text-slate-400">{pos.qty}</td>
                    <td className="px-6 py-4 font-mono text-slate-400">${pos.entry_price.toFixed(2)}</td>
                    <td className={`px-6 py-4 font-bold flex items-center space-x-2 ${pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {pos.unrealized_pnl >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                      <span>${pos.unrealized_pnl.toFixed(2)}</span>
                    </td>
                    <td className="px-6 py-4 font-mono text-slate-500">{pos.leverage}x</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-600 font-medium italic">
                    Sin posiciones abiertas en este momento
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default OperationsTab;
