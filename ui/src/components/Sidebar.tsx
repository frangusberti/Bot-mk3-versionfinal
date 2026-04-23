import React from 'react';
import { LayoutDashboard, Layers, TrendingUp, History, Settings } from 'lucide-react';
import type { Tab } from '../App';

const NAV: { id: Tab; icon: React.ElementType; label: string }[] = [
  { id: 'dashboard',  icon: LayoutDashboard, label: 'Panel' },
  { id: 'posiciones', icon: Layers,          label: 'Posiciones' },
  { id: 'mercado',    icon: TrendingUp,       label: 'Mercado' },
  { id: 'historial',  icon: History,          label: 'Historial' },
  { id: 'sistema',    icon: Settings,         label: 'Sistema' },
];

interface SidebarProps {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeTab, onTabChange }) => {
  return (
    <aside className="w-52 bg-gray-950 border-r border-gray-800 flex flex-col shrink-0">
      {/* Logo */}
      <div className="px-4 py-4 flex items-center gap-3 border-b border-gray-800">
        <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-lg shadow-blue-900/40">
          <span className="font-black text-sm text-white">B3</span>
        </div>
        <span className="font-bold text-white text-base tracking-tight">BOT MK3</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-0.5">
        {NAV.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => onTabChange(id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all duration-150 ${
              activeTab === id
                ? 'bg-blue-600/15 text-blue-400 border border-blue-500/25'
                : 'text-gray-500 hover:bg-gray-900 hover:text-gray-200 border border-transparent'
            }`}
          >
            <Icon size={17} strokeWidth={activeTab === id ? 2.5 : 1.8} />
            <span className="text-sm font-medium">{label}</span>
          </button>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-gray-800/60">
        <p className="text-[10px] text-gray-700 text-center uppercase tracking-widest font-semibold">
          Paper Mode · v3.0
        </p>
      </div>
    </aside>
  );
};

export default Sidebar;
