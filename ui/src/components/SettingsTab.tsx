import React, { useEffect, useState } from 'react';
import { invoke } from "@tauri-apps/api/core";
import { Settings, Shield, Cpu, Cloud, ToggleLeft as Toggle, Sliders, Lock } from 'lucide-react';

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

const SettingsTab: React.FC = () => {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data: SettingsData = await invoke("get_settings");
        setSettings(data);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    fetchSettings();
  }, []);

  if (loading) return <div className="p-12 text-center text-slate-500 italic animate-pulse">Cargando configuración del sistema...</div>;

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      
      {/* Layer 1: Nivel Operador (Básico) */}
      <section>
        <div className="flex items-center space-x-3 mb-8">
          <Shield className="text-emerald-500" size={20} />
          <h2 className="text-xl font-bold tracking-tight text-slate-100">Nivel Operador (Básico)</h2>
          <div className="h-px flex-1 bg-slate-800 ml-4"></div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-slate-900/40 p-8 rounded-3xl border border-slate-800 hover:border-slate-700 transition-colors">
            <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-6">Configuración de Riesgo</h3>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-bold text-slate-300">Perfil Activo</span>
              <span className="px-3 py-1 bg-emerald-500/10 text-emerald-500 rounded-lg text-[10px] font-black uppercase tracking-widest border border-emerald-500/20">
                {settings?.basic.risk_level}
              </span>
            </div>
            <p className="text-xs text-slate-600 italic">El perfil moderado equilibra la captura de liquidez con la protección ante gaps de volatilidad.</p>
          </div>

          <div className="bg-slate-900/40 p-8 rounded-3xl border border-slate-800 hover:border-slate-700 transition-colors">
            <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-6">Modo de Ejecución</h3>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center space-x-3">
                <div className={`w-2 h-2 rounded-full ${settings?.basic.mode === 'LIVE' ? 'bg-rose-500' : 'bg-emerald-500'}`}></div>
                <span className="text-sm font-bold text-slate-100">{settings?.basic.mode}</span>
              </div>
              <Lock size={16} className="text-slate-700" title="Control bloqueado en modo visor" />
            </div>
            <div className="flex flex-wrap gap-2">
              {settings?.basic.symbols.map(s => (
                <span key={s} className="px-2 py-1 bg-slate-800 rounded font-mono text-[10px] text-slate-400">{s}</span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Layer 2: Nivel Avanzado (Técnico) */}
      <section>
        <div className="flex items-center space-x-3 mb-8 opacity-70">
          <Cpu className="text-blue-500" size={20} />
          <h2 className="text-xl font-bold tracking-tight text-slate-100 italic">Nivel Avanzado (Técnico)</h2>
          <div className="h-px flex-1 bg-slate-800 ml-4"></div>
        </div>

        <div className="space-y-4">
          <div className="bg-slate-900/20 p-6 rounded-2xl border border-dashed border-slate-800 grid grid-cols-1 md:grid-cols-3 gap-8">
            <div className="space-y-1">
              <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block">Terminal gRPC</span>
              <code className="text-xs text-blue-400 font-mono bg-blue-500/5 px-2 py-1 rounded inline-block">
                {settings?.advanced.grpc_terminal}
              </code>
            </div>
            <div className="space-y-1">
              <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block">Logging Level</span>
              <span className="text-xs font-bold text-slate-300">{settings?.advanced.log_level}</span>
            </div>
            <div className="space-y-2">
              <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest block">Adaptive Risk Engine</span>
              <div className="flex items-center space-x-2">
                <Toggle className={settings?.advanced.adaptive_risk ? "text-blue-500" : "text-slate-700"} size={20} />
                <span className="text-[10px] font-black uppercase tracking-tighter opacity-70">
                  {settings?.advanced.adaptive_risk ? 'Habilitado' : 'Deshabilitado'}
                </span>
              </div>
            </div>
          </div>
          <p className="text-[10px] text-slate-700 uppercase tracking-widest text-center italic">
            Configuración sensible. Modificar solo en entornos de Laboratorio controlados.
          </p>
        </div>
      </section>

    </div>
  );
};

export default SettingsTab;
