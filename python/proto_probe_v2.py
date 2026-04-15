
import bot_pb2
r = bot_pb2.StepRequest()
print(f"Campos en StepRequest: {r.DESCRIPTOR.fields_by_name.keys()}")
try:
    r.action = bot_pb2.Action(type=1)
    print("ASIGNACIÓN 'action' FUNCIONA")
except AttributeError:
    print("ASIGNACIÓN 'action' ERROR")
except TypeError:
    print("ASIGNACIÓN 'action' ERROR (Tipo inválido)")
    
print(f"Valor de action.type en el request: {r.action.type}")
