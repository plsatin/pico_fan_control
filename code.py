# SPDX-FileCopyrightText: 2020 ladyada for Adafruit Industries
# SPDX-License-Identifier: MIT

# Simple demo of printing the temperature from the first found DS18x20 sensor every second.
# Author: Tony DiCola

# A 4.7Kohm pullup between DATA and POWER is REQUIRED!

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


# Initialize one-wire bus on board pin GP22.
ow_bus = OneWireBus(board.GP22)

# Реле на GP15
relay_1 = DigitalInOut(board.GP15)
relay_1.direction = Direction.OUTPUT




def on_startup():
    # print("Start system.... Fan running at 100% speed")
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



# Main loop to print the temperature every second.
while True:
    if ds18.temperature > 26.0:
        # print(f"Температура ({ds18.temperature:0.3f}C) выше нормы!")
        print(str(ds18.temperature))
        # relay_1.value = True

        fan_pin.duty_cycle = 65535
        led.value = True


    else:
        # print(f"Температура: {ds18.temperature:0.3f}C")
        print(str(ds18.temperature))
        # relay_1.value = False

        fan_pin.duty_cycle = 0
        led.value = False


    time.sleep(5.0)
