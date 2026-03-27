import React from 'react';
import { Beaker, Database, LineChart, Binary, Zap, Search, Activity } from 'lucide-react';

const LabTab: React.FC = () => {
  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      
      {/* Header */}
      <div className="flex items-center space-x-4 mb-8">
        <div className="bg-blue-500/10 p-3 rounded-2xl border border-blue-500/20">
          <Beaker className="text-blue-500" size={24} />
        </div>
        <div className="flex-1">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-bold tracking-tight text-slate-100 italic">Laboratorio de I+D</h2>
            <span className="px-3 py-1 bg-blue-500/10 text-blue-500 rounded-lg text-[10px] font-black uppercase tracking-[0.2em] border border-blue-500/20">
              MODO LECTURA PROFUNDA
            </span>
          </div>
          <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Entorno Experimental Controlado</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Domain: Data Factory */}
        <div className="space-y-6">
          <div className="flex items-center space-x-2 px-2">
            <Database size={16} className="text-blue-400" />
            <span className="text-xs font-black uppercase tracking-widest text-slate-300">Data Factory</span>
          </div>
          <div className="bg-slate-900/40 rounded-3xl border border-slate-800 p-6 space-y-4 hover:border-blue-500/30 transition-colors">
            <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-blue-500/5 transition-all">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200 group-hover:text-blue-400">Dataset Builder</span>
                <Binary size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500 leading-relaxed">Generación de vectores HDF5 a partir de capturas L2 crudas.</p>
            </div>
            <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-blue-500/5 transition-all opacity-50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200">Quality Profiler</span>
                <Search size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500">Análisis forense de gaps y latencia en el dataset.</p>
            </div>
          </div>
        </div>

        {/* Domain: Policy Lab */}
        <div className="space-y-6">
          <div className="flex items-center space-x-2 px-2">
            <Zap size={16} className="text-emerald-400" />
            <span className="text-xs font-black uppercase tracking-widest text-slate-300">Policy Lab</span>
          </div>
          <div className="bg-slate-900/40 rounded-3xl border border-slate-800 p-6 space-y-4 hover:border-emerald-500/30 transition-colors">
             <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-emerald-500/5 transition-all">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200 group-hover:text-emerald-400">Model Inspector</span>
                <LineChart size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500 leading-relaxed">Visualización de pesos, logits y entropía de la política activa.</p>
            </div>
            <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-emerald-500/5 transition-all opacity-50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200">Hyper-Params</span>
                <Sliders size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500">Ajuste fino de recompensas y penalizaciones RL.</p>
            </div>
          </div>
        </div>

        {/* Domain: Audit Deep-Dive */}
        <div className="space-y-6">
          <div className="flex items-center space-x-2 px-2">
            <Activity size={16} className="text-orange-400" />
            <span className="text-xs font-black uppercase tracking-widest text-slate-300">Audit Deep-Dive</span>
          </div>
          <div className="bg-slate-900/40 rounded-3xl border border-slate-800 p-6 space-y-4 hover:border-orange-500/30 transition-colors">
            <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-orange-500/5 transition-all">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200 group-hover:text-orange-400">gRPC Sniffer</span>
                <Binary size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500 leading-relaxed">Monitorización de mensajes crudos entre backend y modelos.</p>
            </div>
            <div className="p-4 bg-slate-950/50 rounded-2xl border border-slate-800 group cursor-pointer hover:bg-orange-500/5 transition-all opacity-50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-200">Crash Dumps</span>
                <Beaker size={14} className="text-slate-600" />
              </div>
              <p className="text-[10px] text-slate-500 leading-relaxed">Análisis de estados post-mortem en fallos críticos.</p>
            </div>
          </div>
        </div>

      </div>

      <div className="mt-12 p-6 bg-blue-500/5 rounded-3xl border border-blue-500/10 text-center">
        <p className="text-xs text-blue-500/70 italic font-medium">
          "El Laboratorio es un entorno de solo lectura para diagnóstico profundo. Las herramientas de escritura requieren autorización de Nivel 4."
        </p>
      </div>

    </div>
  );
};

export default LabTab;
