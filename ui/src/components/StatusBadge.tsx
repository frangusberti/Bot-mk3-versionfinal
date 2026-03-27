import React from 'react';

interface StatusBadgeProps {
  label: string;
  status: 'healthy' | 'warning' | 'error' | 'inactive';
  icon?: React.ReactNode;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ label, status, icon }) => {
  const styles = {
    healthy: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
    warning: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
    error: 'bg-rose-500/10 text-rose-500 border-rose-500/20',
    inactive: 'bg-slate-800 text-slate-400 border-slate-700',
  };

  const dotColors = {
    healthy: 'bg-emerald-500',
    warning: 'bg-amber-500',
    error: 'bg-rose-500',
    inactive: 'bg-slate-500',
  };

  return (
    <div className={`flex items-center space-x-2 px-3 py-1.5 rounded-full border text-[11px] font-bold tracking-wider uppercase ${styles[status]}`}>
      <div className={`w-1.5 h-1.5 rounded-full ${dotColors[status]} ${status === 'healthy' ? 'animate-pulse' : ''}`} />
      {icon && <span className="opacity-70">{icon}</span>}
      <span>{label}</span>
    </div>
  );
};

export default StatusBadge;
