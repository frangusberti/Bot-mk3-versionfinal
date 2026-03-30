import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.proto import bot_pb2

info = bot_pb2.StepInfo()
print("Fields in StepInfo:", [f.name for f in info.DESCRIPTOR.fields])
print("Is 'trades_executed' present?", any(f.name == 'trades_executed' for f in info.DESCRIPTOR.fields))
