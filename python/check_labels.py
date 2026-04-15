
import os
import sys
import bot_pb2
import bot_pb2_grpc
from bot_ml.grpc_env import GrpcTradingEnv
import grpc

def check_labels():
    env = GrpcTradingEnv(server_addr="localhost:50051", symbol="BTCUSDT")
    info = env.stub.GetEnvInfo(bot_pb2.EnvInfoRequest())
    print("Labels:", list(info.obs_labels))
    obs, _ = env.reset()
    print("Obs length:", len(obs))
    env.close()

if __name__ == "__main__":
    check_labels()
