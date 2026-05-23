## Smart-Fan
## 22.05.2025 [plsatin]



import time
import board
from digitalio import DigitalInOut, Direction, Pull

from adafruit_onewire.bus import OneWireBus
from adafruit_ds18x20 import DS18X20

import pwmio
import supervisor
import digitalio

# print('Start ...')

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT

# Set up PWM on GP0 with a standard 25kHz frequency for fans
fan_pin = pwmio.PWMOut(board.GP0, frequency=25000, duty_cycle=0)

# Для будующего функционала "Тахометр"
tach_pin = digitalio.DigitalInOut(board.GP1)
tach_pin.direction = digitalio.Direction.INPUT
tach_pin.pull = digitalio.Pull.UP



# Initialize one-wire bus on board pin GP22.
ow_bus = OneWireBus(board.GP22)

# Переменная для установки PWM на вентиляторе
pwm_set_value = 0


def on_startup():
    print("Запуск системы... Вентилятор работает на 100% скорости в течение 3 секунд.")
    # Set the fan speed (0 to 65535)
    # Example: 50% speed is 32767
    fan_pin.duty_cycle = 65535

    for n in range(0, 3):
        led.value = True   # Turn LED on
        time.sleep(0.5)    # Wait half a second
        led.value = False  # Turn LED off
        time.sleep(0.5)    # Wait half a second

    # print("Fan running at 0% speed")
    fan_pin.duty_cycle = 0
    time.sleep(2)


## Запускае вентилятор при старте
on_startup()


# Scan for sensors and grab the first one found.
ds18 = DS18X20(ow_bus, ow_bus.scan()[0])


print(f'Ожидаем команду ... ')

# Main loop to print the temperature every second.
while True:
    # print(f'Ожидаем команду ... ')
    # time.sleep(2)
    if supervisor.runtime.serial_bytes_available:
        value = input().strip()
        # Sometimes Windows sends an extra (or missing) newline - ignore them
        if value == "":
            continue
        if value == "temperature":
            print(str(ds18.temperature))
            continue

        if value == "pwm":
            print(str(pwm_set_value))
            continue

        if value == "temperature,pwm":
            print(f"{str(ds18.temperature)};{str(pwm_set_value)}")
            continue


        print("Команда [{}] принята.".format(value))
        # print("Command recivied")

        try:
            if int(value) == 0:
                fan_pin.duty_cycle = int(value)
                led.value = False
                pwm_set_value = int(value)
            else:
                fan_pin.duty_cycle = int(value)
                led.value = True
                pwm_set_value = int(value)
            
        except Exception as exc:
            print(f"\n[!] Возникли ошибки: {exc}")
