import { useState } from "react";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import SummaryTab from "./components/SummaryTab";
import OperationsTab from "./components/OperationsTab";
import MarketTab from "./components/MarketTab";
import HistoryTab from "./components/HistoryTab";
import SettingsTab from "./components/SettingsTab";
import LabTab from "./components/LabTab";
import "./App.css";

type Tab = "summary" | "market" | "operations" | "history" | "config" | "lab";

function App() {
  const [activeTab, setActiveTab] = useState<Tab>("summary");

  const renderContent = () => {
    switch (activeTab) {
      case "summary":
        return <SummaryTab />;
      case "operations":
        return <OperationsTab />;
      case "market":
        return <MarketTab />;
      case "history":
        return <HistoryTab />;
      case "config":
        return <SettingsTab />;
      case "lab":
        return <LabTab />;
      default:
        return (
          <div className="p-12 text-center text-slate-500 italic">
            Panel en desarrollo. Próximamente.
          </div>
        );
    }
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 font-sans selection:bg-blue-500/30">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header activeTab={activeTab} />
        
        <div className="flex-1 overflow-y-auto p-8 custom-scrollbar">
          <div className="max-w-7xl mx-auto">
            {renderContent()}
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
