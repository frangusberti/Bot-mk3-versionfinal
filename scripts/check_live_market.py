# scripts/check_live_market.py
import os
import sys
import grpc

# Add bot_ml path for protos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python', 'bot_ml'))
import bot_pb2
import bot_pb2_grpc

def check_market():
    server_addr = "localhost:50051"
    print(f"Subscribing to MarketSnapshot for BTCUSDT...")
    
    try:
        channel = grpc.insecure_channel(server_addr)
        stub = bot_pb2_grpc.MarketServiceStub(channel)
        
        request = bot_pb2.MarketSubscription(symbol="BTCUSDT")
        snapshots = stub.SubscribeMarketSnapshot(request)
        
        count = 0
        for snap in snapshots:
            print(f"\n--- Snapshot {count+1} ---")
            print(f"Symbol:      {snap.symbol}")
            print(f"Mid Price:   {snap.mid_price:.2f}")
            print(f"Spread %:    {snap.spread_percent:.4f}%")
            print(f"InSync:      {snap.in_sync}")
            print(f"EPS:         {snap.events_per_sec:.1f}")
            print(f"File Size:   {snap.file_size_bytes / (1024*1024):.2f} MB")
            
            count += 1
            if count >= 3:
                break
                
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    check_market()
