import React from 'react';
import StatusBadge from './StatusBadge';
import { ShieldCheck, Zap, Activity } from 'lucide-react';

interface HeaderProps {
  activeTab: string;
}

const Header: React.FC<HeaderProps> = ({ activeTab }) => {
  const getTitle = () => {
    switch(activeTab) {
      case 'summary': return 'Resumen de Misión';
      case 'market': return 'Análisis de Mercado';
      case 'operations': return 'Panel de Operaciones';
      case 'history': return 'Historial de Trades';
      case 'config': return 'Configuración del Bot';
      case 'lab': return 'Laboratorio Avanzado';
      default: return 'Bot MK3';
    }
  };

  return (
    <header className="h-20 bg-slate-900/50 backdrop-blur-md border-b border-slate-800 flex items-center justify-between px-8 sticky top-0 z-10">
      <div className="flex flex-col">
        <h1 className="text-xl font-bold text-white tracking-tight">{getTitle()}</h1>
        <div className="flex items-center text-[10px] text-slate-500 font-bold tracking-widest uppercase mt-0.5">
          <span>SISTEMA</span>
          <span className="mx-2 opacity-50">/</span>
          <span className="text-blue-500">{activeTab}</span>
        </div>
      </div>

      <div className="flex items-center space-x-4">
        <StatusBadge 
          label="MODO VISOR" 
          status="healthy" 
          icon={<ShieldCheck size={12} className="text-blue-400" />} 
        />
        <StatusBadge 
          label="CAPTURA ACTIVA" 
          status="healthy" 
          icon={<Activity size={12} />} 
        />
        <StatusBadge 
          label="PAPER TRADE" 
          status="warning" 
          icon={<Zap size={12} />} 
        />
        
        <div className="h-8 w-px bg-slate-800 mx-2" />
        
        <div className="flex items-center space-x-3 bg-slate-800/50 rounded-lg px-3 py-1.5 border border-slate-700">
           <div className="flex flex-col items-end">
             <span className="text-[10px] font-bold text-slate-500 uppercase">Equity</span>
             <span className="text-sm font-mono font-bold text-emerald-400">$10,452.20</span>
           </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
