import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Zap, Waves, BarChart3, CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react';

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

const LEVEL_WIDTH: Record<string, string> = { BAJA: 'w-1/4', MEDIA: 'w-1/2', ALTA: 'w-3/4' };
const LEVEL_COLOR: Record<string, string> = { BAJA: 'bg-emerald-500', MEDIA: 'bg-amber-500', ALTA: 'bg-rose-500' };

const MarketTab: React.FC = () => {
  const [market, setMarket] = useState<MarketData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMarket = async () => {
      try {
        const res: MarketData = await invoke("get_market_status", { symbol: "BTCUSDT" });
        setMarket(res);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    fetchMarket();
    const t = setInterval(fetchMarket, 2000);
    return () => clearInterval(t);
  }, []);

  if (error) {
    return (
      <div className="flex items-start gap-3 bg-rose-500/8 border border-rose-500/20 rounded-xl p-4 text-rose-400 text-sm">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <span>Error de red al consultar el mercado. Reintentando cada 2 segundos...</span>
      </div>
    );
  }

  const condStyle = () => {
    const c = market?.executionCondition;
    if (c === 'FAVORABLE')    return 'text-emerald-400 bg-emerald-400/8 border-emerald-500/25';
    if (c === 'DESFAVORABLE') return 'text-rose-400 bg-rose-400/8 border-rose-500/25';
    return 'text-amber-400 bg-amber-400/8 border-amber-500/25';
  };

  const pressureBuy = market?.pressure === 'COMPRADORA' ? 65
    : market?.pressure === 'VENDEDORA' ? 35
    : 50;
  const pressureSell = 100 - pressureBuy;

  return (
    <div className="space-y-6">
      {/* ── Condición Operativa (hero) ── */}
      <div className={`rounded-xl border p-6 flex items-center justify-between ${condStyle()}`}>
        <div className="flex items-center gap-5">
          <div className="p-3 bg-white/5 rounded-xl">
            {market?.executionCondition === 'FAVORABLE'
              ? <CheckCircle2 size={36} />
              : <AlertTriangle size={36} />}
          </div>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest opacity-60 mb-1">
              Condición Operativa
            </p>
            <h2 className="text-3xl font-black tracking-tight">
              {market?.executionCondition ?? "ESTIMANDO..."}
            </h2>
            <p className="text-[11px] opacity-50 mt-1">
              Evaluación combinada de spread, volatilidad y presión de mercado.
            </p>
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className="text-[11px] font-bold uppercase tracking-widest opacity-50 mb-1">Precio</p>
          <div className="text-4xl font-mono font-black tabular-nums">
            {market ? `$${market.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
          </div>
          {market?.change24h !== undefined && (
            <span className={`text-sm font-bold ${market.change24h >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {market.change24h >= 0 ? '+' : ''}{market.change24h.toFixed(2)}% 24h
            </span>
          )}
        </div>
      </div>

      {/* ── Métricas secundarias ── */}
      <div className="grid grid-cols-3 gap-4">

        {/* Spread */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors">
          <div className="flex items-center gap-2 mb-4">
            <Zap size={15} className="text-gray-500" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Spread (Fricción)</span>
          </div>
          <p className="text-[10px] text-gray-600 mb-3">
            Diferencia entre precio de compra y venta. Menor spread = menor costo por operación.
          </p>
          <div className="text-2xl font-mono font-bold text-gray-100 mb-1">
            {market ? `${(market.spread * 100).toFixed(3)}%` : "—"}
          </div>
          {market && (
            <div className="text-[11px] text-gray-600 font-mono">
              ≈ {(market.price * market.spread).toFixed(2)} USD abs.
            </div>
          )}
        </div>

        {/* Volatilidad */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors">
          <div className="flex items-center gap-2 mb-4">
            <Waves size={15} className="text-gray-500" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Volatilidad</span>
          </div>
          <p className="text-[10px] text-gray-600 mb-3">
            Intensidad de las oscilaciones de precio. Alta volatilidad aumenta el riesgo de slippage.
          </p>
          <div className="text-2xl font-bold text-gray-100 mb-3">
            {market?.volatility ?? "—"}
          </div>
          <div className="w-full bg-gray-800 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-700 rounded-full ${
                market ? LEVEL_COLOR[market.volatility] : 'bg-gray-700'
              } ${market ? LEVEL_WIDTH[market.volatility] : 'w-0'}`}
            />
          </div>
        </div>

        {/* Presión */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={15} className="text-gray-500" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Presión Dominante</span>
          </div>
          <p className="text-[10px] text-gray-600 mb-3">
            Balance entre órdenes compradoras y vendedoras. Indica hacia dónde empuja el mercado.
          </p>
          <div className="text-2xl font-bold text-gray-100 mb-3">
            {market?.pressure ?? "—"}
          </div>
          <div className="flex gap-1">
            <div className="flex-1 h-1.5 bg-rose-900/40 rounded-full overflow-hidden">
              <div
                className="bg-rose-500 h-full transition-all duration-700"
                style={{ width: `${pressureSell}%` }}
              />
            </div>
            <div className="flex-1 h-1.5 bg-emerald-900/40 rounded-full overflow-hidden">
              <div
                className="bg-emerald-500 h-full transition-all duration-700"
                style={{ width: `${pressureBuy}%` }}
              />
            </div>
          </div>
          <div className="flex justify-between mt-1">
            <span className="text-[9px] text-rose-600 font-bold uppercase">Venta</span>
            <span className="text-[9px] text-emerald-600 font-bold uppercase">Compra</span>
          </div>
        </div>

      </div>
    </div>
  );
};

export default MarketTab;
