import board
import pwmio
import time
import digitalio
# import usb_cdc
import supervisor


led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT


# Set up PWM on GP0 with a standard 25kHz frequency for fans
fan_pin = pwmio.PWMOut(board.GP0, frequency=25000, duty_cycle=0)


print("Start system.... Fan running at 100% speed")
# Set the fan speed (0 to 65535)
# Example: 50% speed is 32767
fan_pin.duty_cycle = 65535

for n in range(0, 3):
    led.value = True   # Turn LED on
    time.sleep(0.5)    # Wait half a second
    led.value = False  # Turn LED off
    time.sleep(0.5)    # Wait half a second

print("Fan running at 0% speed")
fan_pin.duty_cycle = 0
time.sleep(2)


# Use the data serial port (serial[1])
# Note: serial[0] is typically reserved for the REPL/Console

# serial = usb_cdc.data

while True:
    # print(f'Ожидаем команду ... ')
    # time.sleep(2)
    if supervisor.runtime.serial_bytes_available:
        value = input().strip()
        # Sometimes Windows sends an extra (or missing) newline - ignore them
        if value == "":
            continue
        print("RX: {}".format(value))
        # print("Command recivied")

        try:
            if int(value) == 0:
                fan_pin.duty_cycle = int(value)
                led.value = False
            else:
                fan_pin.duty_cycle = int(value)
                led.value = True
        except Exception as exc:
            print(f"\n[!] Возникли ошибки: {exc}")
