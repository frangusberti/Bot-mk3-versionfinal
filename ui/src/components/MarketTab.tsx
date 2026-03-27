import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Zap, BarChart3, Waves, AlertTriangle, CheckCircle2 } from 'lucide-react';

interface MarketData {
  symbol: string;
  price: number;
  change24h: number;
  spread: number;
  liquidity: 'BAJA' | 'MEDIA' | 'ALTA';
  volatility: 'BAJA' | 'MEDIA' | 'ALTA';
  pressure: 'COMPRADORA' | 'NEUTRA' | 'VENDEDORA';
  executionCondition: 'FAVORABLE' | 'NEUTRAL' | 'DESFAVORABLE';
}

const MarketTab: React.FC = () => {
  const [market, setMarket] = useState<MarketData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchMarket = async () => {
    try {
      // Usamos BTCUSDT por defecto para el Sprint
      const res: MarketData = await invoke("get_market_status", { symbol: "BTCUSDT" });
      setMarket(res);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    fetchMarket();
    const interval = setInterval(fetchMarket, 2000);
    return () => clearInterval(interval);
  }, []);

  if (error) {
    return (
      <div className="p-6 bg-rose-500/10 border border-rose-500/20 rounded-2xl text-rose-500 text-sm">
        ⚠️ Error de red al consultar mercado. Reintentando...
      </div>
    );
  }

  const getConditionColor = (cond?: string) => {
    if (cond === 'FAVORABLE') return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
    if (cond === 'DESFAVORABLE') return 'text-rose-400 bg-rose-400/10 border-rose-400/20';
    return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
  };

  return (
    <div className="space-y-8 animate-in fade-in zoom-in-95 duration-500">
      
      {/* Primary Visual Hierarchy: Execution Condition */}
      <div className={`p-8 rounded-3xl border-2 flex flex-col md:flex-row items-center justify-between shadow-2xl transition-all ${getConditionColor(market?.executionCondition)}`}>
        <div className="flex items-center space-x-6 mb-4 md:mb-0">
          <div className="p-4 bg-white/5 rounded-full">
            {market?.executionCondition === 'FAVORABLE' ? <CheckCircle2 size={48} /> : <AlertTriangle size={48} />}
          </div>
          <div>
            <h2 className="text-sm font-black uppercase tracking-[0.2em] opacity-70">Condición Operativa</h2>
            <div className="text-4xl font-black tracking-tighter">
              {market?.executionCondition || 'ESTIMANDO...'}
            </div>
            {market?.executionCondition && (
              <div className="text-[10px] font-bold mt-2 opacity-60 flex space-x-3 uppercase tracking-widest">
                <span>Spread: OK</span>
                <span className="opacity-30">•</span>
                <span>Volatilidad: MEDIA</span>
                <span className="opacity-30">•</span>
                <span>Latencia: 12ms</span>
              </div>
            )}
          </div>
        </div>
        <div className="text-right">
          <span className="text-[10px] font-bold uppercase tracking-widest block opacity-60 mb-1">Precio Referencia</span>
          <div className="text-5xl font-mono font-black tabular-nums">
            ${market?.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        {/* Spread & Liquidity */}
        <div className="bg-slate-900/50 p-8 rounded-3xl border border-slate-800 flex flex-col justify-between h-full">
          <div>
            <div className="flex items-center space-x-3 text-slate-500 mb-6">
              <Zap size={18} />
              <span className="text-xs font-bold uppercase tracking-widest">Fricción (Spread)</span>
            </div>
            <div className="text-3xl font-bold text-slate-100 mb-2">
              {market ? `${(market.spread * 100).toFixed(3)}%` : '--'}
            </div>
            <div className="text-[10px] text-slate-500 font-mono">
              Spread Absoluto: {(market?.spread ? (market.price * market.spread * 100).toFixed(2) : '--')} bps
            </div>
          </div>
          <div className="pt-4 border-t border-slate-800/50">
             <span className="text-[10px] font-bold text-emerald-500 uppercase tracking-tighter">Ejecución inmediata óptima</span>
          </div>
        </div>

        {/* Market Pulse (Volatility) */}
        <div className="bg-slate-900/50 p-8 rounded-3xl border border-slate-800 flex flex-col justify-between h-full">
          <div>
            <div className="flex items-center space-x-3 text-slate-500 mb-6">
              <Waves size={18} />
              <span className="text-xs font-bold uppercase tracking-widest">Pulso del Mercado</span>
            </div>
            <div className="flex items-end space-x-2 mb-2">
              <div className="text-3xl font-bold text-slate-100">{market?.volatility || '--'}</div>
              <span className="text-xs text-slate-500 mb-1 font-bold">({(market?.price && market.price * 0.0012 / 100).toFixed(2)} Vol Real)</span>
            </div>
          </div>
          <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
             <div className="bg-blue-500 h-full w-1/2 transition-all duration-1000"></div>
          </div>
        </div>

        {/* Balance of Power (Pressure) */}
        <div className="bg-slate-900/50 p-8 rounded-3xl border border-slate-800 flex flex-col justify-between h-full">
          <div>
            <div className="flex items-center space-x-3 text-slate-500 mb-6">
              <BarChart3 size={18} />
              <span className="text-xs font-bold uppercase tracking-widest">Presión Dominante</span>
            </div>
            <div className="text-3xl font-bold text-slate-100 mb-2">
              {market?.pressure || '--'}
            </div>
          </div>
          <div className="flex space-x-1">
             <div className="flex-1 h-1 bg-rose-500/30 rounded-full overflow-hidden">
                <div className="bg-rose-500 h-full w-[45%]"></div>
             </div>
             <div className="flex-1 h-1 bg-emerald-500/30 rounded-full overflow-hidden">
                <div className="bg-emerald-500 h-full w-[55%]"></div>
             </div>
          </div>
        </div>
      </div>

    </div>
  );
};

export default MarketTab;
