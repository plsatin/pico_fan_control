import time
import serial
import sys


ser = serial.Serial('COM5', 115200, timeout=0.01)  # open serial port

arg_value = str(sys.argv[1]) + '\n\r'
# command = arg_value.to_bytes(2, byteorder='big')
command = arg_value.encode() 

# command = b'0\n\r'
# command = b'temperature\n\r'
# command = b'65535\n\r'

# print(f"Sending Command: [{command}]")
ser.write(command)     # write a string

ended = False
reply = b''

for _ in range(len(command)):
    a = ser.read() # Read the loopback chars and ignore

while True:
    a = ser.read()
    if a== b'\r':
        break
    else:
        reply += a

    time.sleep(0.01)

ser.close()

string_data = reply.decode()
print(f"{string_data}")