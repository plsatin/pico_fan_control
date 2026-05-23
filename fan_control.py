# Управление умным вентилятором
import time
import serial
import sys

try:
    ser = serial.Serial('COM5', 115200, timeout=0.01)  # open serial port

    arg_value = str(sys.argv[1]) + '\n\r'
    # command = arg_value.to_bytes(2, byteorder='big')
    command = arg_value.encode() 

    # print(f"Sending Command: [{command}]")
    ser.write(command)     # write a string

    ended = False
    reply = b''

    for _ in range(len(command)):
        a = ser.readline() # Read the loopback chars and ignore

    while True:
        a = ser.read()
        if a== b'\r':
            break
        else:
            reply += a

        time.sleep(0.01)

    ser.close()

    string_data = reply.decode('utf-8', errors='ignore').strip()
    print(f"{string_data}")

except Exception as exc:
    print(f"\n[!] Возникли ошибки: {exc}")
    ser.close()
