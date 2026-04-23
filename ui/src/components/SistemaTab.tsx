import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Shield, Cpu, Database, Zap, Activity, Lock } from 'lucide-react';

interface SettingsData {
  basic: {
    mode: string;
    risk_level: string;
    symbols: string[];
  };
  advanced: {
    grpc_terminal: string;
    log_level: string;
    adaptive_risk: boolean;
  };
}

const SistemaTab: React.FC = () => {
  const [cfg, setCfg] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    invoke<SettingsData>("get_settings")
      .then(d => { setCfg(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-gray-600 text-sm animate-pulse p-4">Cargando configuración...</p>;
  }

  return (
    <div className="space-y-8">

      {/* ── Operación ── */}
      <section>
        <SectionTitle icon={<Shield size={14} className="text-emerald-500" />}>
          Operación
        </SectionTitle>
        <div className="grid grid-cols-3 gap-4">
          <ConfigCard
            label="Modo de Ejecución"
            hint="PAPER = simulación sin dinero real. LIVE = órdenes reales en el exchange."
          >
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${cfg?.basic.mode === 'LIVE' ? 'bg-rose-500 animate-pulse' : 'bg-amber-500'}`} />
              <span className="font-bold text-white text-base">{cfg?.basic.mode ?? "—"}</span>
              <Lock size={13} className="text-gray-700 ml-1" title="Solo modificable desde configuración del servidor" />
            </div>
          </ConfigCard>

          <ConfigCard
            label="Perfil de Riesgo"
            hint="Determina el tamaño máximo de posición y los umbrales de stop-loss automáticos."
          >
            <span className="px-2 py-0.5 rounded text-xs font-bold uppercase border bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
              {cfg?.basic.risk_level ?? "—"}
            </span>
          </ConfigCard>

          <ConfigCard
            label="Activos Monitoreados"
            hint="Pares de trading que el bot puede operar en esta sesión."
          >
            <div className="flex flex-wrap gap-1.5">
              {cfg?.basic.symbols.map(s => (
                <span key={s} className="px-2 py-0.5 bg-gray-800 border border-gray-700 rounded font-mono text-xs text-gray-300">{s}</span>
              )) ?? <span className="text-gray-600">—</span>}
            </div>
          </ConfigCard>
        </div>
      </section>

      {/* ── Técnico ── */}
      <section>
        <SectionTitle icon={<Cpu size={14} className="text-blue-500" />}>
          Configuración Técnica
        </SectionTitle>
        <div className="bg-gray-900 border border-gray-800 rounded-xl divide-y divide-gray-800/70">
          <Row label="Terminal gRPC" hint="Dirección del servidor de control. Debe estar activo para que la GUI funcione.">
            <code className="text-blue-400 font-mono text-sm bg-blue-500/8 px-2 py-0.5 rounded border border-blue-500/15">
              {cfg?.advanced.grpc_terminal ?? "—"}
            </code>
          </Row>
          <Row label="Nivel de Logging" hint="Verbosidad de los logs del servidor. DEBUG genera más detalle pero más volumen.">
            <span className="font-mono text-sm text-gray-300">{cfg?.advanced.log_level ?? "—"}</span>
          </Row>
          <Row label="Adaptive Risk Engine" hint="Si está activo, el bot ajusta dinámicamente el tamaño de posición según la volatilidad reciente.">
            <span className={`px-2 py-0.5 rounded text-[11px] font-bold uppercase border ${
              cfg?.advanced.adaptive_risk
                ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                : 'bg-gray-800 text-gray-500 border-gray-700'
            }`}>
              {cfg?.advanced.adaptive_risk ? 'Habilitado' : 'Deshabilitado'}
            </span>
          </Row>
        </div>
        <p className="text-[10px] text-gray-700 mt-2 px-1">
          Esta configuración solo puede modificarse directamente en el servidor. La GUI es de solo lectura.
        </p>
      </section>

      {/* ── Diagnóstico / Herramientas ── */}
      <section>
        <SectionTitle icon={<Activity size={14} className="text-orange-500" />}>
          Herramientas de Diagnóstico
        </SectionTitle>
        <div className="grid grid-cols-3 gap-4">
          <ToolCard
            icon={<Database size={15} className="text-blue-400" />}
            title="Dataset Builder"
            description="Genera vectores de entrenamiento HDF5 a partir de capturas crudas del order book."
            available={false}
          />
          <ToolCard
            icon={<Zap size={15} className="text-emerald-400" />}
            title="Model Inspector"
            description="Visualiza pesos, logits y entropía de la política activa del modelo RL."
            available={false}
          />
          <ToolCard
            icon={<Activity size={15} className="text-orange-400" />}
            title="gRPC Sniffer"
            description="Monitoriza los mensajes crudos entre el backend y los modelos en tiempo real."
            available={false}
          />
        </div>
        <p className="text-[10px] text-gray-700 mt-3 px-1">
          Estas herramientas están pendientes de implementación en próximas versiones.
        </p>
      </section>

    </div>
  );
};

/* ── Sub-components ── */

const SectionTitle: React.FC<{ icon?: React.ReactNode; children: React.ReactNode }> = ({ icon, children }) => (
  <div className="flex items-center gap-2 mb-4">
    {icon}
    <h2 className="text-[11px] font-bold text-gray-500 uppercase tracking-widest">{children}</h2>
    <div className="flex-1 h-px bg-gray-800/60" />
  </div>
);

const ConfigCard: React.FC<{ label: string; hint: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-colors" title={hint}>
    <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-3">{label}</p>
    {children}
  </div>
);

const Row: React.FC<{ label: string; hint: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div className="flex items-center justify-between px-5 py-3.5" title={hint}>
    <span className="text-sm text-gray-400 font-medium">{label}</span>
    {children}
  </div>
);

interface ToolCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  available: boolean;
}

const ToolCard: React.FC<ToolCardProps> = ({ icon, title, description, available }) => (
  <div className={`bg-gray-900 border rounded-xl p-4 transition-colors ${
    available ? 'border-gray-700 hover:border-gray-600 cursor-pointer' : 'border-gray-800/50 opacity-40'
  }`}>
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-sm font-semibold text-gray-300">{title}</span>
      </div>
      {!available && (
        <span className="text-[9px] font-bold uppercase text-gray-600 border border-gray-700 px-1.5 py-0.5 rounded tracking-wider">
          Pendiente
        </span>
      )}
    </div>
    <p className="text-[11px] text-gray-600 leading-relaxed">{description}</p>
  </div>
);

export default SistemaTab;
