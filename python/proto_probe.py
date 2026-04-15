
import bot_pb2
a = bot_pb2.Action()
print(f"Campos disponibles en Action: {a.DESCRIPTOR.fields_by_name.keys()}")
try:
    a.type = 1
    print("CAMPO 'type' FUNCIONA")
except AttributeError:
    print("CAMPO 'type' ERROR")
try:
    a.type_ = 1
    print("CAMPO 'type_' FUNCIONA")
except AttributeError:
    print("CAMPO 'type_' ERROR")
try:
    a.r_type = 1
    print("CAMPO 'r_type' FUNCIONA")
except AttributeError:
    print("CAMPO 'r_type' ERROR")
