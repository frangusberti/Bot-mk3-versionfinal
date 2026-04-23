import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { TrendingUp, TrendingDown, DollarSign, Activity, AlertCircle } from 'lucide-react';

interface Position {
  symbol: string;
  side: string;
  qty: number;
  entry_price: number;
  unrealized_pnl: number;
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

  useEffect(() => {
    const fetchOps = async () => {
      try {
        const res: OperationalStatus = await invoke("get_operational_status");
        setData(res);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    fetchOps();
    const t = setInterval(fetchOps, 5000);
    return () => clearInterval(t);
  }, []);

  if (error) {
    return (
      <div className="flex items-start gap-3 bg-rose-500/8 border border-rose-500/20 rounded-xl p-4 text-rose-400 text-sm">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <span>No se pudo conectar con el orquestador. Verifica que el servidor esté activo.</span>
      </div>
    );
  }

  const openPnl = data?.symbols.reduce((acc, p) => acc + p.unrealized_pnl, 0) ?? null;

  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid grid-cols-3 gap-4">
        <KpiCard
          icon={<DollarSign size={15} className="text-gray-500" />}
          label="Patrimonio Total"
          hint="Balance total incluyendo el valor de las posiciones abiertas."
          value={data ? `$${data.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
          valueClass="text-emerald-400"
        />
        <KpiCard
          icon={<Activity size={15} className="text-gray-500" />}
          label="Exposición"
          hint="Fracción del patrimonio comprometida en posiciones abiertas."
          value={data ? `${(data.exposure * 100).toFixed(2)}%` : "—"}
          valueClass="text-blue-400"
        />
        <KpiCard
          icon={openPnl !== null && openPnl >= 0
            ? <TrendingUp size={15} className="text-emerald-500" />
            : <TrendingDown size={15} className="text-rose-500" />}
          label="PnL No Realizado"
          hint="Ganancia o pérdida acumulada en las posiciones actualmente abiertas."
          value={openPnl !== null
            ? `${openPnl >= 0 ? '+' : ''}$${openPnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
            : "—"}
          valueClass={openPnl !== null ? (openPnl >= 0 ? "text-emerald-400" : "text-rose-400") : "text-gray-500"}
        />
      </div>

      {/* Positions table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
          <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">Posiciones Abiertas</span>
          <span className="text-[10px] text-gray-600 font-semibold">Actualiza cada 5s</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="border-b border-gray-800/70">
                {["Activo", "Dirección", "Cantidad", "Precio Entrada", "PnL No Realizado", "Apalancamiento"].map(h => (
                  <th key={h} className="px-5 py-3 text-[10px] font-bold uppercase tracking-wider text-gray-600">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {data?.symbols && data.symbols.length > 0 ? (
                data.symbols.map((pos) => (
                  <tr key={pos.symbol} className="hover:bg-gray-800/30 transition-colors">
                    <td className="px-5 py-4 font-semibold text-gray-200">{pos.symbol}</td>
                    <td className="px-5 py-4">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                        pos.side.toUpperCase() === 'LONG'
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                          : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                      }`}>
                        {pos.side.toUpperCase() === 'LONG' ? 'Largo' : 'Corto'}
                      </span>
                    </td>
                    <td className="px-5 py-4 font-mono text-gray-400">{pos.qty.toLocaleString()}</td>
                    <td className="px-5 py-4 font-mono text-gray-400">${pos.entry_price.toFixed(2)}</td>
                    <td className={`px-5 py-4 font-mono font-bold ${pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className="px-5 py-4 font-mono text-gray-500">{pos.leverage}x</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center text-gray-600 text-sm">
                    No hay posiciones abiertas en este momento.
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

interface KpiCardProps {
  icon: React.ReactNode;
  label: string;
  hint: string;
  value: string;
  valueClass: string;
}

const KpiCard: React.FC<KpiCardProps> = ({ icon, label, hint, value, valueClass }) => (
  <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors" title={hint}>
    <div className="flex items-center gap-2 mb-3">
      {icon}
      <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
    </div>
    <div className={`text-2xl font-bold font-mono tabular-nums ${valueClass}`}>{value}</div>
  </div>
);

export default OperationsTab;
