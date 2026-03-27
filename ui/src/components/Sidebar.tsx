import React from 'react';
import { 
  LayoutDashboard, 
  BarChart3, 
  Zap, 
  History, 
  Settings, 
  FlaskConical,
  LogOut
} from 'lucide-react';

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: any) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab }) => {
  const navItems = [
    { id: 'summary', icon: LayoutDashboard, label: 'Resumen' },
    { id: 'market', icon: BarChart3, label: 'Mercado' },
    { id: 'operations', icon: Zap, label: 'Operaciones' },
    { id: 'history', icon: History, label: 'Historial' },
  ];

  return (
    <aside className="w-20 lg:w-64 bg-slate-950 border-r border-slate-800 flex flex-col h-full transition-all duration-300">
      <div className="p-6 flex items-center space-x-3">
        <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-900/20">
          <span className="font-bold text-xl text-white">B3</span>
        </div>
        <span className="hidden lg:block font-bold text-xl tracking-tight text-white px-2">BOT MK3</span>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-2 mb-4 hidden lg:block">
          MODO OPERADOR
        </div>
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={`w-full flex items-center p-3 rounded-lg transition-all group ${
              activeTab === item.id 
                ? 'bg-blue-600/10 text-blue-400 border border-blue-500/20' 
                : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
            }`}
          >
            <item.icon size={22} className={activeTab === item.id ? 'text-blue-400' : 'group-hover:text-slate-200'} />
            <span className="ml-3 hidden lg:block font-medium">{item.label}</span>
          </button>
        ))}

        <div className="h-px bg-slate-800 my-6 mx-2" />

        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-2 mb-4 hidden lg:block">
          AVANZADO
        </div>
        <button
          onClick={() => setActiveTab('config')}
          className={`w-full flex items-center p-3 rounded-lg transition-all group ${
            activeTab === 'config' 
              ? 'bg-slate-800 text-white' 
              : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
          }`}
        >
          <Settings size={22} />
          <span className="ml-3 hidden lg:block font-medium">Ajustes</span>
        </button>
        <button
          onClick={() => setActiveTab('lab')}
          className={`w-full flex items-center p-3 rounded-lg transition-all group ${
            activeTab === 'lab' 
              ? 'bg-purple-600/10 text-purple-400 border border-purple-500/20' 
              : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
          }`}
        >
          <FlaskConical size={22} />
          <span className="ml-3 hidden lg:block font-medium">Laboratorio</span>
        </button>
      </nav>

      <div className="p-4 border-t border-slate-900">
        <button className="w-full flex items-center p-3 text-slate-500 hover:text-red-400 hover:bg-red-400/5 rounded-lg transition-colors group">
          <LogOut size={22} />
          <span className="ml-3 hidden lg:block font-medium">Salir</span>
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
