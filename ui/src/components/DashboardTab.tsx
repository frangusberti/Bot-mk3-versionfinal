import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Radio, Wifi, Activity, AlertCircle } from 'lucide-react';

interface SystemStatus {
  recording_active: boolean;
  eps: number;
  marketDataInSync: boolean;
  symbol: string;
}

interface OperationalStatus {
  state: string;
  mode: string;
  equity: number;
  exposure: number;
  symbols: unknown[];
}

interface MarketData {
  symbol: string;
  price: number;
  change24h: number;
  executionCondition: string;
}

const DashboardTab: React.FC = () => {
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [ops, setOps] = useState<OperationalStatus | null>(null);
  const [market, setMarket] = useState<MarketData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = async () => {
    try {
      const [s, o, m] = await Promise.all([
        invoke<SystemStatus>("get_system_status"),
        invoke<OperationalStatus>("get_operational_status"),
        invoke<MarketData>("get_market_status", { symbol: "BTCUSDT" }),
      ]);
      setSys(s);
      setOps(o);
      setMarket(m);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    poll();
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, []);

  const conditionStyle = (c?: string) => {
    if (c === 'FAVORABLE')    return { text: 'text-emerald-400', dot: 'bg-emerald-500' };
    if (c === 'DESFAVORABLE') return { text: 'text-rose-400',    dot: 'bg-rose-500' };
    return                           { text: 'text-amber-400',   dot: 'bg-amber-500' };
  };

  const cond = conditionStyle(market?.executionCondition);

  return (
    <div className="space-y-8">
      {error && (
        <div className="flex items-start gap-3 bg-rose-500/8 border border-rose-500/20 rounded-xl p-4 text-rose-400 text-sm">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <div>
            <strong className="font-semibold">Backend no disponible.</strong>
            {' '}Verifica que el servidor esté activo en el puerto 50051 y vuelve a intentarlo.
          </div>
        </div>
      )}

      {/* ── Estado del Sistema ── */}
      <section>
        <SectionTitle>Estado del Sistema</SectionTitle>
        <div className="grid grid-cols-3 gap-4">
          <StatCard
            icon={<Radio size={15} className={sys?.recording_active ? "text-emerald-500" : "text-gray-600"} />}
            label="Captura de Datos"
            hint="Indica si el bot está registrando el order book en tiempo real."
            value={!sys ? "—" : sys.recording_active ? "GRABANDO" : "DETENIDO"}
            valueClass={sys?.recording_active ? "text-emerald-400" : "text-gray-500"}
          />
          <StatCard
            icon={<Activity size={15} className="text-blue-400" />}
            label="Frecuencia de Eventos"
            hint="Cuántos eventos de mercado procesa el bot por segundo."
            value={sys ? `${sys.eps} ev/s` : "—"}
            valueClass="text-blue-400"
            mono
          />
          <StatCard
            icon={<Wifi size={15} className={!sys ? "text-gray-600" : sys.marketDataInSync ? "text-emerald-500" : "text-rose-500"} />}
            label="Feed de Mercado"
            hint="Sincronía entre el bot y el exchange. Si falla, los datos de precio pueden estar desactualizados."
            value={!sys ? "—" : sys.marketDataInSync ? "SINCRONIZADO" : "DESCONECTADO"}
            valueClass={sys?.marketDataInSync ? "text-emerald-400" : "text-rose-400"}
          />
        </div>
      </section>

      {/* ── Mercado Activo ── */}
      <section>
        <SectionTitle>Mercado Activo</SectionTitle>
        <div className="grid grid-cols-3 gap-4">
          <StatCard
            label="Par de Trading"
            hint="Activo que está monitoreando el bot en esta sesión."
            value={market?.symbol || "—"}
            valueClass="text-white"
          />
          <StatCard
            label="Precio"
            hint="Último precio de referencia recibido del exchange."
            value={market ? `$${market.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
            valueClass="text-white"
            mono
          />
          <StatCard
            label="Condición Operativa"
            hint="Evaluación del bot sobre las condiciones actuales del mercado para ejecutar órdenes."
            value={
              <span className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${cond.dot} animate-pulse`} />
                {market?.executionCondition || "—"}
              </span>
            }
            valueClass={cond.text}
          />
        </div>
      </section>

      {/* ── Cartera ── */}
      <section>
        <SectionTitle>Cartera</SectionTitle>
        <div className="grid grid-cols-3 gap-4">
          <StatCard
            label="Patrimonio Total"
            hint="Balance total de la cuenta, incluyendo posiciones abiertas."
            value={ops ? `$${ops.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
            valueClass="text-emerald-400"
            mono
          />
          <StatCard
            label="Exposición"
            hint="Porcentaje del patrimonio comprometido actualmente en posiciones abiertas."
            value={ops ? `${(ops.exposure * 100).toFixed(1)}%` : "—"}
            valueClass="text-blue-400"
            mono
          />
          <StatCard
            label="Posiciones Abiertas"
            hint="Número de trades activos en este momento."
            value={ops ? String(ops.symbols.length) : "—"}
            valueClass="text-white"
          />
        </div>
      </section>
    </div>
  );
};

/* ── Sub-components ── */

const SectionTitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h2 className="text-[11px] font-bold text-gray-500 uppercase tracking-widest mb-3">{children}</h2>
);

interface StatCardProps {
  icon?: React.ReactNode;
  label: string;
  hint: string;
  value: React.ReactNode;
  valueClass: string;
  mono?: boolean;
}

const StatCard: React.FC<StatCardProps> = ({ icon, label, hint, value, valueClass, mono }) => (
  <div
    className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-colors group"
    title={hint}
  >
    <div className="flex items-center gap-2 mb-3">
      {icon}
      <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
    </div>
    <div className={`text-xl font-bold ${valueClass} ${mono ? 'font-mono tabular-nums' : ''}`}>
      {value}
    </div>
    <p className="text-[10px] text-gray-700 mt-1.5 leading-relaxed opacity-0 group-hover:opacity-100 transition-opacity">{hint}</p>
  </div>
);

export default DashboardTab;
