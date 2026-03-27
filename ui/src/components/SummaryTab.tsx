import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";

interface SystemStatus {
  recording_active: boolean;
  eps: number;
  in_sync: boolean;
  symbol: string;
}

const SummaryTab: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    try {
      const res: SystemStatus = await invoke("get_system_status");
      setStatus(res);
      setError(null);
    } catch (e) {
      setError(String(e));
      setStatus(null);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/50 hover:border-slate-600 transition-colors">
          <span className="text-slate-500 text-xs font-bold uppercase tracking-wider">Fidelidad de Captura</span>
          <div className={`text-2xl font-bold mt-1 ${status?.recording_active ? 'text-emerald-400' : 'text-slate-500'}`}>
            {status ? (status.recording_active ? 'GRABANDO' : 'DETENIDO') : '...'}
          </div>
          {status?.recording_active && (
            <div className="text-[10px] text-emerald-500/70 font-bold mt-1 uppercase tracking-tighter">
              Flujo de datos optimizado
            </div>
          )}
        </div>

        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/50">
          <span className="text-slate-500 text-xs font-bold uppercase tracking-wider">Pulsaciones (Frecuencia)</span>
          <div className="text-2xl font-bold mt-1 text-blue-400">
            {status ? `${status.eps} ` : '-- '}
            <span className="text-sm text-slate-500">ev/s</span>
          </div>
          <div className="text-[10px] text-slate-500 font-bold mt-1 uppercase tracking-tighter">
             Latido en tiempo real
          </div>
        </div>

        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/50">
          <span className="text-slate-500 text-xs font-bold uppercase tracking-wider">Estado de Sincronía</span>
          <div className={`text-2xl font-bold mt-1 ${status ? (status.marketDataInSync ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-600'}`}>
            {!status ? 'ESPERANDO...' : (status.marketDataInSync ? 'SINCRONIZADO' : 'ERROR')}
          </div>
        </div>

        <div className="bg-slate-800/40 p-6 rounded-2xl border border-slate-700/50">
          <span className="text-slate-500 text-xs font-bold uppercase tracking-wider">Activo Actual</span>
          <div className="text-2xl font-bold mt-1 text-slate-100">{status?.symbol || 'N/A'}</div>
        </div>
      </div>

      {error && (
        <div className="bg-rose-500/10 border border-rose-500/20 p-4 rounded-xl text-rose-500 text-xs">
          <div className="flex justify-between items-center mb-1">
             <span className="font-bold">⚠️ PROBLEMA DE CONEXIÓN CON EL BOT</span>
             <button className="text-[10px] underline uppercase tracking-widest opacity-60 hover:opacity-100">Ver detalles</button>
          </div>
          <span>No puedo obtener datos del servidor. Asegúrese de que el motor de ejecución esté activo.</span>
        </div>
      )}

      <div className="bg-slate-800/20 h-96 rounded-2xl border border-slate-800 flex items-center justify-center border-dashed relative group overflow-hidden">
        <div className="absolute inset-0 bg-blue-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
        <span className="text-slate-600 font-medium group-hover:text-slate-400 transition-colors">Visualizador de Mercado (Sprint 4)</span>
      </div>
    </div>
  );
};

export default SummaryTab;
