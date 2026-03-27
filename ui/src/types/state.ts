/**
 * UI State Contract V3
 * Define la separación de responsabilidades entre el estado operativo, 
 * de mercado y técnico.
 */

export type SystemMode = 'LIVE' | 'PAPER' | 'REPLAY';
export type AppTab = 'summary' | 'market' | 'operations' | 'history' | 'config' | 'lab';

export interface OperationalState {
  status: 'IDLE' | 'STARTING' | 'RUNNING' | 'PAUSED' | 'ERROR' | 'SHUTTING_DOWN';
  mode: SystemMode;
  symbol: string;
  pnlDay: number;
  tradeCount: number;
  drawdown: number;
  equity: number;
  activeModel: string;
}

export interface MarketState {
  price: number;
  change24h: number;
  spread: number;
  liquidity: 'BAJA' | 'MEDIA' | 'ALTA';
  volatility: 'BAJA' | 'MEDIA' | 'ALTA';
  pressure: 'COMPRADORA' | 'NEUTRA' | 'VENDEDORA';
  executionCondition: 'FAVORABLE' | 'NEUTRAL' | 'DESFAVORABLE';
}

export interface TechnicalState {
  eps: number;
  marketDataInSync: boolean;
  recordingActive: boolean;
  recordingId?: string;
  latencyMs: number;
  lastGapDetected?: string;
  modelReady: boolean;
}

export interface UIState {
  operational: OperationalState;
  market: MarketState;
  technical: TechnicalState;
}
