import { useState } from "react";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import DashboardTab from "./components/DashboardTab";
import OperationsTab from "./components/OperationsTab";
import MarketTab from "./components/MarketTab";
import HistoryTab from "./components/HistoryTab";
import SistemaTab from "./components/SistemaTab";
import "./App.css";

export type Tab = "dashboard" | "posiciones" | "mercado" | "historial" | "sistema";

function App() {
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");

  const renderContent = () => {
    switch (activeTab) {
      case "dashboard":   return <DashboardTab />;
      case "posiciones":  return <OperationsTab />;
      case "mercado":     return <MarketTab />;
      case "historial":   return <HistoryTab />;
      case "sistema":     return <SistemaTab />;
    }
  };

  return (
    <div className="flex h-screen bg-gray-950 text-gray-200 font-sans selection:bg-blue-500/30">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header activeTab={activeTab} />
        <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
          <div className="max-w-5xl mx-auto">
            {renderContent()}
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
