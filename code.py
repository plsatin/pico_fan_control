import board
import pwmio
import time
import digitalio


led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT


# Set up PWM on GP0 with a standard 25kHz frequency for fans
fan_pin = pwmio.PWMOut(board.GP0, frequency=25000, duty_cycle=0)


print("Start system.... Fan running at 100% speed")
# Set the fan speed (0 to 65535)
# Example: 50% speed is 32767
fan_pin.duty_cycle = 65535

for n in range(0, 10):
    led.value = True   # Turn LED on
    time.sleep(0.5)    # Wait half a second
    led.value = False  # Turn LED off
    time.sleep(0.5)    # Wait half a second

print("Fan running at 50% speed")
fan_pin.duty_cycle = 32767
time.sleep(5)

while True:
    
    print("Fan running at 0% speed")
    led.value = False
    # Turn off
    fan_pin.duty_cycle = 0
    time.sleep(15)

    print("Fan running at 100% speed")
    led.value = True
    # Full speed
    fan_pin.duty_cycle = 65535
    time.sleep(15)
