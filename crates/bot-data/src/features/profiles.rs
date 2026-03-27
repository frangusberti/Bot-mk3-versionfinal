use serde::{Serialize, Deserialize};
use std::collections::HashSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FeatureProfile {
    Simple,
    Rich,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FeatureSetVersion(pub String);

impl Default for FeatureSetVersion {
    fn default() -> Self {
        FeatureSetVersion("v1".to_string())
    }
}

impl FeatureProfile {
    pub fn required_streams(&self) -> HashSet<&'static str> {
        let mut streams = HashSet::new();
        match self {
            FeatureProfile::Simple => {
                // Needs at least one price source. Ideally "trade" or "aggTrade".
                // "ticker" or "bookTicker" also works. 
                // We enforce checking for at least one compatible stream in the engine.
                // For strictness, let's say we need 'trade'.
                streams.insert("trade"); 
            },
            FeatureProfile::Rich => {
                streams.insert("trade");
                streams.insert("depthUpdate"); // or "depth"
                streams.insert("bookTicker");  // For best bid/ask
                // streams.insert("markPrice"); // Optional really, but let's make it strict if we want mark_distance
            }
        }
        streams
    }

    pub fn is_compatible(&self, available_streams: &HashSet<String>) -> bool {
        let required = self.required_streams();
        for req in required {
            if !available_streams.contains(req) {
                 // Relax for 'trade' if 'aggTrade' present
                 if req == "trade" && available_streams.contains("aggTrade") { continue; }
                 return false;
            }
        }
        true
    }
    
    pub fn description(&self) -> &'static str {
        match self {
            FeatureProfile::Simple => "Basic price-action features (returns, vol, TA)",
            FeatureProfile::Rich => "Full microstructure features (spread, imbalance, flow)",
        }
    }

}

impl std::str::FromStr for FeatureProfile {
    type Err = ();

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "rich" => Ok(FeatureProfile::Rich),
            _ => Ok(FeatureProfile::Simple),
        }
    }
}
